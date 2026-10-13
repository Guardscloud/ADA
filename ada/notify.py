"""Masaüstü bildirimleri. Engellemez; hata olursa sessizce geçer.

macOS: osascript `display notification`.
Windows: WinRT ToastGeneric; Windows PowerShell 5.1 ile -EncodedCommand (UTF-16LE base64)
çalıştırılır (Türkçe metin için sağlam). Ek bağımlılık gerektirmez.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

from . import config

# Paketlenmemiş uygulamalar için AppUserModelID (bildirimde "ADA" adıyla görünür)
_AUMID = "DuckTeam.ADA"
# Kayıt başarısız olursa PowerShell'in iyi bilinen AUMID'i (bildirim yine gösterilir)
_PS_AUMID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
CREATE_NO_WINDOW = 0x08000000

_registered: bool | None = None  # AUMID kaydı süreç başına bir kez denenir


def _mac(title: str, message: str) -> None:
    from .tools.system import _q

    subprocess.Popen(
        ["osascript", "-e", f'display notification "{_q(message[:180])}" with title "{_q(title[:60])}"'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _icon_uri() -> str | None:
    """Paketteki bir PNG'yi (favicon/ikon) bildirim simgesi yap; yoksa None."""
    base = Path(__file__).resolve().parent
    candidates = list((base / "static" / "dist" / "assets").glob("favicon*.png"))
    candidates.append(base.parent / "assets" / "icon.png")
    for p in candidates:
        try:
            if p.is_file():
                return p.as_uri()
        except OSError:
            continue
    return None


def _register_aumid() -> str:
    """HKCU altında AppUserModelID kaydeder (süreç başına bir kez) ve AUMID'i döner.
    Herhangi bir hata olursa PowerShell'in iyi bilinen AUMID'ine düşer."""
    global _registered
    if _registered:
        return _AUMID
    if _registered is False:
        return _PS_AUMID
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{_AUMID}") as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "ADA")
            icon = _icon_uri()
            if icon:
                winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon)
        _registered = True
        return _AUMID
    except Exception:  # noqa: BLE001
        _registered = False
        return _PS_AUMID


def _toast_script(title: str, message: str, aumid: str) -> str:
    """Verilen AUMID ile ToastGeneric bildirimi gösteren PowerShell betiği."""
    # XML özel karakterlerinin yanında tırnakları da varlık olarak kaçır: XML tek tırnakla sarılı bir
    # PowerShell dizesine gömülür, ham ' veya " o dizeyi bozardı (ör. "ADA'nın raporu").
    quotes = {"'": "&apos;", '"': "&quot;"}
    t = escape(title[:60], quotes)
    m = escape(message[:200], quotes)
    return (
        "$ErrorActionPreference='Stop';"
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.UI.Notifications.ToastNotification,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime]|Out-Null;"
        f"$xml='<toast><visual><binding template=\"ToastGeneric\"><text>{t}</text><text>{m}</text></binding></visual></toast>';"
        "$doc=New-Object Windows.Data.Xml.Dom.XmlDocument;$doc.LoadXml($xml);"
        "$toast=New-Object Windows.UI.Notifications.ToastNotification -ArgumentList $doc;"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{aumid}').Show($toast)"
    )


def encoded_command(title: str, message: str, aumid: str | None = None) -> str:
    """Betiği Windows PowerShell'in -EncodedCommand beklediği biçime (UTF-16LE base64) çevirir."""
    script = _toast_script(title, message, aumid or _PS_AUMID)
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _win(title: str, message: str) -> None:
    encoded = encoded_command(title, message, _register_aumid())
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )


def notify(title: str, message: str) -> None:
    if not config.NOTIFICATIONS:
        return
    try:
        if config.IS_MAC:
            _mac(title, message)
        elif config.IS_WIN:
            _win(title, message)
    except Exception:  # noqa: BLE001
        pass
