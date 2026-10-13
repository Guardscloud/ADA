"""macOS izinlerini kullanıcı başındayken toplu iste (arka plan görevlerinde izin penceresinde takılmamak için).

Her uygulamaya zararsız bir AppleScript gönderilir; ilk seferde macOS "ADA, X'i denetlemek istiyor"
penceresini açar. Ekran kaydı izni için bir ekran görüntüsü denenir.
"""

from __future__ import annotations

from . import config

# İzin adı → Sistem Ayarları'ndaki gizlilik sayfası
PANES = {"Erişilebilirlik": "Privacy_Accessibility", "Ekran kaydı": "Privacy_ScreenCapture"}


def open_settings(name: str) -> str:
    """İlgili Sistem Ayarları gizlilik sayfasını açar (otomasyon izinleri için Otomasyon sayfası).
    macOS dışında yapacak bir şey yoktur (izinler yalnızca macOS'ta gereklidir); boş döner."""
    if not config.IS_MAC:
        return ""
    import subprocess

    pane = PANES.get(name, "Privacy_Automation")
    subprocess.Popen(["open", f"x-apple.systempreferences:com.apple.preference.security?{pane}"])
    return pane

CHECKS = [
    ("Mail", 'tell application "Mail" to get name'),
    ("Takvim", 'tell application "Calendar" to get name'),
    ("Anımsatıcılar", 'tell application "Reminders" to get name'),
    ("Notlar", 'tell application "Notes" to get name'),
    ("Kişiler", 'tell application "Contacts" to get name'),
    ("Sistem Olayları", 'tell application "System Events" to get name of every login item'),
    ("Finder", 'tell application "Finder" to get name'),
]


def request_all(timeout: int = 180) -> list[dict]:
    """Her izin için {ad, ok, mesaj}. Kullanıcı pencereleri onaylarken bekler."""
    if not config.IS_MAC:
        return [{"name": "macOS", "ok": False, "message": "Yalnızca macOS'ta gereklidir."}]
    from .tools import ToolError
    from .tools.mac_apps import run_osascript

    results = []
    for name, script in CHECKS:
        try:
            run_osascript(script, timeout=timeout)
            results.append({"name": name, "ok": True, "message": "izin var"})
        except ToolError as e:
            results.append({"name": name, "ok": False, "message": str(e)[:200]})
    try:
        import ApplicationServices as AS

        trusted = bool(AS.AXIsProcessTrusted())
        results.append({"name": "Erişilebilirlik", "ok": trusted,
                        "message": "izin var" if trusted else "Sistem Ayarları → Gizlilik ve Güvenlik → Erişilebilirlik → ADA'yı açın"})
    except ImportError:
        pass
    try:
        from .tools import computer

        shot = computer.execute("screenshot", {})
        results.append({"name": "Ekran kaydı", "ok": not shot.is_error, "message": "izin var" if not shot.is_error else shot.content[:200]})
    except Exception as e:  # noqa: BLE001
        results.append({"name": "Ekran kaydı", "ok": False, "message": str(e)[:200]})
    return results
