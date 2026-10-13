"""Konuşmaların JSON dosyalarında saklanması.

`messages` API'ye giden ham geçmiştir ve yalnızca sona ekleme yapılır (düşünce blokları
geçmiş düzenlenirse geçersiz olur). `ui` ise arayüzde yeniden gösterim için sade kayıttır.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

from . import config


@dataclass
class Conversation:
    id: str
    title: str = "Yeni görev"
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated: str = ""
    messages: list[dict] = field(default_factory=list)
    ui: list[dict] = field(default_factory=list)
    # messages biçimi sağlayıcıya özeldir; farklı sağlayıcıyla devam edilemez
    provider: str = field(default_factory=lambda: config.PROVIDER)
    engine: str = ""  # ADA-AGI: bu görevin son turunu yapan motor (süreklilik için)
    kind: str = ""  # ADA-AGI: son turun görev türü ("tekrar dene" gibi devam mesajları onu sürdürür)

    @property
    def path(self):
        return config.CONVERSATIONS_DIR / f"{self.id}.json"

    def save(self) -> None:
        self.updated = datetime.now().isoformat(timespec="seconds")
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def create() -> Conversation:
    conv = Conversation(id=uuid.uuid4().hex[:12])
    conv.save()
    return conv


def _valid_id(conv_id: str) -> bool:
    return conv_id.isalnum() and len(conv_id) <= 32


def load(conv_id: str) -> Conversation | None:
    if not _valid_id(conv_id):
        return None
    path = config.CONVERSATIONS_DIR / f"{conv_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("provider", "anthropic")  # sağlayıcı alanından önceki kayıtlar
    return Conversation(**data)


def delete(conv_id: str) -> None:
    if _valid_id(conv_id):
        (config.CONVERSATIONS_DIR / f"{conv_id}.json").unlink(missing_ok=True)


def list_all() -> list[dict]:
    items = []
    for p in config.CONVERSATIONS_DIR.glob("*.json"):
        data = json.loads(p.read_text(encoding="utf-8"))
        items.append({"id": data["id"], "title": data["title"], "updated": data.get("updated") or data["created"]})
    return sorted(items, key=lambda x: x["updated"], reverse=True)
