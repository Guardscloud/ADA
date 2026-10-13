"""Claude Code motoru: istem kurma, MCP ucu, araçların ADA katmanından geçmesi, yönlendiriciyle devir.

Gerçek `claude` komutu çalıştırılmaz: yerine aynı satır biçimini (stream-json) üreten ve ADA'nın MCP
ucuna gerçekten bağlanan küçük bir sahte program kullanılır.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

import httpx
import pytest
from conftest import gemini_response, text

from ada import agent, config, engines, router, store
from ada.providers import claude_code

FAKE_CLI = r'''
import json, os, sys, urllib.request

args = sys.argv[1:]
mode = os.environ.get("ADA_FAKE_CC_MODE", "tool")
prompt = json.loads(sys.stdin.readline())
with open(os.environ["ADA_FAKE_CC_LOG"], "w", encoding="utf-8") as f:
    json.dump({"args": args, "prompt": prompt, "system": open(args[args.index("--system-prompt-file") + 1], encoding="utf-8").read(),
               "api_key": os.environ.get("ANTHROPIC_API_KEY", "")}, f)

def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

def say(t):
    out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}})

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

if mode == "limit":
    out({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 4102444800}})
    out({"type": "result", "subtype": "success", "is_error": True, "result": "You've hit your limit"})
    sys.exit(1)
if mode == "crash":
    sys.stderr.write("fetch failed: ECONNRESET\n")
    sys.exit(3)
if mode == "tool":
    cfg = json.load(open(args[args.index("--mcp-config") + 1], encoding="utf-8"))
    url = cfg["mcpServers"]["ada"]["url"]
    rpc(url, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake", "version": "0"}})
    rpc(url, "notifications/initialized", id=None)
    names = [t["name"] for t in rpc(url, "tools/list")["result"]["tools"]]
    say("Dosyayı yazıyorum.")
    r1 = rpc(url, "tools/call", {"name": "mcp__ada__write_text_file", "arguments": {"filename": "not.txt", "content": "merhaba"}})
    r2 = rpc(url, "tools/call", {"name": "mcp__ada__yok_boyle_arac", "arguments": {}})
    say("Bitti: " + ("write_text_file" in names and not r1["result"]["isError"] and r2["result"]["isError"] and "tamam" or "sorun"))
else:
    say("hazırım")
out({"type": "result", "subtype": "success", "is_error": False, "result": "ok"})
'''


@pytest.fixture
def fake_cli(monkeypatch, tmp_path):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLI, encoding="utf-8")
    log = tmp_path / "cc.json"
    monkeypatch.setenv("ADA_FAKE_CC_LOG", str(log))
    monkeypatch.setattr(claude_code, "find_exe", lambda: sys.executable)
    real_args = claude_code._args
    monkeypatch.setattr(claude_code, "_args", lambda exe, *a: [exe, str(script)] + real_args(exe, *a)[1:])

    def use(mode: str):
        monkeypatch.setenv("ADA_FAKE_CC_MODE", mode)
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


def install(*items, cc_state="ok"):
    state = engines.load_state()
    state["engines"] = {e.id: asdict(e) for e in items}
    state["cloud_status"] = {engines.CLAUDE_CODE: {"state": cc_state}}
    engines.save_state(state)


def ui():
    return {"role": "assistant", "text": "", "steps": [], "files": []}


async def collect(messages, entry, **kw):
    kw.setdefault("tools", [t for t in agent.TOOLS if t.get("name") == "write_text_file"])
    return [e async for e in claude_code.claude_code_loop(
        messages, system=[{"type": "text", "text": "SİSTEM"}], ui_entry=entry, save=lambda: None,
        max_steps=kw.pop("max_steps", 10), model="opus", **kw)]


# ---------------------------------------------------------------- istem ve hata sınıflandırma

def test_prompt_carries_history_request_images_and_inherited_steps():
    png = "data:image/png;base64,AAAA"
    messages = [
        {"role": "user", "content": "İlk istek"},
        {"role": "assistant", "content": "Bakıyorum", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "x" * 5000},
        {"role": "assistant", "content": "İlk yanıt"},
        {"role": "user", "content": [{"type": "text", "text": "Şimdiki istek"}, {"type": "image_url", "image_url": {"url": png}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "run_shell", "arguments": '{"command": "dir"}'}}]},
        {"role": "tool", "tool_call_id": "c2", "content": "çıktı"},
    ]
    blocks = claude_code.build_prompt(messages)
    assert [b["type"] for b in blocks] == ["text", "text", "image", "text"]
    earlier, request, image, after = blocks
    assert "Kullanıcı: İlk istek" in earlier["text"] and "ADA: İlk yanıt" in earlier["text"] and "read_file" in earlier["text"]
    assert "kısaltıldı" in earlier["text"] and len(earlier["text"]) < 2000  # eski araç sonucu kısalır
    assert request["text"] == "Şimdiki istek"
    assert image["source"] == {"type": "base64", "media_type": "image/png", "data": "AAAA"}
    assert "run_shell" in after["text"] and "çıktı" in after["text"] and "Kaldığın yerden devam et" in after["text"]


def test_prompt_for_first_message_is_just_the_request():
    assert claude_code.build_prompt([{"role": "user", "content": "selam"}]) == [{"type": "text", "text": "selam"}]


def test_errors_are_classified():
    limit = claude_code.classify_error("You've hit your limit · resets 3pm")
    assert "kullanım sınırı" in limit.reason and limit.rest == 3600 and not limit.provider_wide
    login = claude_code.classify_error("Invalid API key · Please run /login")
    assert "oturumu açık değil" in login.reason and login.provider_wide
    assert "kullanılamıyor" in claude_code.classify_error("model not found: fable").reason
    net = claude_code.classify_error("fetch failed ECONNRESET")
    assert net.rest == 180 and net.provider_wide
    assert claude_code.classify_error("garip bir şey").reason.startswith("Claude Code hatası")


def test_tool_specs_expand_the_screen_toolset():
    names = {s["name"] for s in claude_code.tool_specs(agent.TOOLS)}
    assert {"run_shell", "screenshot", "left_click"} <= names
    assert all("inputSchema" in s for s in claude_code.tool_specs(agent.TOOLS))


# ---------------------------------------------------------------- tur

async def test_turn_runs_ada_tools_through_mcp_and_records_history(fake_cli):
    seen = fake_cli("tool")
    messages = [{"role": "user", "content": "not.txt dosyasına merhaba yaz"}]
    entry = ui()
    events = await collect(messages, entry)

    assert (config.OUTPUTS_DIR / "not.txt").read_text(encoding="utf-8") == "merhaba"
    assert entry["text"] == "Dosyayı yazıyorum.\n\nBitti: tamam"
    assert entry["files"] == ["outputs/not.txt"]
    assert [e["label"] for e in events if e["type"] == "step"] == ["Dosya yazıyor"]  # bilinmeyen araç çalıştırılmadı
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert messages[1]["content"] == "Dosyayı yazıyorum."
    assert messages[1]["tool_calls"][0]["function"]["name"] == "write_text_file"  # geçmişte ADA'nın araç adı
    assert messages[3] == {"role": "assistant", "content": "Bitti: tamam"}

    log = seen()
    assert log["prompt"]["message"]["content"] == [{"type": "text", "text": "not.txt dosyasına merhaba yaz"}]
    assert log["system"].startswith("SİSTEM") and "mcp__ada__" in log["system"]
    args = log["args"]
    assert args[args.index("--tools") + 1] == "" and "--strict-mcp-config" in args  # kendi araçları kapalı
    assert args[args.index("--model") + 1] == "opus"


async def test_tool_calls_are_written_to_the_audit_log(fake_cli):
    from ada import safety

    fake_cli("tool")
    await collect([{"role": "user", "content": "yaz"}], ui())
    assert any(e["tool"] == "write_text_file" and e["ok"] for e in safety.recent(20))


async def test_subscription_is_used_not_the_api_key(fake_cli, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-gizli")
    monkeypatch.setenv("CLAUDECODE", "1")
    seen = fake_cli("chat")
    entry = ui()
    await collect([{"role": "user", "content": "selam"}], entry, tools=[])
    assert entry["text"] == "hazırım"
    assert seen()["api_key"] == ""
    assert "--mcp-config" not in seen()["args"]  # araç yoksa MCP ucu da açılmaz


async def test_step_limit_stops_tool_use(fake_cli):
    fake_cli("tool")
    messages = [{"role": "user", "content": "yaz"}]
    events = await collect(messages, ui(), max_steps=0)
    assert not (config.OUTPUTS_DIR / "not.txt").exists()
    assert any(e["type"] == "error" and "Adım sınırına" in e["message"] for e in events)


async def test_usage_limit_and_crash_raise_engine_errors(fake_cli):
    fake_cli("limit")
    with pytest.raises(claude_code.ClaudeCodeError) as limit:
        await collect([{"role": "user", "content": "selam"}], ui())
    assert "kullanım sınırı" in limit.value.reason and limit.value.rest > 3600  # sıfırlanma saatine kadar dinlenir
    fake_cli("crash")
    with pytest.raises(claude_code.ClaudeCodeError) as crash:
        await collect([{"role": "user", "content": "selam"}], ui())
    assert crash.value.provider_wide and "ulaşılamadı" in crash.value.reason


async def test_missing_cli_is_a_provider_wide_failure(monkeypatch):
    monkeypatch.setattr(claude_code, "find_exe", lambda: None)
    with pytest.raises(claude_code.ClaudeCodeError) as e:
        await collect([{"role": "user", "content": "selam"}], ui())
    assert e.value.provider_wide


async def test_mcp_endpoint_rejects_wrong_token_and_other_methods():
    import asyncio

    turn = claude_code._Turn([], [], ui(), lambda: None, 5)
    server = await asyncio.start_server(turn.handle_conn, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/mcp/"
    try:
        async with httpx.AsyncClient() as client:
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            assert (await client.post(base + "yanlis", json=body)).status_code == 404
            assert (await client.get(base + turn.token)).status_code == 405
            ok = await client.post(base + turn.token, json=body)
            assert ok.status_code == 200 and ok.json()["result"] == {"tools": []}
            assert (await client.post(base + turn.token, json={"jsonrpc": "2.0", "method": "notifications/initialized"})).status_code == 202
            unknown = await client.post(base + turn.token, json={"jsonrpc": "2.0", "id": 2, "method": "olmayan"})
            assert unknown.json()["error"]["code"] == -32601
    finally:
        server.close()


# ---------------------------------------------------------------- motor kaydı ve yönlendirme

async def test_scan_registers_engines_only_usable_when_logged_in(adaagi, monkeypatch):
    async def logged_in():
        return {"state": "ok", "detail": "a@b.c · max aboneliği"}

    async def logged_out():
        return {"state": "nologin", "detail": "oturum yok"}

    async def missing():
        return {"state": "missing", "detail": "kurulu değil"}

    monkeypatch.setattr(claude_code, "status", logged_in)
    assert (await engines.scan_claude_code())["state"] == "ok"
    found = {e.id: e for e in engines.all_engines()}
    assert set(found) == {"claudecode:opus", "claudecode:sonnet", "claudecode:haiku"}
    opus = found["claudecode:opus"]
    assert opus.available and opus.vision and opus.tier == 5 and not opus.local and opus.toolset == "full"
    assert opus.public()["provider_label"] == "Claude Code (abonelik)"
    assert router.plan("kod")[0].id == "claudecode:opus"
    assert router.plan("gizli") == []  # gizli işler buluta çıkmaz

    engines.set_enabled("claudecode:haiku", False)
    monkeypatch.setattr(claude_code, "status", logged_out)
    await engines.scan_claude_code()
    found = {e.id: e for e in engines.all_engines()}
    assert not found["claudecode:opus"].available and not found["claudecode:haiku"].enabled  # kullanıcının seçimi korunur
    assert router.plan("kod") == []

    monkeypatch.setattr(claude_code, "status", missing)
    await engines.scan_claude_code()
    assert engines.all_engines() == []


async def test_router_runs_a_turn_with_claude_code(adaagi, fake_cli):
    fake_cli("tool")
    install(claude_code.engine("opus"))
    conv = store.create()
    prompt = "not.txt dosyasına merhaba yaz"
    events = [e async for e in agent.run_turn(conv, agent.build_user_content(prompt, []), prompt)]

    assert not [e for e in events if e["type"] == "error"]
    assert (config.OUTPUTS_DIR / "not.txt").exists()
    assert conv.engine == "claudecode:opus" and conv.ui[-1]["engine"] == "Claude Code · Opus"
    # Geçmiş ortak (Gemini) biçimde: başka bir motor sonraki turu sürdürebilir
    parts = [p for m in conv.messages for p in m.get("parts", [])]
    assert any((p.get("function_call") or {}).get("name") == "write_text_file" for p in parts)
    assert any((p.get("function_response") or {}).get("name") == "write_text_file" for p in parts)
    assert conv.messages[-1]["role"] == "model" and conv.messages[-1]["parts"][0]["text"] == "Bitti: tamam"


async def test_router_hands_over_when_the_subscription_limit_is_hit(adaagi, fake_cli, fake_gemini):
    fake_cli("limit")
    fake_gemini(lambda contents, cfg: gemini_response(text("Gemini devraldı")))
    install(claude_code.engine("opus"), engines.cloud_engine(engines.PROVIDERS["gemini"], "gemini-3.8-flash"))
    engines.set_route("sohbet", "claudecode:opus")
    conv = store.create()
    events = [e async for e in agent.run_turn(conv, agent.build_user_content("selam", []), "selam")]

    assert conv.ui[-1]["text"] == "Gemini devraldı"
    assert any(e["type"] == "step" and e["label"] == "Motor değişti" and "kullanım sınırı" in e["detail"] for e in events)
    assert router._cooling("claudecode:opus")
