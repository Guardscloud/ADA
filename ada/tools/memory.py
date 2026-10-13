"""Konuşmalar arası kalıcı hafıza: kullanıcı tercihleri, şirket bilgileri, kişiler."""

from __future__ import annotations

import json
from datetime import datetime

from .. import config
from . import tool


def _load() -> dict:
    if config.MEMORY_FILE.exists():
        return json.loads(config.MEMORY_FILE.read_text(encoding="utf-8"))
    return {}


def _save(data: dict) -> None:
    config.MEMORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@tool(
    "memory_save",
    "Gelecekteki görevlerde işe yarayacak kalıcı bir bilgiyi kaydeder (ör. kullanıcının "
    "şirketi, unvanı, yazışma üslubu tercihi, sık kullanılan kişiler). Aynı anahtar varsa günceller.",
    {
        "key": {"type": "string", "description": "Kısa anahtar, ör. 'sirket_bilgisi'"},
        "content": {"type": "string"},
    },
    ["key", "content"],
)
def memory_save(key: str, content: str) -> str:
    data = _load()
    data[key] = {"content": content, "updated": datetime.now().isoformat(timespec="seconds")}
    _save(data)
    return f"Hafızaya kaydedildi: {key}"


@tool(
    "memory_search",
    "Kalıcı hafızadaki bilgileri getirir. Sorgu boşsa tüm kayıtları listeler. Yeni bir göreve "
    "başlarken kullanıcıyla ilgili bilinenleri kontrol etmek için kullanın.",
    {"query": {"type": "string"}},
)
def memory_search(query: str = "") -> str:
    data = _load()
    q = query.casefold()
    hits = [
        f"[{k}] ({v['updated']}): {v['content']}"
        for k, v in data.items()
        if not q or q in k.casefold() or q in v["content"].casefold()
    ]
    return "\n".join(hits) or "Hafızada kayıt yok."


@tool(
    "memory_delete",
    "Hafızadan bir kaydı siler (kullanıcı isterse veya bilgi yanlışsa).",
    {"key": {"type": "string"}},
    ["key"],
)
def memory_delete(key: str) -> str:
    data = _load()
    if data.pop(key, None) is None:
        return f"Kayıt bulunamadı: {key}"
    _save(data)
    return f"Silindi: {key}"
