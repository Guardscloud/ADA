"""ADA'nın kendi kendine tekrarlayan görev planlaması."""

from __future__ import annotations

from .. import scheduler
from . import ToolError, tool


@tool(
    "schedule_task",
    "Tekrarlayan veya ileri tarihli bir görev planlar; vakti gelince ADA görevi kendi başına yapar "
    "(ör. 'her sabah 08:30 gelen kutusu özeti', 'her Pazartesi haftalık rapor', "
    "'yarın 15:00 hatırlat'). Görev metnini ADA'ya verilecek tam talimat olarak yazın.",
    {
        "name": {"type": "string", "description": "Kısa ad"},
        "prompt": {"type": "string", "description": "Vakti gelince ADA'ya verilecek görev talimatı"},
        "type": {"type": "string", "enum": ["daily", "interval", "once"]},
        "time": {"type": "string", "description": "daily için HH:MM"},
        "weekdays": {"type": "array", "items": {"type": "integer"}, "description": "daily için 0=Pzt ... 6=Paz; boşsa her gün"},
        "minutes": {"type": "integer", "description": "interval için dakika"},
        "at": {"type": "string", "description": "once için ISO tarih-saat"},
    },
    ["name", "prompt", "type"],
)
def schedule_task(name: str, prompt: str, type: str, time: str = "", weekdays: list[int] | None = None, minutes: int = 0, at: str = "") -> str:
    spec: dict = {"type": type}
    if type == "daily":
        if not time:
            raise ToolError("daily için time (HH:MM) gerekli.")
        spec["time"] = time
        if weekdays:
            spec["weekdays"] = [int(d) for d in weekdays]
    elif type == "interval":
        if minutes < 1:
            raise ToolError("interval için minutes ≥ 1 gerekli.")
        spec["minutes"] = minutes
    elif type == "once":
        if not at:
            raise ToolError("once için at (ISO tarih-saat) gerekli.")
        spec["at"] = at
    try:
        item = scheduler.add(name, prompt, spec)
    except ValueError as e:
        raise ToolError(str(e))
    return f"Planlandı [{item['id']}]: {name} — {scheduler.describe(spec)}; ilk çalışma {item['next_run']}"


@tool("list_scheduled_tasks", "Planlanmış görevleri listeler.", {})
def list_scheduled_tasks() -> str:
    items = scheduler.load()
    if not items:
        return "Planlanmış görev yok."
    return "\n".join(
        f"[{i['id']}] {i['name']} — {scheduler.describe(i['spec'])} — sonraki: {i['next_run']} — talimat: {i['prompt'][:100]}"
        for i in items
    )


@tool("cancel_scheduled_task", "Planlanmış görevi iptal eder.", {"schedule_id": {"type": "string"}}, ["schedule_id"])
def cancel_scheduled_task(schedule_id: str) -> str:
    if not scheduler.remove(schedule_id):
        raise ToolError(f"Bulunamadı: {schedule_id}")
    return f"İptal edildi: {schedule_id}"
