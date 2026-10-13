"""Zamanlanmış görevler: belirli saatte/aralıkta ADA'ya otomatik iş verir.

Kayıtlar workspace/.ada/schedules.json içinde tutulur. Sunucu açılınca `Scheduler.loop`
başlar; vakti gelen görev için yeni bir konuşma açılır ve görev normal bir mesaj gibi çalışır.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from . import config

WEEKDAYS = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


def load() -> list[dict]:
    if config.SCHEDULES_FILE.exists():
        return json.loads(config.SCHEDULES_FILE.read_text(encoding="utf-8"))
    return []


def save(items: list[dict]) -> None:
    config.SCHEDULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.SCHEDULES_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def compute_next(spec: dict, after: datetime) -> datetime | None:
    kind = spec.get("type")
    if kind == "interval":
        return after + timedelta(minutes=max(int(spec.get("minutes", 60)), 1))
    if kind == "once":
        at = datetime.fromisoformat(spec["at"])
        return at if at > after else None
    if kind == "daily":
        hour, minute = (int(x) for x in spec.get("time", "09:00").split(":"))
        weekdays = spec.get("weekdays") or list(range(7))
        candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        for _ in range(8):
            if candidate > after and candidate.weekday() in weekdays:
                return candidate
            candidate += timedelta(days=1)
    return None


def describe(spec: dict) -> str:
    kind = spec.get("type")
    if kind == "interval":
        return f"her {spec.get('minutes')} dakikada"
    if kind == "once":
        return f"bir kez, {spec.get('at')}"
    if kind == "daily":
        days = spec.get("weekdays")
        label = "her gün" if not days or len(days) == 7 else ", ".join(WEEKDAYS[d] for d in sorted(days))
        return f"{label} saat {spec.get('time', '09:00')}"
    return json.dumps(spec)


def add(name: str, prompt: str, spec: dict) -> dict:
    nxt = compute_next(spec, datetime.now())
    if nxt is None:
        raise ValueError("Bu zamanlama için gelecekte bir çalışma zamanı yok.")
    item = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "prompt": prompt,
        "spec": spec,
        "next_run": nxt.isoformat(timespec="minutes"),
        "last_run": None,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    items = load()
    items.append(item)
    save(items)
    return item


def remove(schedule_id: str) -> bool:
    items = load()
    kept = [i for i in items if i["id"] != schedule_id]
    if len(kept) == len(items):
        return False
    save(kept)
    return True


class Scheduler:
    def __init__(self, runner: Callable[[str, str], Awaitable[None]]) -> None:
        self.runner = runner  # (başlık, görev metni) -> görevi başlatır

    async def loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001
                print(f"[zamanlayıcı] hata: {e}")
            await asyncio.sleep(30)

    async def tick(self) -> None:
        from . import safety

        if safety.is_halted():
            return
        now = datetime.now()
        items = load()
        changed = False
        for item in items:
            if datetime.fromisoformat(item["next_run"]) > now:
                continue
            changed = True
            item["last_run"] = now.isoformat(timespec="seconds")
            nxt = compute_next(item["spec"], now)
            item["next_run"] = nxt.isoformat(timespec="minutes") if nxt else None
            await self.runner(f"⏰ {item['name']}", item["prompt"])
        if changed:
            save([i for i in items if i["next_run"]])
