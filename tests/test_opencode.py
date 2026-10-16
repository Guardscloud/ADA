"""OpenCode motoru ve OpenCode Zen sağlayıcısı: model süzme, hata sınıflandırma, MCP ucu, yönlendiriciyle devir.

Gerçek `opencode` komutu çalıştırılmaz: yerine aynı satır biçimini (`run --format json`) üreten ve ADA'nın
MCP ucuna gerçekten bağlanan küçük bir sahte program kullanılır.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

import httpx
import pytest
from conftest import gemini_response, text

from ada import agent, config, engines, router, store
from ada.providers import opencode

FAKE_CLI = r'''
import json, os, sys, urllib.request

args = sys.argv[1:]
mode = os.environ.get("ADA_FAKE_OC_MODE", "tool")
prompt = sys.stdin.read()
cfg = json.load(open(os.environ["OPENCODE_CONFIG"], encoding="utf-8"))
agent_cfg = cfg["agent"]["ada"]
system = open(agent_cfg["prompt"][len("{file:"):-1], encoding="utf-8").read()
with open(os.environ["ADA_FAKE_OC_LOG"], "w", encoding="utf-8") as f:
    json.dump({"args": args, "prompt": prompt, "system": system, "permission": agent_cfg["permission"]}, f)

def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

def say(t):
    out({"type": "text", "part": {"type": "text", "text": t}})

def rpc(url, method, params=None, id=1):
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if id is not None:
        body["id"] = id
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8")
        if "text/event-stream" in (r.headers.get("Content-Type") or ""):
            data = [l[5:].strip() for l in raw.splitlines() if l.startswith("data:")]
            return json.loads(data[-1])
        return json.loads(raw) if raw else None

out({"type": "step_start", "part": {"type": "step-start"}})
if mode == "free":
    out({"type": "error", "error": {"name": "APIError", "data": {"message": "OpenCode's free tier can only be used from within OpenCode.", "statusCode": 403}}})
    sys.exit(1)
if mode == "crash":
    sys.stderr.write("fetch failed: ECONNRESET\n")
    sys.exit(3)
if mode == "native":
    out({"type": "tool_use", "part": {"type": "tool", "tool": "bash", "callID": "call_1", "state": {
        "status": "completed", "input": {"command": "echo hi"}, "output": "hi", "title": "echo hi"}}})
    out({"type": "tool_use", "part": {"type": "tool", "tool": "read", "callID": "call_2", "state": {
        "status": "error", "input": {"filePath": "C:/Windows/win.ini"}, "error": "rule prevents"}}})
    out({"type": "step_finish", "part": {"type": "step-finish", "reason": "tool-calls"}})
    say("Yaptım.")
elif mode == "tool":
    url = cfg["mcp"]["ada"]["url"]
    rpc(url, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake", "version": "0"}})
    rpc(url, "notifications/initialized", id=None)
    names = [t["name"] for t in rpc(url, "tools/list")["result"]["tools"]]
    say("Dosyayı yazıyorum.")
    r1 = rpc(url, "tools/call", {"name": "write_text_file", "arguments": {"filename": "not.txt", "content": "merhaba"}})
    r2 = rpc(url, "tools/call", {"name": "yok_boyle_arac", "arguments": {}})
    out({"type": "step_finish", "part": {"type": "step-finish", "reason": "tool-calls"}})
    say("Bitti: " + ("write_text_file" in names and not r1["result"]["isError"] and r2["result"]["isError"] and "tamam" or "sorun"))
else:
    say("hazırım")
out({"type": "step_finish", "part": {"type": "step-finish", "reason": "stop"}})
'''


@pytest.fixture
def fake_cli(monkeypatch, tmp_path):
    script = tmp_path / "fake_opencode.py"
    script.write_text(FAKE_CLI, encoding="utf-8")
    log = tmp_path / "oc.json"
    monkeypatch.setenv("ADA_FAKE_OC_LOG", str(log))
    monkeypatch.setattr(opencode, "find_exe", lambda: sys.executable)
    real_args = opencode._args
    monkeypatch.setattr(opencode, "_args", lambda exe, *a: [exe, str(script)] + real_args(exe, *a)[1:])

    def use(mode: str):
        monkeypatch.setenv("ADA_FAKE_OC_MODE", mode)
        return lambda: json.loads(log.read_text(encoding="utf-8"))

    return use


@pytest.fixture
def adaagi(monkeypatch):
    monkeypatch.setattr(config, "PROVIDER", "adaagi")
    engines.AI_FILE.unlink(missing_ok=True)
    router.STATS_FILE.unlink(missing_ok=True)
    router._COOLDOWN.clear()
    yield
    engines.AI_FILE.unlink(missing_ok=True)
    router.STATS_FILE.unlink(missing_ok=True)
    router._COOLDOWN.clear()


def install(*items, oc_state="ok"):
    state = engines.load_state()
    state["engines"] = {e.id: asdict(e) for e in items}
    state["cloud_status"] = {engines.OPENCODE: {"state": oc_state}}
    engines.save_state(state)


def ui():
    return {"role": "assistant", "text": "", "steps": [], "files": []}


# ---------------------------------------------------------------- model listesi ve hatalar

def test_local_and_training_models_are_not_taken_as_engines():
    listed = ["opencode/big-pickle", "opencode/glm-5.3-flash-free", "opencode/kimi-k3", "ollama/qwen3:8b",
              "opencode/muse-spark-1.3-contributor-free", "github-copilot/gpt-5.5"]
    assert opencode.usable(listed) == ["opencode/big-pickle", "opencode/glm-5.3-flash-free", "opencode/kimi-k3",
                                       "github-copilot/gpt-5.5"]
    assert opencode.is_free("opencode/big-pickle") and opencode.is_free("opencode/glm-5.3-flash-free")
    assert not opencode.is_free("opencode/kimi-k3") and not opencode.is_free("other/x-free")


def test_engine_profile_comes_from_the_model_name():
    e = opencode.engine("anthropic/claude-opus-5-5")
    assert e.id == "opencode:anthropic/claude-opus-5-5" and e.model == "anthropic/claude-opus-5-5"
    assert e.family == "opencode" and e.provider == "opencode" and e.tier == 5 and e.vision and not e.local
    assert e.public()["provider_label"] == "OpenCode (CLI)"


def test_error_classification():
    assert "ücretsiz" in opencode.classify_error("OpenCode's free tier can only be used from within OpenCode").reason
    assert "oturumu yok" in opencode.classify_error("401 Unauthorized").reason
    assert "kota" in opencode.classify_error("429 rate limit exceeded").reason
    net = opencode.classify_error("fetch failed ECONNRESET")
    assert net.rest == 180
    assert opencode.classify_error("garip").reason.startswith("OpenCode hatası")
    assert isinstance(net, opencode.ClaudeCodeError)  # yönlendirici aynı yoldan ele alır


async def test_zen_lists_only_chat_completions_models(adaagi, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "k")
    rows = ["big-pickle", "glm-5.3-flash-free", "claude-opus-5-5", "gpt-5.5", "gemini-3-flash", "jev-1.13", "test",
            "claude-opus-5-5-backup", "kimi-k3", "glm-5.3", "deepseek-v4-pro"]
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"object": "list", "data": [{"id": r} for r in rows]})

    real = httpx.AsyncClient
    monkeypatch.setattr(engines.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    found = await engines.list_cloud("zen")
    assert seen["url"] == "https://opencode.ai/inference/v1/models"
    assert sorted(e.model for e in found) == ["deepseek-v4-pro", "glm-5.3", "kimi-k3"]
    assert all(e.base_url == "https://opencode.ai/inference/openai/v1" and e.family == "openai" for e in found)


# ---------------------------------------------------------------- tur

async def test_turn_runs_ada_tools_over_mcp_with_opencode_tools_denied(adaagi, fake_cli):
    log = fake_cli("tool")
    messages = [{"role": "user", "content": "not.txt dosyasına merhaba yaz"}]
    entry = ui()
    events = [e async for e in opencode.opencode_loop(
        messages, system=[{"type": "text", "text": "SİSTEM"}], tools=[t for t in agent.TOOLS if t.get("name") == "write_text_file"],
        ui_entry=entry, save=lambda: None, max_steps=10, model="anthropic/claude-sonnet-5-5")]

    seen = log()
    assert seen["permission"] == {"*": "deny", "ada_*": "allow"}
    assert seen["system"].startswith("SİSTEM") and "ada_run_shell" in seen["system"]
    assert "not.txt" in seen["prompt"] and "-m" in seen["args"] and "anthropic/claude-sonnet-5-5" in seen["args"]
    assert (config.OUTPUTS_DIR / "not.txt").read_text(encoding="utf-8") == "merhaba"
    assert entry["text"] == "Dosyayı yazıyorum.\n\nBitti: tamam"
    # Bilinmeyen araç geçmişe yazılmaz; yalnızca çalışan adım ve son yanıt eklenir
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"]
    assert not [e for e in events if e["type"] == "error"]


async def test_free_model_runs_with_opencode_tools_confined_and_logged(adaagi, fake_cli, monkeypatch):
    from ada import safety

    logged = []
    monkeypatch.setattr(safety, "log", lambda tool, args, **kw: logged.append((tool, args, kw["ok"])))
    log = fake_cli("native")
    messages = [{"role": "user", "content": "selam"}]
    entry = ui()
    events = [e async for e in opencode.opencode_loop(
        messages, system=[{"type": "text", "text": "SİSTEM"}], tools=[t for t in agent.TOOLS if t.get("name") == "write_text_file"],
        ui_entry=entry, save=lambda: None, max_steps=10, model="opencode/big-pickle")]

    assert log()["permission"] == {"*": "allow", "external_directory": "deny"}
    assert logged == [("opencode.bash", {"command": "echo hi"}, True), ("opencode.read", {"filePath": "C:/Windows/win.ini"}, False)]
    assert [s["label"] for s in entry["steps"]] == ["OpenCode · bash", "OpenCode · read (hata)"]
    assert any(e["type"] == "step_error" for e in events) and entry["text"] == "Yaptım."
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    assert messages[2]["content"] == "hi" and messages[4]["content"].startswith("HATA:")


async def test_free_engines_are_listed_but_off_by_default(adaagi, monkeypatch):
    async def ok():
        return {"state": "ok", "detail": "1 model"}, ["opencode/big-pickle"]

    monkeypatch.setattr(opencode, "status", ok)
    await engines.scan_opencode()
    (e,) = engines.all_engines()
    assert e.cost == 0 and not e.enabled and "ücretsiz" in e.label
    engines.set_enabled(e.id, True)
    await engines.scan_opencode()
    assert engines.all_engines()[0].enabled  # kullanıcının seçimi korunur


async def test_free_tier_rejection_and_crash_raise(adaagi, fake_cli):
    for mode, words in (("free", "ücretsiz"), ("crash", "ulaşılamadı")):
        fake_cli(mode)
        with pytest.raises(opencode.OpenCodeError) as e:
            await opencode.ask("opencode/kimi-k3", "selam")
        assert words in e.value.reason


async def test_scan_registers_engines(adaagi, monkeypatch):
    async def ok():
        return {"state": "ok", "detail": "2 model"}, ["anthropic/claude-opus-5-5", "opencode/kimi-k3"]

    async def missing():
        return {"state": "missing", "detail": "kurulu değil"}, []

    monkeypatch.setattr(opencode, "status", ok)
    assert (await engines.scan_opencode())["state"] == "ok"
    found = {e.id: e for e in engines.all_engines()}
    assert set(found) == {"opencode:anthropic/claude-opus-5-5", "opencode:opencode/kimi-k3"}
    assert all(e.available for e in found.values())
    assert router.plan("gizli") == []  # gizli işler buluta çıkmaz

    monkeypatch.setattr(opencode, "status", missing)
    await engines.scan_opencode()
    assert engines.all_engines() == []


async def test_router_runs_a_turn_with_opencode(adaagi, fake_cli):
    fake_cli("tool")
    install(opencode.engine("anthropic/claude-opus-5-5"))
    conv = store.create()
    prompt = "not.txt dosyasına merhaba yaz"
    events = [e async for e in agent.run_turn(conv, agent.build_user_content(prompt, []), prompt)]

    assert not [e for e in events if e["type"] == "error"]
    assert conv.engine == "opencode:anthropic/claude-opus-5-5"
    assert conv.messages[-1]["role"] == "model" and conv.messages[-1]["parts"][0]["text"] == "Bitti: tamam"


async def test_router_hands_over_when_opencode_fails(adaagi, fake_cli, fake_gemini):
    fake_cli("crash")
    fake_gemini(lambda contents, cfg: gemini_response(text("Gemini devraldı")))
    install(opencode.engine("anthropic/claude-opus-5-5"), engines.cloud_engine(engines.PROVIDERS["gemini"], "gemini-3.8-flash"))
    engines.set_route("sohbet", "opencode:anthropic/claude-opus-5-5")
    conv = store.create()
    events = [e async for e in agent.run_turn(conv, agent.build_user_content("selam", []), "selam")]

    assert conv.ui[-1]["text"] == "Gemini devraldı"
    assert any(e["type"] == "step" and e["label"] == "Motor değişti" for e in events)
    assert router._cooling("opencode:anthropic/claude-opus-5-5")
