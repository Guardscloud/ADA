"""Terminal oturumları, kalıcı komutlar ve Gözcü.

Terminal testleri gerçek bir pty (Windows: ConPTY) açar ve yalnızca zararsız `echo` komutları çalıştırır.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from conftest import gemini_response, last_user_text, text
from fastapi.testclient import TestClient

from ada import config, runtime, safety, server, store, terminal, watchdog
from ada.tools import REGISTRY, ToolError, guard_write

IS_WIN = sys.platform.startswith("win")


@pytest.fixture(autouse=True)
def clean_terminal():
    yield
    terminal.kill_all()
    terminal.SESSIONS.clear()
    terminal.FILE.unlink(missing_ok=True)
    terminal.KEEPER.restarts.clear()
    terminal.KEEPER.gave_up.clear()
    watchdog.STATE_FILE.unlink(missing_ok=True)
    watchdog.CHECKLIST_FILE.unlink(missing_ok=True)


async def wait_for(cond, timeout=20.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("zaman aşımı")
        await asyncio.sleep(0.1)


# ---------------------------------------------------------------- terminal

async def test_command_session_output_and_exit():
    s = terminal.open_session("echo merhaba-ğüş-42")
    await wait_for(lambda: not s.alive)
    assert "merhaba-ğüş-42" in s.tail()
    assert s.exit_code == 0
    assert "süreç bitti" in s.tail()


async def test_interactive_shell_write_and_close():
    s = terminal.open_session("")
    q = s.subscribe()
    s.write("echo yazildi-77\r")
    await wait_for(lambda: s.tail().count("yazildi-77") >= 2)  # yankı + çıktı
    assert not q.empty()
    assert terminal.close_session(s.id)
    await wait_for(lambda: not s.alive)
    assert terminal.get(s.id) is None


async def test_session_limit(monkeypatch):
    monkeypatch.setattr(terminal, "MAX_SESSIONS", 1)
    terminal.open_session("")
    with pytest.raises(RuntimeError):
        terminal.open_session("")


async def test_multiline_command_rejected():
    with pytest.raises(ValueError):
        terminal.open_session("echo a\nrm -rf /")


async def test_emergency_stop_kills_only_ada_sessions():
    mine = terminal.open_session("", source="arayüz")
    ada = terminal.open_session("", source="ada")
    r = await safety.emergency_stop("test")
    safety.resume("test")
    assert r["terminals"] == 1
    await wait_for(lambda: not ada.alive)
    assert mine.alive


async def test_keeper_restarts_and_gives_up(monkeypatch):
    monkeypatch.setattr(terminal, "RESTART_DELAY_SECONDS", 0)
    monkeypatch.setattr(terminal, "MAX_RESTARTS_PER_HOUR", 2)
    item = terminal.add_saved("echo kalici", "test", autostart=True)
    await terminal.KEEPER.tick()
    first = terminal.session_for(item["id"])
    assert first is not None and first.source == "kalıcı"
    await wait_for(lambda: not first.alive)
    await terminal.KEEPER.tick()
    second = terminal.session_for(item["id"])
    assert second is not first
    await wait_for(lambda: not second.alive)
    await terminal.KEEPER.tick()  # saatlik sınır doldu: vazgeçer
    assert terminal.session_for(item["id"]) is second
    status = terminal.KEEPER.status()[0]
    assert status["gave_up"] and not status["alive"]
    # Gözcü bunu kritik sorun sayar
    found = watchdog.Watchdog(60).evaluate({"disks": [], "cpu": 0, "mem": 0, "internet": True, "qwen": None})
    assert found[f"keeper:{item['id']}"]["level"] == "crit"


async def test_keeper_ignores_disabled_and_halt():
    item = terminal.add_saved("echo x", autostart=False)
    await terminal.KEEPER.tick()
    assert terminal.session_for(item["id"]) is None
    terminal.update_saved(item["id"], autostart=True)
    await safety.emergency_stop("test")
    try:
        await terminal.KEEPER.tick()
        assert terminal.session_for(item["id"]) is None
    finally:
        safety.resume("test")


async def test_tools_open_read_send_and_keep_refused_unattended():
    out = await REGISTRY["terminal_open"].handler(command="", title="araç", wait_seconds=1)
    sid = out.content.split("Oturum ")[1].split(" ")[0]
    s = terminal.get(sid)
    assert s.source == "ada"
    await REGISTRY["terminal_send"].handler(session_id=sid, text="echo arac-99", wait_seconds=0)
    await wait_for(lambda: s.tail().count("arac-99") >= 2)
    read = await REGISTRY["terminal_read"].handler(session_id=sid)
    assert "arac-99" in read.content and "\x1b" not in read.content
    with pytest.raises(ToolError):
        await REGISTRY["terminal_open"].handler(command="mkfs /dev/sda")
    token = runtime.UNATTENDED.set(True)
    try:
        with pytest.raises(ToolError):
            await REGISTRY["terminal_keep"].handler(command="echo x")
    finally:
        runtime.UNATTENDED.reset(token)
    assert terminal.load_saved() == []


# ---------------------------------------------------------------- sunucu uçları

@pytest.fixture
def client(monkeypatch):
    @asynccontextmanager
    async def no_lifespan(_app):  # arka plan döngüleri testte başlamasın
        yield

    monkeypatch.setattr(server.app.router, "lifespan_context", no_lifespan)
    with TestClient(server.app) as c:
        yield c


ORIGIN = {"origin": "http://testserver"}


def test_http_and_websocket_terminal(client):
    r = client.post("/api/terminal/sessions", json={"command": ""}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    with client.websocket_connect(f"/api/terminal/sessions/{sid}/ws", headers=ORIGIN) as ws:
        ws.send_json({"t": "resize", "cols": 100, "rows": 30})
        ws.send_json({"t": "in", "d": "echo ws-test-5\r"})
        seen = ""
        for _ in range(200):
            msg = ws.receive_json()
            seen += msg.get("d", "")
            if seen.count("ws-test-5") >= 2:
                break
        assert seen.count("ws-test-5") >= 2
    assert any(s["id"] == sid for s in client.get("/api/terminal").json()["sessions"])
    assert client.delete(f"/api/terminal/sessions/{sid}", headers=ORIGIN).status_code == 200


def test_terminal_rejects_foreign_origin(client):
    evil = {"origin": "http://evil.example"}
    assert client.post("/api/terminal/sessions", json={"command": "echo x"}, headers=evil).status_code == 403
    assert client.post("/api/terminal/saved", json={"command": "echo x"}, headers=evil).status_code == 403
    assert client.post("/api/watch/checklist", json={"content": "- x"}, headers=evil).status_code == 403
    sid = client.post("/api/terminal/sessions", json={"command": ""}, headers=ORIGIN).json()["id"]
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(f"/api/terminal/sessions/{sid}/ws", headers=evil) as ws:
            ws.receive_json()
    assert e.value.code == 4403


def test_websocket_requires_login_when_password_set(client, monkeypatch):
    monkeypatch.setattr(config, "PASSWORD", "gizli")
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/api/terminal/sessions/yok/ws", headers=ORIGIN) as ws:
            ws.receive_json()
    assert e.value.code == 4403


def test_saved_commands_api(client):
    r = client.post("/api/terminal/saved", json={"command": "claude remote-control", "title": "RC", "autostart": False}, headers=ORIGIN)
    assert r.status_code == 200
    sid = r.json()["id"]
    assert client.post(f"/api/terminal/saved/{sid}", json={"autostart": True}, headers=ORIGIN).json()["autostart"] is True
    assert client.get("/api/terminal").json()["saved"][0]["command"] == "claude remote-control"
    assert client.post("/api/terminal/saved", json={"command": ""}, headers=ORIGIN).status_code == 400
    assert client.delete(f"/api/terminal/saved/{sid}", headers=ORIGIN).status_code == 200
    assert terminal.load_saved() == []


# ---------------------------------------------------------------- gözcü

def test_checklist_items_ignore_template_and_comments():
    assert watchdog.checklist_items(watchdog.CHECKLIST_TEMPLATE) == ""
    content = "# Liste\nAçıklama\n- Birinci madde\n<!-- - gizli -->\n2. İkinci\n"
    assert watchdog.checklist_items(content) == "- Birinci madde\n2. İkinci"


def test_guard_write_protects_checklist():
    with pytest.raises(ToolError):
        guard_write(config.WORKSPACE / "gozcu.md")


async def test_problem_lifecycle_notify_and_ai_fix(fake_gemini, monkeypatch):
    notes = []
    monkeypatch.setattr(runtime, "notify", lambda t, m: notes.append(t))
    prompts = []
    fake_gemini(lambda c, cfg: (prompts.append(last_user_text(c)), gemini_response(text("Geçici dosyalar temizlendi.")))[1])
    w = watchdog.Watchdog(60)
    now = datetime.now()
    m = {"disks": [{"mount": "C:\\", "percent": 98.0, "free_gb": 2.0, "total_gb": 100.0}], "cpu": 5, "mem": 40, "internet": True, "qwen": None}
    w.metrics = m
    await w.apply(w.evaluate(m), now)
    assert w.problems["disk:C:\\"]["level"] == "crit"
    assert any("Gözcü" in n for n in notes)
    conv_id = w.fix_runs["disk:C:\\"]
    await asyncio.wait({runtime.RUNS[conv_id].task})
    assert "[GÖZCÜ MODU]" in prompts[0] and "diski" in prompts[0]
    # Aynı sorun tekrar bildirilmez, yeniden incelenmez
    notes.clear()
    await w.apply(w.evaluate(m), now + timedelta(minutes=1))
    assert notes == [] and not await w.maybe_fix("disk:C:\\", w.problems["disk:C:\\"], now + timedelta(minutes=2))
    # Düzelince geçmişe "çözüldü" yazılır
    ok = {**m, "disks": [{"mount": "C:\\", "percent": 50.0, "free_gb": 50.0, "total_gb": 100.0}]}
    await w.apply(w.evaluate(ok), now + timedelta(minutes=3))
    assert not w.problems
    events = [h["event"] for h in w.status()["history"]]
    assert events[:2] == ["çözüldü", "sorun"]


async def test_cpu_needs_sustained_samples():
    w = watchdog.Watchdog(60)
    m = {"disks": [], "cpu": 99, "mem": 10, "internet": True, "qwen": None, "top_cpu": ["x"]}
    for _ in range(watchdog.CPU_SAMPLES - 1):
        assert "cpu" not in w.evaluate(m)
    assert "cpu" in w.evaluate(m)
    assert "cpu" not in w.evaluate({**m, "cpu": 10})


async def _run_checklist(fake_gemini, answer):
    watchdog.save_checklist("- Masaüstünü kontrol et\n")
    seen = []
    fake_gemini(lambda c, cfg: (seen.append(last_user_text(c)), gemini_response(text(answer)))[1])
    w = watchdog.Watchdog(60)
    w.started = datetime.now() - timedelta(hours=1)
    conv_id = await w.maybe_checklist(datetime.now())
    assert conv_id
    await wait_for(lambda: w.checklist_conv is None)
    assert "Masaüstünü kontrol et" in seen[0]
    return w, conv_id


async def test_checklist_ok_deletes_conversation(fake_gemini):
    w, conv_id = await _run_checklist(fake_gemini, "1. Temiz.\nDURUM: TAMAM")
    assert store.load(conv_id) is None
    cl = w.status()["checklist"]
    assert cl["last_status"] == "ok" and cl["next_run"]
    # Aralık dolmadan tekrar çalışmaz
    assert await w.maybe_checklist(datetime.now()) is None


async def test_checklist_issue_keeps_conversation(fake_gemini, monkeypatch):
    notes = []
    monkeypatch.setattr(runtime, "notify", lambda t, m: notes.append(m))
    w, conv_id = await _run_checklist(fake_gemini, "1. Masaüstünde 3 yeni dosya vardı, taşıdım.\nDURUM: SORUN")
    assert store.load(conv_id) is not None
    cl = w.status()["checklist"]
    assert cl["last_status"] == "issue" and cl["last_conv"] == conv_id and "taşıdım" in cl["last_summary"]
    assert notes


async def test_checklist_skipped_when_empty_or_disabled(monkeypatch):
    w = watchdog.Watchdog(60)
    w.started = datetime.now() - timedelta(hours=1)
    watchdog.save_checklist(watchdog.CHECKLIST_TEMPLATE)
    assert await w.maybe_checklist(datetime.now()) is None
    watchdog.save_checklist("- bir madde")
    monkeypatch.setattr(config, "WATCH_AI_MINUTES", 0)
    assert await w.maybe_checklist(datetime.now()) is None


def test_measure_real_system():
    m = watchdog.measure()
    assert 0 <= m["mem"] <= 100 and m["disks"]

# ---------------------------------------------------------------- yerleşik bakım rutini ve tam özerklik

def test_builtin_maintenance_routine_is_gone_and_removed_from_old_installs():
    from ada import maintenance, routines

    # Yeni kurulum: ADA kendiliğinden rutin eklemez
    maintenance.FLAG.unlink(missing_ok=True)
    assert maintenance.remove_builtin() is False and routines.load() == []
    assert not (config.ROUTINES_DIR / maintenance.FILENAME).exists()

    # Eski sürümden kalan kurulum: ADA'nın eklediği rutin bir kez silinir, kullanıcının rutinlerine dokunulmaz
    routines.create("---\nad: Sistem bakımı ve güvenlik denetimi\nsaat: 10:00\n---\nDenetle.", maintenance.FILENAME)
    mine = routines.create("---\nad: Benim rutinim\nsaat: 09:00\n---\nRapor yaz.", "benim.md")
    maintenance.FLAG.write_text("1", encoding="utf-8")
    assert maintenance.remove_builtin() is True
    assert [r["id"] for r in routines.load()] == [mine["id"]]
    assert not (config.ROUTINES_DIR / maintenance.FILENAME).exists() and (config.ROUTINES_DIR / "benim.md").exists()
    assert not maintenance.FLAG.exists() and maintenance.remove_builtin() is False

    # Kullanıcı aynı adla kendi rutinini yazdıysa (işaret dosyası yok) silinmez
    routines.create("---\nad: Kendi bakımım\nsaat: 10:00\n---\nDenetle.", maintenance.FILENAME)
    assert maintenance.remove_builtin() is False and (config.ROUTINES_DIR / maintenance.FILENAME).exists()


def test_full_autonomy_hint_in_context(monkeypatch):
    from ada import agent

    monkeypatch.setattr(config, "AUTONOMY", "full")
    assert "Soru sorma" in agent._context_block()["text"]
    monkeypatch.setattr(config, "AUTONOMY", "smart")
    assert "TAM ÖZERKLİK" not in agent._context_block()["text"]
