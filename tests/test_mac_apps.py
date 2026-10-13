"""macOS uygulama köprüsü: kapalı uygulamalar betikten önce başlatılır (gerçek uygulama açılmaz)."""

from __future__ import annotations

import pytest

from ada.tools import ToolError, mac_apps


def test_closed_app_is_launched_before_script(monkeypatch):
    running = {"Calendar": False}
    launched, scripts = [], []

    def fake_run(argv, check=False):
        class R:
            returncode = 0 if (argv[0] == "pgrep" and running.get(argv[-1])) else (1 if argv[0] == "pgrep" else 0)
        if argv[:2] == ["open", "-gja"]:
            launched.append(argv[-1])
            running[argv[-1]] = True
        return R()

    monkeypatch.setattr(mac_apps.subprocess, "run", fake_run)
    monkeypatch.setattr(mac_apps, "_raw_osascript", lambda script, timeout=120: scripts.append(script) or "ok")
    assert mac_apps.run_osascript('tell application "Calendar" to get name of every calendar') == "ok"
    assert launched == ["Calendar"]
    assert scripts[-1].endswith("every calendar")


def test_retry_once_on_not_running_error(monkeypatch):
    calls = []

    def flaky(script, timeout=120):
        calls.append(script)
        if len(calls) == 1:
            raise ToolError("AppleScript hatası: Uygulama çalışmıyor. (-600)")
        return "ok"

    monkeypatch.setattr(mac_apps, "ensure_running", lambda app, timeout=90: None)
    monkeypatch.setattr(mac_apps, "_raw_osascript", flaky)
    monkeypatch.setattr(mac_apps.time, "sleep", lambda s: None)
    assert mac_apps.run_osascript('tell application "Notes" to get name') == "ok"
    assert len(calls) == 2


def test_other_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr(mac_apps, "ensure_running", lambda app, timeout=90: None)

    def fail(script, timeout=120):
        raise ToolError("AppleScript hatası: sözdizimi")

    monkeypatch.setattr(mac_apps, "_raw_osascript", fail)
    with pytest.raises(ToolError, match="sözdizimi"):
        mac_apps.run_osascript('tell application "Mail" to get name')


def test_permission_errors_are_explained(monkeypatch):
    from ada.tools import system

    class P:
        returncode = 1
        stdout = ""

        def __init__(self, err):
            self.stderr = err

    monkeypatch.setattr(system.subprocess, "run", lambda *a, **k: P("execution error: Not authorized to send Apple events to Mail. (-1743)"))
    with pytest.raises(ToolError, match="Otomasyon → ADA altında 'Mail'"):
        system.run_osascript('tell application "Mail" to get name')
    monkeypatch.setattr(system.subprocess, "run", lambda *a, **k: P("Calendar bir hatayla karşılaştı: AppleEvent zaman aşımına uğradı. (-1712)"))
    with pytest.raises(ToolError, match="izin penceresi"):
        system.run_osascript('tell application "Calendar" to get name')


def test_ui_elements_parsing_and_risk(monkeypatch):
    from ada.tools import ui_access

    S = ui_access.SEP
    raw = "\n".join([
        f"AXStaticText{S}{S}‎96{S}466{S}535{S}38{S}36",
        f"AXButton{S}AXIdentifier=Seven;{S}missing value{S}295{S}633{S}48{S}48",
        f"AXButton{S}AXIdentifier=_XCUI:CloseWindow;{S}{S}303{S}464{S}16{S}16",
        f"AXButton{S}AXTitle=Siparişi tamamla;AXIdentifier=buy;{S}{S}10{S}10{S}100{S}30",
    ])
    monkeypatch.setattr(mac_apps, "run_osascript", lambda script, timeout=120: raw)

    def no_native(app):
        raise ImportError  # AppleScript yedek yolunu sına

    monkeypatch.setattr(ui_access, "_native_collect", no_native)
    out = ui_access.ui_elements("Calculator").content
    assert '[1] metin: (adsız) = "96"' in out
    assert "[2] düğme: Seven" in out
    assert "CloseWindow" not in out  # pencere düğmeleri listelenmez
    assert ui_access._last["Calculator"][1]["cx"] == 319.0
    assert ui_access._risk({"app": "Calculator", "index": 3})  # "Siparişi tamamla" onaya düşer
    assert ui_access._risk({"app": "Calculator", "index": 2}) is None
    with pytest.raises(ToolError):
        ui_access._element("Calculator", 9)
