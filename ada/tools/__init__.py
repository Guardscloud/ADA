"""Araç kayıt sistemi.

Her araç bir `Tool` nesnesidir: Claude'a gönderilen tanım + yerelde çalışan handler
(senkron veya async). Yeni bir yetenek eklemek için ilgili modülde `@tool(...)` ile bir
fonksiyon tanımlayın ve modülü aşağıdaki `_MODULES` listesine ekleyin.

`risk` fonksiyonu verilen girdiye bakıp bir onay gerekçesi döndürürse, özerklik ayarına göre
işlem kullanıcı onayına sunulur (bkz. config.AUTONOMY).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .. import config


@dataclass
class ToolResult:
    content: str
    files: list[str] = field(default_factory=list)  # workspace'e göre göreli yollar (indirilebilir)
    is_error: bool = False
    image: bytes | None = None  # PNG; modele görsel olarak iletilir


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Any]
    risk: Callable[[dict], str | None] | None = None  # onay gerekçesi döndürürse onay istenir

    def definition(self) -> dict:
        # Akışlı isteklerde büyük girdiler (rapor metni vb.) üretildikçe gelsin
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "eager_input_streaming": True,
        }


REGISTRY: dict[str, Tool] = {}


def tool(
    name: str,
    description: str,
    properties: dict,
    required: list[str] | None = None,
    risk: Callable[[dict], str | None] | None = None,
):
    def decorator(fn):
        REGISTRY[name] = Tool(
            name=name,
            description=description,
            input_schema={"type": "object", "properties": properties, "required": required or []},
            handler=fn,
            risk=risk,
        )
        return fn

    return decorator


def always(reason: str) -> Callable[[dict], str]:
    """Her çağrıda onay gerektiren araçlar için risk fonksiyonu."""
    return lambda _args: reason


class ToolError(Exception):
    """Kullanıcıya/modele anlamlı bir hata mesajı döndürmek için."""


def safe_path(rel: str, must_exist: bool = False) -> Path:
    """Yolu workspace içine hapseder (yükleme/indirme uçları ve çıktı dosyaları için)."""
    rel = (rel or "").strip()
    candidate = (config.WORKSPACE / rel.lstrip("/")).resolve()
    if not candidate.is_relative_to(config.WORKSPACE):
        raise ToolError(f"Yol çalışma alanının dışına çıkıyor: {rel}")
    if candidate.is_relative_to(config.DATA_DIR):
        raise ToolError("Bu klasör ADA'nın iç verisidir, erişilemez.")
    if must_exist and not candidate.exists():
        raise ToolError(f"Dosya bulunamadı: {rel}")
    return candidate


def resolve_path(p: str, must_exist: bool = False) -> Path:
    """Bilgisayardaki herhangi bir yolu çözer. Göreli yollar workspace'e göredir, ~ desteklenir."""
    raw = (p or "").strip()
    if not raw:
        raise ToolError("Yol boş olamaz.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = config.WORKSPACE / path
    path = path.resolve()
    if path.is_relative_to(config.DATA_DIR):
        raise ToolError("Bu klasör ADA'nın iç verisidir, erişilemez.")
    if must_exist and not path.exists():
        raise ToolError(f"Bulunamadı: {raw}")
    return path


def guard_write(path: Path) -> Path:
    """Rutin dosyaları yalnızca rutin araçları ve arayüzle değişir: bir web sayfasına ya da belgeye gizlenmiş
    talimat, genel dosya araçlarıyla kendine her gün kendiliğinden çalışan bir iş yazamasın."""
    if path.resolve().is_relative_to(config.ROUTINES_DIR.resolve()):
        raise ToolError(
            "Rutin dosyaları bu araçla değiştirilemez; routine_create, routine_update veya routine_delete kullanın."
        )
    if path.resolve() == (config.WORKSPACE / "gozcu.md").resolve():
        raise ToolError("Gözcü kontrol listesini (gozcu.md) yalnızca kullanıcı arayüzden değiştirebilir.")
    return path


def output_path(filename: str, suffix: str) -> Path:
    """outputs/ altında güvenli bir çıktı dosyası yolu üretir. Alt klasör de verilebilir (ör. rutin
    çıktıları için 'rutinler/<rutin>/2026-10-02-ozet'); dışarı çıkan yollar dosya adına indirgenir."""
    sub = PurePosixPath((filename or "").replace("\\", "/").strip().removeprefix("outputs/"))
    parts = [p for p in sub.parts if p not in ("", ".")]
    if not parts or sub.is_absolute() or ".." in parts or any(":" in p for p in parts):
        parts = [Path(filename or "").name or "cikti"]  # eski davranış: yalnızca dosya adı
    name = parts[-1] if parts[-1].lower().endswith(suffix) else parts[-1] + suffix
    path = config.OUTPUTS_DIR.joinpath(*parts[:-1], name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        from .. import safety

        safety.backup(path)  # aynı adla yeniden üretilen çıktının eski hali saklanır
    return path


def rel(path: Path) -> str:
    """Workspace içindeki dosyalar için göreli yol (indirilebilir), diğerleri için mutlak yol."""
    try:
        return path.relative_to(config.WORKSPACE).as_posix()  # Windows'ta da indirme bağlantısı "/" ile
    except ValueError:
        return str(path)


def downloadable(paths: list[Path]) -> list[str]:
    return [rel(p) for p in paths if p.is_relative_to(config.WORKSPACE)]


_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def validate_input(t: Tool, args: Any) -> str | None:
    """Eager streaming'de sunucu girdiyi doğrulamaz; çalıştırmadan önce biz kontrol ederiz."""
    if not isinstance(args, dict):
        return "Girdi bir JSON nesnesi olmalı."
    props = t.input_schema.get("properties", {})
    for key in t.input_schema.get("required", []):
        if key not in args:
            return f"Zorunlu alan eksik: {key}"
    for key, value in args.items():
        if key not in props:
            return f"Bilinmeyen alan: {key}"
        expected = props[key].get("type")
        if expected and not _TYPE_CHECKS[expected](value):
            return f"'{key}' alanı {expected} tipinde olmalı."
    return None


_MODULES = ["files", "office", "code", "system", "web", "email_tools", "calendar_tools", "memory", "agents", "scheduling", "autonomy", "routines", "safety_tools", "pdf", "terminal_tools"]
if config.ENABLE_MAC_APPS:
    _MODULES.append("mac_apps")
    if config.ENABLE_COMPUTER:
        _MODULES.append("ui_access")
for _m in _MODULES:
    importlib.import_module(f"{__name__}.{_m}")
