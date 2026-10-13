"""Gemini önceliği ve Qwen yedeği: Gemini turu yapamazsa Qwen kaldığı yerden sürdürür."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest
from conftest import call, gemini_response, text
from google.genai import errors, types

from ada import agent, config, store
from ada.providers import qwen_fallback, selfhost


def chunk(content=None, tcs=None, finish=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tcs, reasoning=None), finish_reason=finish)])


def tc(i, id=None, name=None, args=None):
    return NS(index=i, id=id, function=NS(name=name, arguments=args))


def quota_error():
    return errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})


@pytest.fixture
def fake_qwen(monkeypatch):
    monkeypatch.setattr(config, "QWEN_FALLBACK", True)

    def install(turns):
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

    return install


async def run(conv, prompt):
    return [e async for e in agent.run_turn(conv, agent.build_user_content(prompt, []), prompt)]


@pytest.mark.asyncio
async def test_quota_exhausted_hands_turn_to_qwen_and_gemini_stays_first(fake_gemini, fake_qwen):
    gemini_turns = iter([quota_error()] * len(config.GEMINI_MODELS) + [gemini_response(text("Gemini geri döndü"))])
    fake = fake_gemini(lambda c, cfg: next(gemini_turns))
    requests = fake_qwen([
        [chunk(tcs=[tc(0, "c1", "write_text_file", '{"filename": "q.md", "content": "qwen"}')], finish="tool_calls")],
        [chunk("Qwen bitirdi."), chunk(finish="stop")],
    ])
    conv = store.create()
    events = await run(conv, "yaz")

    assert any(e.get("label") == "Qwen devraldı" for e in events)
    assert (config.OUTPUTS_DIR / "q.md").read_text() == "qwen"
    assert conv.ui[-1]["text"] == "Qwen bitirdi."
    assert requests[0]["model"] == config.QWEN_MODEL
    # Geçmiş Gemini biçiminde kalır ve Gemini'ye geri gönderilebilir
    assert [m["role"] for m in conv.messages] == ["user", "model", "user", "model"]
    contents = [types.Content.model_validate(m) for m in conv.messages]
    fc_part = contents[1].parts[0]
    assert fc_part.function_call.name == "write_text_file" and fc_part.thought_signature
    assert contents[2].parts[0].function_response.response["result"].startswith("Kaydedildi")

    # Sonraki tur yine önce Gemini ile denenir
    await run(conv, "devam")
    assert conv.ui[-1]["text"] == "Gemini geri döndü"
    assert len(fake.calls[-1]["contents"]) == 5


@pytest.mark.asyncio
async def test_safety_block_goes_to_qwen(fake_gemini, fake_qwen):
    fake_gemini(lambda c, cfg: gemini_response(finish="SAFETY"))
    fake_qwen([[chunk("Qwen yanıtı"), chunk(finish="stop")]])
    conv = store.create()
    await run(conv, "soru")
    assert conv.ui[-1]["text"] == "Qwen yanıtı"
    assert [m["role"] for m in conv.messages] == ["user", "model"]


@pytest.mark.asyncio
async def test_local_qwen_gets_core_toolset(fake_gemini, fake_qwen, monkeypatch):
    monkeypatch.setattr(config, "QWEN_TOOLSET", "core")
    fake_gemini(lambda c, cfg: gemini_response(finish="SAFETY"))
    requests = fake_qwen([[chunk("ok"), chunk(finish="stop")]])
    await run(store.create(), "soru")
    names = {t["function"]["name"] for t in requests[0]["tools"]}
    assert names and names <= agent.CORE_TOOLS


@pytest.mark.asyncio
async def test_both_fail_reports_both(fake_gemini, fake_qwen):
    import httpx
    import openai

    fake_gemini(lambda c, cfg: gemini_response(finish="SAFETY"))
    fake_qwen([openai.APIConnectionError(request=httpx.Request("POST", "http://x"))])
    conv = store.create()
    events = await run(conv, "soru")
    msg = next(e["message"] for e in events if e["type"] == "error")
    assert "Gemini" in msg and "Qwen" in msg


@pytest.mark.asyncio
async def test_depleted_prepay_credit_goes_to_qwen(fake_gemini, fake_qwen):
    fake = fake_gemini(lambda c, cfg: errors.ClientError(402, {"error": {"code": 402, "message": "Your prepayment credits are depleted.", "status": "RESOURCE_EXHAUSTED"}}))
    fake_qwen([[chunk("Qwen yanıtı"), chunk(finish="stop")]])
    conv = store.create()
    events = await run(conv, "soru")
    assert any(e.get("detail", "").startswith("Gemini ön ödemeli kredisi bitti") for e in events)
    assert len(fake.calls) == 1  # kredi proje geneli: diğer Gemini modelleri boşuna denenmez
    assert conv.ui[-1]["text"] == "Qwen yanıtı"


@pytest.mark.asyncio
async def test_without_fallback_gemini_errors_as_before(fake_gemini):
    fake_gemini(lambda c, cfg: gemini_response(finish="SAFETY"))
    conv = store.create()
    events = await run(conv, "soru")
    assert any("güvenlik filtresi" in e.get("message", "") for e in events)


@pytest.mark.asyncio
async def test_old_selfhost_task_continues_on_gemini(fake_gemini):
    fake = fake_gemini(lambda c, cfg: gemini_response(text("devam ediyorum")))
    conv = store.create()
    conv.provider = "selfhost"
    conv.messages = [
        {"role": "user", "content": "rapor yaz"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "içerik"},
        {"role": "assistant", "content": "Okudum, yazıyorum.", "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "write_text_file", "arguments": "{}"}}]},
        # görev burada durdurulmuştu: c2'nin sonucu yok
    ]
    events = await run(conv, "devam et")
    assert not any(e["type"] == "error" for e in events)
    assert conv.provider == "gemini" and conv.ui[-1]["text"] == "devam ediyorum"
    sent = fake.calls[0]["contents"]
    assert [c.role for c in sent] == ["user", "model", "user", "model", "user"]
    last = sent[-1].parts
    assert last[0].function_response.name == "write_text_file" and last[-1].text == "devam et"


def test_history_conversion_pairs_calls_and_results():
    msgs = [
        types.Content(role="user", parts=[types.Part(text="selam")]),
        types.Content(role="model", parts=[text("düşünce", thought=True), text("Bakıyorum."), call("read_file", path="a"), call("list_directory", path=".")]),
        types.Content(role="user", parts=[
            types.Part.from_function_response(name="read_file", response={"result": "içerik"}),
            types.Part.from_function_response(name="list_directory", response={"error": "yok"}),
            types.Part(inline_data=types.Blob(mime_type="image/png", data=b"png", display_name="ekran-1")),
        ]),
    ]
    oa = qwen_fallback.to_openai([m.model_dump(mode="json", exclude_none=True) for m in msgs], vision=True)
    assert [m["role"] for m in oa] == ["user", "assistant", "tool", "tool", "user"]
    ids = [c["id"] for c in oa[1]["tool_calls"]]
    assert oa[1]["content"] == "Bakıyorum."
    assert [oa[2]["tool_call_id"], oa[3]["tool_call_id"]] == ids
    assert oa[3]["content"] == "HATA: yok"
    assert oa[4]["ada_screenshot"] and oa[4]["content"][0]["type"] == "image_url"

    back = [types.Content.model_validate(m) for m in qwen_fallback.to_gemini(oa[1:])]
    assert [c.role for c in back] == ["model", "user"]
    assert [p.function_response.name for p in back[1].parts if p.function_response] == ["read_file", "list_directory"]
    assert back[1].parts[-1].inline_data.data == b"png"
