"""ADA-AGI üst katmanı: yerel model taraması, görev türüne göre motor seçimi ve motorlar arası devir."""

from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace as NS

import httpx
import pytest
from conftest import gemini_response, text
from google.genai import errors, types

from ada import agent, config, engines, router, store
from ada.providers import selfhost
from ada.tools import computer


def chunk(content=None, tcs=None, finish=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tcs, reasoning=None), finish_reason=finish)])


def tc(i, id=None, name=None, args=None):
    return NS(index=i, id=id, function=NS(name=name, arguments=args))


GEMINI = engines.PROVIDERS["gemini"]


def flash():
    return engines.cloud_engine(GEMINI, "gemini-3.8-flash")


def pro():
    return engines.cloud_engine(GEMINI, "gemini-3.5-pro")


def qwen_vl():
    return engines.local_engine("ollama", "http://127.0.0.1:11434/v1", "qwen3-vl-ada:latest",
                                caps=["completion", "vision", "tools"], params_b=31.1, family="qwen3vlmoe")


def coder():
    return engines.local_engine("ollama", "http://127.0.0.1:11434/v1", "qwen3-coder:30b",
                                caps=["completion", "tools"], params_b=30.5, family="qwen3moe")


def install(*items, routes=None):
    state = engines.load_state()
    state["engines"] = {e.id: asdict(e) for e in items}
    state["local_status"] = {"ollama": {"state": "running"}, "lmstudio": {"state": "running"}}
    state["routes"] = routes or {}
    engines.save_state(state)


@pytest.fixture(autouse=True)
def adaagi(monkeypatch):
    monkeypatch.setattr(config, "PROVIDER", "adaagi")
    engines.AI_FILE.unlink(missing_ok=True)
    router.STATS_FILE.unlink(missing_ok=True)
    router._COOLDOWN.clear()
    monkeypatch.setattr(engines, "ollama_up", _true)
    yield
    engines.AI_FILE.unlink(missing_ok=True)
    router.STATS_FILE.unlink(missing_ok=True)
    router._COOLDOWN.clear()


async def _true(*a, **k):
    return True


@pytest.fixture
def fake_openai(monkeypatch):
    def install_turns(turns):
        it = iter(turns)
        requests = []

        class Comp:
            async def create(self, **kw):
                requests.append(kw)
                seq = next(it)
                if isinstance(seq, Exception):
                    raise seq

                async def gen():
                    for c in seq:
                        yield c
                return gen()

        monkeypatch.setattr(selfhost, "get_client", lambda ep=None: NS(chat=NS(completions=Comp())))
        return requests

    return install_turns


async def run(conv, prompt):
    return [e async for e in agent.run_turn(conv, agent.build_user_content(prompt, []), prompt)]


# ---------------------------------------------------------------- sınıflandırma ve seçim

def test_quota_wait_is_read_from_provider_message():
    msg = "429 Quota exceeded for metric: free_tier_requests, limit: 20, model: gemini-3.8-flash\nPlease retry in 13h40m16s"
    reason, wait = router._quota(msg)
    assert "günlük kota" in reason and 13 * 3600 < wait < 14 * 3600
    assert router._quota("free_tier_input_token_count, limit: 0, model: gemini-3.1-pro")[1] == 12 * 3600
    assert router._quota("Please retry in 20.5s")[1] == 15 * 60  # kısa beklemede yine de en az 15 dk


def test_cooldowns_survive_restart():
    router.cool("gemini:x", 3600, "kota")
    router._COOLDOWN.clear()
    router._cooldowns_loaded = False
    assert router._cooling("gemini:x")


def test_classify_kinds():
    assert router.classify("selam nasılsın") == "sohbet"
    assert router.classify("Bu python betiğindeki hatayı ayıkla") == "kod"
    assert router.classify("Excel'deki satışları rapor yap") == "ofis"
    assert router.classify("Chrome'u aç ve butona tıkla") == "ekran"
    assert router.classify("Bu notları gizli tut, buluta gönderme") == "gizli"
    assert router.classify("Gizli klasörleri göster") != "gizli"
    assert router.classify("Şirket için 3 yıllık strateji planla") == "derin"
    assert router.classify("Dolar kuru hakkında güncel haberleri araştır") == "arastirma"


def test_plan_uses_specialists_and_keeps_private_local():
    install(flash(), pro(), qwen_vl(), coder())
    assert router.plan("sohbet")[0].model == "gemini-3.8-flash"  # hızlı ve ücretsiz
    assert router.plan("derin")[0].model == "gemini-3.5-pro"     # en güçlü akıl yürütme
    secret = router.plan("gizli")
    assert secret and all(e.local for e in secret)
    # Ekran işi görsel okuyamayan motora verilmez (kod modeli görsel okumaz)
    assert router.plan("ekran")[0].vision


def test_pinned_route_cooldown_and_disabled_engines():
    install(flash(), pro(), qwen_vl(), routes={"sohbet": qwen_vl().id})
    assert router.plan("sohbet")[0].id == qwen_vl().id
    engines.set_enabled(qwen_vl().id, False)
    assert qwen_vl().id not in [e.id for e in router.plan("sohbet")]
    router.cool(flash().id, 600, "kota")
    assert router.plan("sohbet")[0].id == pro().id


def test_cloud_engines_need_key(monkeypatch):
    install(flash(), qwen_vl())
    monkeypatch.delenv("GEMINI_API_KEY")
    assert [e.id for e in router.plan("sohbet")] == [qwen_vl().id]


def test_local_core_toolset_keeps_screen_only_for_screen_work():
    tools = agent.TOOLS
    core = router._tools_for(qwen_vl(), "kod", tools)
    assert {t.get("name") for t in core} <= agent.CORE_TOOLS | {None}
    assert not any(t.get("type") == "computer_toolset_20260801" for t in core)
    screen = router._tools_for(qwen_vl(), "ekran", tools)
    assert any(t.get("type") == "computer_toolset_20260801" for t in screen)
    assert not any(t.get("type") == "computer_toolset_20260801" for t in router._tools_for(coder(), "ekran", tools))
    assert router._tools_for(flash(), "kod", tools) == tools


# ---------------------------------------------------------------- çalıştırma ve devir

@pytest.mark.asyncio
async def test_quota_hands_over_to_local_engine_mid_turn(fake_gemini, fake_openai):
    install(flash(), qwen_vl())
    calls = iter([
        gemini_response(types.Part(function_call=types.FunctionCall(name="write_text_file", args={"filename": "a.md", "content": "gemini"}))),
        errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}),
    ])
    fake_gemini(lambda c, cfg: next(calls))
    requests = fake_openai([[chunk("Yerel model bitirdi."), chunk(finish="stop")]])
    conv = store.create()
    events = await run(conv, "selam, a.md yaz")

    labels = [e.get("label") for e in events]
    assert "ADA-AGI yönlendirdi" in labels and "Motor değişti" in labels
    assert (config.OUTPUTS_DIR / "a.md").read_text() == "gemini"
    assert conv.ui[-1]["text"] == "Yerel model bitirdi."
    assert requests[0]["model"] == "qwen3-vl-ada:latest"
    # Yerel model Gemini'nin yaptığı adımı (araç sonucu) görür; geçmiş ortak biçimde kalır
    assert [m["role"] for m in requests[0]["messages"]][-2:] == ["assistant", "tool"]
    assert [m["role"] for m in conv.messages] == ["user", "model", "user", "model"]
    assert conv.provider == "adaagi" and conv.engine == qwen_vl().id
    assert router._cooling(flash().id)
    stats = router.stats()
    assert stats["engines"][qwen_vl().id]["ok"] == 1 and stats["engines"][flash().id]["fail"] == 1
    assert stats["decisions"][0]["outcome"].startswith("tamam")


@pytest.mark.asyncio
async def test_partial_text_of_failed_engine_is_dropped(monkeypatch):
    import openai

    # Bağlantı hatası tüm sunucuyu etkiler: devralan motor başka bir sunucudan (LM Studio) gelir
    install(qwen_vl(), engines.local_engine("lmstudio", "http://127.0.0.1:1234/v1", "agi-sap-brain",
                                            caps=["completion", "tools"], params_b=30.5, family="qwen3moe"))

    class Broken:
        def __init__(self):
            self.n = 0

        async def create(self, **kw):
            self.n += 1
            if self.n == 1:
                async def gen():
                    yield chunk("yarım cüm")
                    raise openai.APIConnectionError(request=httpx.Request("POST", "http://x"))
                return gen()

            async def ok():
                yield chunk("Tam yanıt.")
                yield chunk(finish="stop")
            return ok()

    comp = Broken()
    monkeypatch.setattr(selfhost, "get_client", lambda ep=None: NS(chat=NS(completions=comp)))
    conv = store.create()
    events = await run(conv, "selam")
    assert conv.ui[-1]["text"] == "Tam yanıt."
    assert any(e["type"] == "text_set" and e["text"] == "" for e in events)


@pytest.mark.asyncio
async def test_bad_key_skips_rest_of_same_family(fake_gemini, fake_openai):
    install(flash(), engines.cloud_engine(GEMINI, "gemini-3.7-flash"), qwen_vl())
    fake = fake_gemini(lambda c, cfg: errors.ClientError(400, {"error": {"code": 400, "message": "API key not valid", "status": "INVALID_ARGUMENT"}}))
    fake_openai([[chunk("yerel yanıt<tool_call>"), chunk(finish="stop")]])
    conv = store.create()
    await run(conv, "selam")
    assert len(fake.calls) == 1  # 3.7-flash aynı anahtarla boşuna denenmedi
    assert conv.ui[-1]["text"] == "yerel yanıt"  # sızan etiket temizlendi
    assert router._cooling("gemini:gemini-3.7-flash")


def test_chain_keeps_another_family_as_last_resort():
    many = [engines.cloud_engine(GEMINI, f"gemini-3.{i}-flash") for i in range(1, 8)]
    install(*many, coder())
    chain = router.plan("sohbet")
    assert len(chain) == 5 and chain[-1].id == coder().id and chain[0].model == "gemini-3.7-flash"


@pytest.mark.asyncio
async def test_all_engines_failing_reports_each(fake_gemini, fake_openai):
    import openai

    install(flash(), qwen_vl())
    fake_gemini(lambda c, cfg: gemini_response(finish="SAFETY"))
    fake_openai([openai.APIConnectionError(request=httpx.Request("POST", "http://x"))])
    conv = store.create()
    events = await run(conv, "soru")
    msg = next(e["message"] for e in events if e["type"] == "error")
    assert "gemini-3.8-flash" in msg and "qwen3-vl-ada" in msg


@pytest.mark.asyncio
async def test_no_engine_message():
    install()
    conv = store.create()
    events = await run(conv, "soru")
    assert "Kullanılabilir yapay zekâ motoru yok" in next(e["message"] for e in events if e["type"] == "error")


@pytest.mark.asyncio
async def test_engine_coordinates_follow_engine(monkeypatch):
    seen = []
    install(engines.local_engine("ollama", "http://127.0.0.1:11434/v1", "qwen2.5vl:7b", caps=["completion", "vision", "tools"], params_b=7))
    monkeypatch.setenv("ADA_SCREEN_COORDS", "norm1000")

    async def probe(*a, **k):
        seen.append(computer.normalized())
        if False:
            yield {}

    monkeypatch.setattr(selfhost, "selfhost_loop", probe)
    await run(store.create(), "selam")
    assert seen == [False]  # Qwen2.5-VL piksel ister; ADA_SCREEN_COORDS ayarı bunu ezmez


@pytest.mark.asyncio
async def test_old_gemini_task_continues_under_adaagi(fake_gemini):
    install(flash())
    fake = fake_gemini(lambda c, cfg: gemini_response(text("devam")))
    conv = store.create()
    conv.provider = "gemini"
    conv.messages = [types.Content(role="user", parts=[types.Part(text="önce")]).model_dump(mode="json", exclude_none=True),
                     types.Content(role="model", parts=[types.Part(text="tamam")]).model_dump(mode="json", exclude_none=True)]
    events = await run(conv, "sonra")
    assert not any(e["type"] == "error" for e in events)
    assert conv.provider == "adaagi" and len(fake.calls[0]["contents"]) == 3


@pytest.mark.asyncio
async def test_subagent_role_picks_its_kind(fake_gemini, monkeypatch):
    install(flash(), pro())
    fake = fake_gemini(lambda c, cfg: gemini_response(text("KARAR: ONAY")))
    from ada.tools.agents import delegate_tasks

    await delegate_tasks([{"role": "denetci", "task": "kontrol et"}])
    assert fake.calls[0]["model"] == "gemini-3.5-pro"


# ---------------------------------------------------------------- tarama

@pytest.mark.asyncio
async def test_scan_local_reads_ollama_capabilities(monkeypatch):
    def handler(req: httpx.Request):
        if req.url.port != 11434:
            raise httpx.ConnectError("yok", request=req)
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": "qwen3-vl-ada:latest", "details": {"parameter_size": "31.1B", "family": "qwen3vlmoe"}},
                {"name": "qwen3-embedding:latest", "details": {"parameter_size": "7.6B"}},
                {"name": "tiny:1b", "details": {"parameter_size": "1B"}},
            ]})
        if req.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "qwen3-vl-ada:latest"}]})
        if req.url.path == "/api/show":
            import json as _j

            name = _j.loads(req.content)["model"]
            caps = {"qwen3-vl-ada:latest": ["completion", "vision", "tools"], "qwen3-embedding:latest": ["embedding"],
                    "tiny:1b": ["completion"]}[name]
            return httpx.Response(200, json={"capabilities": caps, "parameters": "num_ctx 16384"})
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(engines.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    statuses = await engines.scan_local()
    assert statuses["ollama"]["state"] == "running"
    found = {e.model: e for e in engines.all_engines()}
    assert set(found) == {"qwen3-vl-ada:latest", "tiny:1b"}  # gömme modeli alınmaz
    vl = found["qwen3-vl-ada:latest"]
    assert vl.vision and vl.tools and vl.loaded and vl.context == 16384 and vl.coords == "norm1000" and vl.enabled
    assert not found["tiny:1b"].enabled  # araç çağıramayan model varsayılan kapalı


@pytest.mark.asyncio
async def test_cloud_key_lists_models_and_enables_best(monkeypatch):
    from fastapi.testclient import TestClient

    from ada import server

    async def fake_list(pid):
        p = engines.PROVIDERS[pid]
        return [engines.cloud_engine(p, m) for m in ("gpt-5.5", "gpt-5.5-mini", "gpt-5.5-nano", "gpt-4o", "text-old")]

    monkeypatch.setattr(engines, "list_cloud", fake_list)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(server.app) as client:
        r = client.post("/api/ai/key", json={"provider": "openai", "key": "sk-test-123456789"},
                        headers={"Origin": "http://testserver"})
        assert r.status_code == 200, r.text
        data = r.json()
    try:
        openai_rows = [e for e in data["engines"] if e["provider"] == "openai"]
        assert len(openai_rows) == 5 and sum(e["enabled"] for e in openai_rows) == 3
        card = next(c for c in data["cloud"] if c["id"] == "openai")
        assert card["key_set"] and card["key_mask"].endswith("6789") and "sk-test" not in str(data)
        assert router.plan("derin")[0].provider == "openai"
    finally:
        import os

        os.environ.pop("OPENAI_API_KEY", None)


def test_passive_qwen_is_listed_but_never_used(monkeypatch):
    monkeypatch.setattr(config, "QWEN_ENABLED", False)
    other = engines.local_engine("ollama", "http://127.0.0.1:11434/v1", "llama3.3:70b", caps=["completion", "tools"], params_b=70)
    install(qwen_vl(), coder(), other, engines.cloud_engine(engines.PROVIDERS["qwen"], "qwen-plus"), routes={"kod": coder().id})
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    by_id = {e.id: e for e in engines.all_engines()}
    assert by_id[qwen_vl().id].passive and not by_id[qwen_vl().id].available
    assert by_id["qwen:qwen-plus"].passive and not by_id[other.id].passive
    for kind in router.KINDS:  # sabitlenmiş olsa bile hiçbir türde seçilmez
        assert [e.id for e in router.plan(kind)] == [other.id]
    monkeypatch.setattr(config, "QWEN_ENABLED", True)
    assert router.plan("kod")[0].id == coder().id


def test_passive_qwen_api_rejects_key_test_and_enable(monkeypatch):
    from fastapi.testclient import TestClient

    from ada import server

    monkeypatch.setattr(config, "QWEN_ENABLED", False)
    install(qwen_vl())
    origin = {"Origin": "http://testserver"}
    with TestClient(server.app) as client:
        data = client.get("/api/ai").json()
        assert next(c for c in data["cloud"] if c["id"] == "qwen")["passive"]
        assert data["engines"][0]["passive"] and data["preview"]["sohbet"] == []
        assert client.post("/api/ai/key", json={"provider": "qwen", "key": "sk-x"}, headers=origin).status_code == 400
        assert client.post("/api/ai/test", json={"id": qwen_vl().id}, headers=origin).status_code == 400
        assert client.post("/api/ai/engine", json={"id": qwen_vl().id, "enabled": True}, headers=origin).status_code == 400
        assert not any(f["key"].startswith("ADA_QWEN_") for f in client.get("/api/settings").json()["fields"])
