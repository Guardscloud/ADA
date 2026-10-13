"""Takvim daveti (.ics) üretimi - Outlook, Google Takvim ve Apple Takvim ile açılır."""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timezone as dt_timezone

from . import ToolError, ToolResult, output_path, rel, tool


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _dt(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%Y%m%dT%H%M%S")
    except ValueError:
        raise ToolError(f"Tarih ISO formatında olmalı (ör. 2026-10-05T14:00): {value}")


@tool(
    "create_calendar_event",
    "Toplantı/etkinlik için .ics takvim daveti dosyası oluşturur. Kullanıcı dosyayı açarak "
    "takvimine ekler ve katılımcılara gönderebilir.",
    {
        "filename": {"type": "string"},
        "title": {"type": "string"},
        "start": {"type": "string", "description": "Yerel saat, ISO format: 2026-10-05T14:00"},
        "end": {"type": "string", "description": "Yerel saat, ISO format: 2026-10-05T15:00"},
        "timezone": {"type": "string", "description": "IANA saat dilimi, varsayılan Europe/Istanbul"},
        "location": {"type": "string"},
        "description": {"type": "string", "description": "Gündem / açıklama"},
        "attendees": {"type": "array", "items": {"type": "string"}, "description": "E-posta adresleri"},
    },
    ["filename", "title", "start", "end"],
)
def create_calendar_event(
    filename: str,
    title: str,
    start: str,
    end: str,
    timezone: str = "Europe/Istanbul",
    location: str = "",
    description: str = "",
    attendees: list[str] | None = None,
) -> ToolResult:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ADA-AGI//TR",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uuid.uuid4()}@ada-agi",
        f"DTSTAMP:{datetime.now(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART;TZID={timezone}:{_dt(start)}",
        f"DTEND;TZID={timezone}:{_dt(end)}",
        f"SUMMARY:{_esc(title)}",
    ]
    if location:
        lines.append(f"LOCATION:{_esc(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_esc(description)}")
    for a in attendees or []:
        lines.append(f"ATTENDEE;ROLE=REQ-PARTICIPANT;RSVP=TRUE:mailto:{a.strip()}")
    lines += ["END:VEVENT", "END:VCALENDAR"]

    path = output_path(filename, ".ics")
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return ToolResult(f"Takvim daveti oluşturuldu: {rel(path)}", files=[rel(path)])
