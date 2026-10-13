"""Tehlikeli işlemler için kullanıcı onayı ve ajan döngüsüne yan-kanal olay iletimi.

`SIDE` bağlam değişkeni, araç çalıştırılırken olayların (onay isteği, alt ajan adımları,
ekran görüntüleri) ana akışa ulaşmasını sağlar. Araç görevleri oluşturulurken kopyalanan
bağlam sayesinde alt ajanlar da aynı kanalı kullanır.
"""

from __future__ import annotations

import asyncio
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field

from . import config

SIDE: ContextVar[asyncio.Queue | None] = ContextVar("ada_side", default=None)


def emit(event: dict) -> None:
    queue = SIDE.get()
    if queue is not None:
        queue.put_nowait(event)


@dataclass
class Approval:
    id: str
    title: str
    detail: str
    future: asyncio.Future = field(repr=False)
    key: str | None = None  # "Her zaman izin ver" anahtarı


PENDING: dict[str, Approval] = {}

# "Her zaman izin ver" ile kalıcı olarak onaylanan işlem türleri (araç + risk kategorisi)
ALLOW_FILE = config.DATA_DIR / "always_allow.json"


def allow_key(tool: str, reason: str) -> str:
    """Onay anahtarı: araç + risk kategorisi (yol/ayrıntı hariç), ör. 'run_shell|Komut yönetici (sudo) yetkisi içeriyor.'"""
    return f"{tool}|{reason.split(':')[0].strip()}"


def allowed() -> dict[str, str]:
    import json

    if ALLOW_FILE.exists():
        try:
            return json.loads(ALLOW_FILE.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def is_always_allowed(key: str) -> bool:
    return key in allowed()


def set_always_allowed(key: str, on: bool) -> None:
    import json
    from datetime import datetime

    data = allowed()
    if on:
        data[key] = datetime.now().isoformat(timespec="seconds")
    else:
        data.pop(key, None)
    ALLOW_FILE.parent.mkdir(parents=True, exist_ok=True)
    ALLOW_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def request(title: str, detail: str, key: str | None = None) -> bool:
    """Kullanıcıdan onay ister; arayüzde butonlar belirir. Zaman aşımında reddedilmiş sayılır.

    `key` verilirse kart "Her zaman izin ver" seçeneği sunar; seçilirse bu tür bir daha sorulmaz.
    """
    if key and is_always_allowed(key):
        return True
    loop = asyncio.get_running_loop()
    item = Approval(id=uuid.uuid4().hex[:10], title=title, detail=detail, future=loop.create_future(), key=key)
    PENDING[item.id] = item
    emit({"type": "approval", "id": item.id, "title": title, "detail": detail, "can_always": bool(key)})
    try:
        return await asyncio.wait_for(item.future, timeout=config.APPROVAL_TIMEOUT)
    except asyncio.TimeoutError:
        return False
    finally:
        PENDING.pop(item.id, None)
        emit({"type": "approval_resolved", "id": item.id})


def resolve(approval_id: str, approved: bool, always: bool = False) -> bool:
    item = PENDING.get(approval_id)
    if item is None or item.future.done():
        return False
    if approved and always and item.key:
        set_always_allowed(item.key, True)
    item.future.set_result(approved)
    return True
