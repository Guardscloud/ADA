"""Windows plumbing: bildirimler, Geri Dönüşüm Kutusu, UTF-8 kabuk, tuş eşleme, açılışta başlat,
tek kopya, izin kılavuzu. Testler gerçek kayıt defterine/fare-klavyeye/panoya DOKUNMAZ (hepsi
taklit edilir); yalnızca run_shell'in Türkçe çıktısı gerçek PowerShell ile doğrulanır (güvenli).

Yalnızca Windows'ta anlamlıdır; diğer platformlarda atlanır.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ada import config

pytestmark = pytest.mark.skipif(not config.IS_WIN, reason="Yalnızca Windows")


# ---------------------------------------------------------------- konsol pencereleri

def test_child_processes_start_without_a_console_window(monkeypatch):
    import subprocess

    from ada import desktop

    seen = []

    def fake_init(self, *args, **kwargs):
        seen.append(kwargs.get("creationflags", 0))

    monkeypatch.setattr(subprocess.Popen, "__init__", fake_init)
    assert desktop._hide_child_consoles(force=True)
    assert desktop._hide_child_consoles(force=True)  # ikinci kez sarmalanmaz
    subprocess.Popen(["powershell", "-Command", "1"])
    subprocess.Popen(["cmd"], creationflags=subprocess.CREATE_NEW_CONSOLE)  # kendi bayrağı korunur
    assert seen == [subprocess.CREATE_NO_WINDOW, subprocess.CREATE_NEW_CONSOLE]


def test_hidden_powershell_still_returns_output(monkeypatch):
    import subprocess

    from ada import desktop

    monkeypatch.setattr(subprocess.Popen, "__init__", subprocess.Popen.__init__)
    desktop._hide_child_consoles(force=True)
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "Write-Output gizli"], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "gizli"


# ---------------------------------------------------------------- bildirimler (notify)

def test_toast_script_escapes_xml_and_quotes():
    from ada import notify

    script = notify._toast_script("A & B <c>", "x'y \"z\" & <w>", notify._AUMID)
    # Kullanıcı metnindeki özel karakterler XML varlıklarına çevrilir
    assert "&amp;" in script and "&lt;" in script and "&gt;" in script
    assert "&apos;" in script and "&quot;" in script
    # Ham tehlikeli karakter XML gövdesinde kalmamalı (PowerShell tek tırnaklı dizesini bozardı)
    body = script.split("$xml='", 1)[1]
    assert "x'y" not in body  # apostrof kaçırıldı


def test_toast_script_caps_lengths():
    from ada import notify

    script = notify._toast_script("T" * 200, "M" * 500, notify._AUMID)
    assert "T" * 60 in script and "T" * 61 not in script
    assert "M" * 200 in script and "M" * 201 not in script


def test_encoded_command_is_utf16le_base64():
    from ada import notify

    enc = notify.encoded_command("Başlık", "çğıöşü İ", notify._PS_AUMID)
    decoded = base64.b64decode(enc).decode("utf-16-le")
    assert "ToastGeneric" in decoded
    assert "&#231;" in decoded or "çğıöşü" in decoded or "ç" in decoded  # Türkçe korunur


def test_win_popen_uses_encoded_command_and_no_window(monkeypatch):
    from ada import notify

    calls = {}

    def fake_popen(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return NS()

    monkeypatch.setattr(notify, "_register_aumid", lambda: notify._PS_AUMID)
    monkeypatch.setattr(notify.subprocess, "Popen", fake_popen)
    notify._win("ADA", "bitti")
    argv = calls["argv"]
    assert argv[0] == "powershell"
    assert "-NoProfile" in argv and "-NonInteractive" in argv and "-EncodedCommand" in argv
    assert "-WindowStyle" in argv and "Hidden" in argv
    # -EncodedCommand'den sonra gelen değer geçerli base64 (UTF-16LE) olmalı
    enc = argv[argv.index("-EncodedCommand") + 1]
    assert "ToastGeneric" in base64.b64decode(enc).decode("utf-16-le")
    assert calls["kwargs"]["creationflags"] == notify.CREATE_NO_WINDOW
    assert calls["kwargs"]["stdout"] == notify.subprocess.DEVNULL


def test_register_aumid_falls_back_on_winreg_error(monkeypatch):
    from ada import notify

    monkeypatch.setattr(notify, "_registered", None)

    import winreg

    def boom(*a, **k):
        raise OSError("izin yok")

    monkeypatch.setattr(winreg, "CreateKey", boom)
    assert notify._register_aumid() == notify._PS_AUMID
    assert notify._registered is False  # bir daha denemez


def test_register_aumid_success(monkeypatch):
    from ada import notify

    monkeypatch.setattr(notify, "_registered", None)
    import winreg

    stored = {}

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(winreg, "CreateKey", lambda root, sub: FakeKey())
    monkeypatch.setattr(winreg, "SetValueEx", lambda key, name, r, t, v: stored.__setitem__(name, v))
    assert notify._register_aumid() == notify._AUMID
    assert stored["DisplayName"] == "ADA"


def test_notify_never_raises(monkeypatch):
    from ada import notify

    def boom(*a, **k):
        raise RuntimeError("patladı")

    monkeypatch.setattr(notify.config, "NOTIFICATIONS", True)  # testlerde varsayılan kapalı
    monkeypatch.setattr(notify, "_win", boom)
    monkeypatch.setattr(notify, "_mac", boom)
    notify.notify("x", "y")  # yükselmemeli


def test_notify_tool_registered_on_windows(monkeypatch):
    from ada import notify
    from ada.tools import REGISTRY

    assert "notify" in REGISTRY
    sent = {}
    monkeypatch.setattr(notify, "notify", lambda t, m: sent.update(title=t, message=m))
    result = REGISTRY["notify"].handler(title="ADA", message="iş bitti")
    assert sent == {"title": "ADA", "message": "iş bitti"}
    assert "gösterildi" in result.lower() or "bildirim" in result.lower()


# ---------------------------------------------------------------- run_shell UTF-8 (gerçek)

def test_run_shell_decodes_turkish_output():
    from ada.tools import REGISTRY

    r = REGISTRY["run_shell"].handler(command="Write-Output 'çğıöşü İ'", timeout=30)
    assert "çğıöşü İ" in r.content
    assert "çıkış kodu: 0" in r.content


def test_run_shell_preamble_sets_utf8():
    from ada.tools import system

    assert "UTF8" in system._PS_UTF8_PREAMBLE
    assert "OutputEncoding" in system._PS_UTF8_PREAMBLE


# ---------------------------------------------------------------- delete_path -> Geri Dönüşüm Kutusu

def test_delete_path_windows_uses_recycle(monkeypatch, tmp_path):
    from ada.tools import REGISTRY, system

    victim = tmp_path / "çöp.txt"
    victim.write_text("x", encoding="utf-8")
    captured = []
    monkeypatch.setattr(system, "_recycle_windows", lambda p: captured.append(p))
    msg = REGISTRY["delete_path"].handler(path=str(victim))
    assert captured == [victim.resolve()]
    assert "Geri Dönüşüm" in msg


def test_recycle_windows_sets_correct_flags(monkeypatch, tmp_path):
    import ctypes

    from ada.tools import system

    captured = {}

    def fake_shfileop(ptr):
        op = ptr._obj  # byref'in sardığı struct
        captured["wFunc"] = op.wFunc
        captured["pFrom"] = op.pFrom
        captured["fFlags"] = op.fFlags
        return 0

    monkeypatch.setattr(ctypes.windll.shell32, "SHFileOperationW", fake_shfileop)
    target = tmp_path / "dosya.txt"
    system._recycle_windows(target)
    assert captured["wFunc"] == 0x0003  # FO_DELETE
    # FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
    assert captured["fFlags"] == (0x0040 | 0x0010 | 0x0400 | 0x0004)
    assert captured["pFrom"].startswith(str(target))


def test_recycle_windows_raises_on_error(monkeypatch, tmp_path):
    import ctypes

    from ada.tools import system
    from ada.tools import ToolError

    monkeypatch.setattr(ctypes.windll.shell32, "SHFileOperationW", lambda ptr: 124)
    with pytest.raises(ToolError):
        system._recycle_windows(tmp_path / "x.txt")


# ---------------------------------------------------------------- computer: tuş eşleme + yazma

def test_key_aliases_windows():
    from ada.tools import computer

    assert computer._keys("cmd+s") == ["ctrl", "s"]
    assert computer._keys("command+c") == ["ctrl", "c"]
    assert computer._keys("win") == ["win"]
    assert computer._keys("super") == ["win"]
    assert computer._keys("meta") == ["win"]
    assert computer._keys("alt+tab") == ["alt", "tab"]
    assert computer._keys("option") == ["alt"]
    assert computer._keys("ctrl+shift+n") == ["ctrl", "shift", "n"]


def _fake_gui(calls):
    return NS(
        write=lambda text, interval=0.0: calls.append(("write", text)),
        hotkey=lambda *keys: calls.append(("hotkey", keys)),
    )


def test_type_ascii_uses_write(monkeypatch):
    from ada.tools import computer

    calls = []
    monkeypatch.setattr(computer, "_gui", lambda: _fake_gui(calls))
    computer._type("hello world")
    assert calls == [("write", "hello world")]


def test_type_nonascii_uses_clipboard_and_restores(monkeypatch):
    from ada.tools import computer

    calls = []
    clip = {"v": "eski pano"}
    fake_pyperclip = NS(
        paste=lambda: clip["v"],
        copy=lambda v: (calls.append(("copy", v)), clip.__setitem__("v", v))[0],
    )
    monkeypatch.setitem(sys.modules, "pyperclip", fake_pyperclip)
    monkeypatch.setattr(computer, "_gui", lambda: _fake_gui(calls))
    monkeypatch.setattr(computer.time, "sleep", lambda s: None)

    computer._type("çğıöşü İ")

    # Metin panoya kopyalandı, Ctrl+V basıldı, sonra önceki pano geri yüklendi
    assert ("copy", "çğıöşü İ") in calls
    assert ("hotkey", ("ctrl", "v")) in calls
    assert calls[-1] == ("copy", "eski pano")
    assert not any(c[0] == "write" for c in calls)  # ASCII-dışı yazma asla gui.write ile olmaz


# ---------------------------------------------------------------- login_item: açılışta başlat (Run key)

class _FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fake_winreg(monkeypatch):
    import winreg

    store: dict[str, str] = {}

    def openkey(root, sub, *a):
        return _FakeKey()

    def setvalue(key, name, reserved, typ, value):
        store[name] = value

    def queryvalue(key, name):
        if name not in store:
            raise FileNotFoundError(name)
        return store[name], winreg.REG_SZ

    def deletevalue(key, name):
        if name not in store:
            raise FileNotFoundError(name)
        del store[name]

    monkeypatch.setattr(winreg, "OpenKey", openkey)
    monkeypatch.setattr(winreg, "SetValueEx", setvalue)
    monkeypatch.setattr(winreg, "QueryValueEx", queryvalue)
    monkeypatch.setattr(winreg, "DeleteValue", deletevalue)
    return store


def test_login_item_supported_requires_frozen(monkeypatch):
    from ada import login_item

    monkeypatch.setattr(login_item.config, "FROZEN", False)
    assert login_item.supported() is False
    monkeypatch.setattr(login_item.config, "FROZEN", True)
    assert login_item.supported() is True


def test_login_item_enable_writes_run_key(monkeypatch, fake_winreg):
    from ada import login_item

    monkeypatch.setattr(login_item.config, "FROZEN", True)
    monkeypatch.setattr(login_item.sys, "executable", r"C:\Apps\ADA\ADA.exe")
    assert login_item.is_enabled() is False
    assert login_item.set_enabled(True) is True
    assert fake_winreg["ADA"] == r'"C:\Apps\ADA\ADA.exe" --hidden'
    assert login_item.is_enabled() is True


def test_login_item_disable_removes_run_key(monkeypatch, fake_winreg):
    from ada import login_item

    monkeypatch.setattr(login_item.config, "FROZEN", True)
    fake_winreg["ADA"] = r'"x" --hidden'
    assert login_item.set_enabled(False) is False
    assert "ADA" not in fake_winreg
    # İki kez kapatmak hata vermez (değer yoksa sessiz)
    assert login_item.set_enabled(False) is False


def test_login_item_requires_frozen_to_enable(monkeypatch, fake_winreg):
    from ada import login_item

    monkeypatch.setattr(login_item.config, "FROZEN", False)
    with pytest.raises(RuntimeError):
        login_item.set_enabled(True)


# ---------------------------------------------------------------- desktop: tek kopya denetimi

class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return self._body

    def close(self):
        pass


def test_existing_instance_shows_and_returns_true(monkeypatch):
    import json

    from ada import desktop

    posted = []

    def fake_urlopen(arg, timeout=None):
        if hasattr(arg, "full_url"):  # Request nesnesi (/api/instance/show POST)
            posted.append(arg.full_url)
            return _FakeResp(b"")
        return _FakeResp(json.dumps({"app": "ADA", "pid": 999}).encode())

    monkeypatch.setattr(desktop.sys, "argv", ["ada-app"])
    monkeypatch.setattr(desktop.urllib.request, "urlopen", fake_urlopen)
    assert desktop._existing_instance(8765) is True
    assert any("/api/instance/show" in u for u in posted)


def test_wait_ready_accepts_password_401(monkeypatch):
    """Arayüz parolası açıkken /api/status 401 döner; urlopen bunu hata olarak fırlatır. Sunucu yine hazır
    sayılmalı, yoksa pencere hiç açılmaz ve ADA 60 sn sonra kapanır."""
    import io
    import urllib.error

    from ada import desktop

    def fake_urlopen(url, timeout=None):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b""))

    monkeypatch.setattr(desktop.urllib.request, "urlopen", fake_urlopen)
    assert desktop._wait_ready("http://127.0.0.1:1", timeout=2) is True


def test_wait_ready_false_when_server_errors(monkeypatch):
    import io
    import urllib.error

    from ada import desktop

    def fake_urlopen(url, timeout=None):
        raise urllib.error.HTTPError(url, 500, "err", {}, io.BytesIO(b""))

    monkeypatch.setattr(desktop.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    assert desktop._wait_ready("http://127.0.0.1:1", timeout=0.2) is False


def test_existing_instance_hidden_does_not_show(monkeypatch):
    import json

    from ada import desktop

    posted = []

    def fake_urlopen(arg, timeout=None):
        if hasattr(arg, "full_url"):
            posted.append(arg.full_url)
            return _FakeResp(b"")
        return _FakeResp(json.dumps({"app": "ADA", "pid": 1}).encode())

    monkeypatch.setattr(desktop.sys, "argv", ["ada-app", "--hidden"])
    monkeypatch.setattr(desktop.urllib.request, "urlopen", fake_urlopen)
    assert desktop._existing_instance(8765) is True
    assert posted == []  # --hidden: yalnızca çık, pencere açma


def test_existing_instance_none_when_no_server(monkeypatch):
    from ada import desktop

    def fake_urlopen(arg, timeout=None):
        raise OSError("bağlanılamadı")

    monkeypatch.setattr(desktop.urllib.request, "urlopen", fake_urlopen)
    assert desktop._existing_instance(8765) is False


def test_existing_instance_ignores_foreign_server(monkeypatch):
    import json

    from ada import desktop

    def fake_urlopen(arg, timeout=None):
        return _FakeResp(json.dumps({"app": "başka"}).encode())

    monkeypatch.setattr(desktop.urllib.request, "urlopen", fake_urlopen)
    assert desktop._existing_instance(8765) is False


# ---------------------------------------------------------------- izinler (macOS dışı)

def test_open_settings_noop_off_mac():
    from ada import permissions

    assert permissions.open_settings("Erişilebilirlik") == ""


def test_request_all_off_mac_reports_not_needed():
    from ada import permissions

    results = permissions.request_all()
    assert results and results[0]["ok"] is False


# ---------------------------------------------------------------- desktop: dış sayfada geri çubuğu

def test_back_bar_only_outside_ada_ui():
    from ada import desktop

    base = "http://127.0.0.1:8765"
    assert not desktop._needs_back_bar(base + "/", base)
    assert not desktop._needs_back_bar(base + "/?x=1", base)
    assert not desktop._needs_back_bar(base + "/login", base)
    assert not desktop._needs_back_bar("about:blank", base)
    assert desktop._needs_back_bar("https://example.com/", base)
    assert desktop._needs_back_bar(base + "/api/files/outputs/a.png", base)
    js = desktop._back_bar_js(base)
    assert '"http://127.0.0.1:8765/"' in js and "history.back()" in js
