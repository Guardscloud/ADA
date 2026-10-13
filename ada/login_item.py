"""Bilgisayar açılınca ADA'yı başlatma.

- macOS: Oturum Açma Öğeleri (yalnızca paketlenmiş ADA.app). İlk kullanımda macOS, ADA'nın
  "Sistem Olayları"nı denetlemesi için izin ister.
- Windows: HKCU\\...\\CurrentVersion\\Run altında "ADA" değeri (yalnızca paketlenmiş ADA.exe).
  Uygulama `--hidden` ile küçültülmüş başlar.

Her iki platformda da yalnızca paketlenmiş uygulama için anlamlıdır; geliştirme modunda desteklenmez.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import config

NAME = "ADA"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def app_bundle() -> Path | None:
    if not (config.FROZEN and config.IS_MAC):
        return None
    exe = Path(sys.executable).resolve()  # .../ADA.app/Contents/MacOS/ADA
    bundle = exe.parents[2]
    return bundle if bundle.suffix == ".app" else None


def supported() -> bool:
    """macOS'ta kurulu ADA.app, Windows'ta paketlenmiş ADA.exe için true."""
    if config.IS_MAC:
        return app_bundle() is not None
    if config.IS_WIN:
        return config.FROZEN
    return False


def _osa(script: str) -> str:
    from .tools.system import run_osascript

    return run_osascript(script, timeout=30)


def _win_command() -> str:
    """Açılışta çalıştırılacak komut: \"<exe>\" --hidden."""
    return f'"{sys.executable}" --hidden'


def is_enabled() -> bool:
    if config.IS_WIN:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, NAME)
            return True
        except FileNotFoundError:
            return False
    if not config.IS_MAC:
        return False
    names = _osa('tell application "System Events" to get the name of every login item')
    return NAME in [n.strip() for n in names.split(",")]


def set_enabled(enabled: bool) -> bool:
    if config.IS_WIN:
        if not config.FROZEN:
            raise RuntimeError("Bu seçenek yalnızca kurulu ADA.exe için kullanılabilir.")
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, _win_command())
            else:
                try:
                    winreg.DeleteValue(key, NAME)
                except FileNotFoundError:
                    pass
        return is_enabled()

    bundle = app_bundle()
    if bundle is None:
        raise RuntimeError("Bu seçenek yalnızca Uygulamalar klasörüne kurulu ADA.app için kullanılabilir.")
    from .tools.system import _q

    if enabled and not is_enabled():
        _osa(f'tell application "System Events" to make login item at end with properties {{path:"{_q(str(bundle))}", hidden:true}}')
    elif not enabled and is_enabled():
        _osa(f'tell application "System Events" to delete login item "{NAME}"')
    return is_enabled()
