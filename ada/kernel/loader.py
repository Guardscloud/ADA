"""Eklenti yükleyici: kernel/plugins klasöründeki .py dosyalarını bir kez, sırayla yükler."""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from .. import config


@dataclass
class PluginInfo:
    name: str
    path: str
    ok: bool
    error: str = ""
    tools: tuple[str, ...] = ()


_LOADED: list[PluginInfo] = []
_DONE = False

README = """ADA kernel eklentileri
=====================
Bu klasöre koyduğunuz her .py dosyası ADA açılırken yüklenir (alfabetik sırayla; _ ile başlayanlar atlanır).
Araç ekleme, yerleşik bir aracı değiştirme, araç çağrılarını süzme ve sistem talimatına metin ekleme
yapabilirsiniz. Örnek: _example.py dosyasını kopyalayıp adını değiştirin (ör. my_plugin.py) ve ADA'yı yeniden başlatın.
Belgeler: https://github.com/Guardscloud/ADA/blob/main/docs/KERNEL.md
"""

EXAMPLE = '''"""Örnek ADA eklentisi. Etkinleştirmek için baştaki _ olmadan kopyalayın: my_plugin.py"""
from ada import kernel


@kernel.tool("word_count", "Bir metindeki kelimeleri sayar.", {"text": {"type": "string"}}, required=["text"])
def word_count(text: str) -> str:
    return f"{len(text.split())} kelime"


@kernel.before_tool
def no_format(name, args):
    if name == "run_shell" and "format " in str(args.get("command", "")).lower():
        return "Eklenti: disk biçimlendirme komutları engellendi."


@kernel.system
def house_rules() -> str:
    return "Eklenti kuralı: rapor dosya adlarına tarih ekle (YYYY-AA-GG)."
'''


def plugin_dir() -> Path | None:
    env = os.getenv("ADA_KERNEL_PLUGINS", "").strip()
    if env.lower() in ("off", "0", "false", "no"):
        return None
    return Path(env).expanduser() if env else config.HOME_DIR / "kernel" / "plugins"


def _scaffold(d: Path) -> None:
    try:
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "README.txt").exists():
            (d / "README.txt").write_text(README, encoding="utf-8")
        if not (d / "_example.py").exists():
            (d / "_example.py").write_text(EXAMPLE, encoding="utf-8")
    except OSError:
        pass


def load_plugins(force: bool = False) -> list[PluginInfo]:
    """Eklentileri yükler (süreç başına bir kez). Hata veren eklenti atlanır ve listede ok=False görünür."""
    global _DONE
    if _DONE and not force:
        return list(_LOADED)
    _DONE = True
    _LOADED.clear()
    d = plugin_dir()
    if d is None:
        return []
    _scaffold(d)
    from ..tools import REGISTRY

    for path in sorted(d.glob("*.py")):
        if path.name.startswith("_"):
            continue
        before = {k: id(v) for k, v in REGISTRY.items()}
        mod_name = f"ada_kernel_plugin_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
        except Exception as e:  # noqa: BLE001
            sys.modules.pop(mod_name, None)
            tb = traceback.format_exception_only(type(e), e)[-1].strip()
            _LOADED.append(PluginInfo(path.stem, str(path), False, tb))
            _log(path.stem, False, tb)
            continue
        added = tuple(sorted(k for k, v in REGISTRY.items() if before.get(k) != id(v)))
        _LOADED.append(PluginInfo(path.stem, str(path), True, tools=added))
        _log(path.stem, True, ", ".join(added))
    return list(_LOADED)


def loaded() -> list[PluginInfo]:
    return list(_LOADED)


def _log(name: str, ok: bool, detail: str) -> None:
    try:
        from .. import safety

        safety.log("kernel_eklenti", {"eklenti": name}, ok=ok, result=detail[:300])
    except Exception:  # noqa: BLE001
        pass
