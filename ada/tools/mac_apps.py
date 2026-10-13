"""macOS yerel uygulamaları (AppleScript ile): Mail, Takvim, Anımsatıcılar, Notlar, Kişiler.

Mac'te yapılandırılmış tüm hesaplarla çalışır; OAuth veya şifre gerektirmez.
"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timedelta

from . import ToolError, tool
from .system import _q
from .system import run_osascript as _raw_osascript

SEP = "\u241f"  # alan ayırıcı (metinlerde geçmez)
APPS = ("Mail", "Calendar", "Reminders", "Notes", "Contacts")


def _is_running(app: str) -> bool:
    return subprocess.run(["pgrep", "-xq", app], check=False).returncode == 0


def ensure_running(app: str, timeout: float = 90) -> None:
    """Uygulama kapalıysa arka planda (öne getirmeden) açar ve betiklere yanıt verene kadar bekler.

    Kapalı bir uygulamaya gönderilen AppleScript "Uygulama çalışmıyor (-600)" hatası verebilir.
    """
    if _is_running(app):
        return
    subprocess.run(["open", "-gja", app], check=False)
    end = time.time() + timeout
    while time.time() < end:
        if _is_running(app):
            try:
                _raw_osascript(f'tell application "{app}" to get name', timeout=20)
                return
            except ToolError:
                pass
        time.sleep(1)
    raise ToolError(f"{app} uygulaması {int(timeout)} saniyede açılamadı.")


def run_osascript(script: str, timeout: int = 120) -> str:
    """Betikteki uygulamaları gerekirse önce başlatır; -600 hatasında bir kez daha dener."""
    apps = [a for a in APPS if f'application "{a}"' in script]
    for app in apps:
        ensure_running(app)
    try:
        return _raw_osascript(script, timeout=timeout)
    except ToolError as e:
        if "-600" not in str(e) or not apps:
            raise
        time.sleep(3)
        return _raw_osascript(script, timeout=timeout)


def _date_expr(var: str, dt: datetime) -> str:
    """Yerel ayardan bağımsız AppleScript tarih kurulumu."""
    return (
        f"set {var} to current date\n"
        f"set year of {var} to {dt.year}\nset month of {var} to {dt.month}\nset day of {var} to {dt.day}\n"
        f"set hours of {var} to {dt.hour}\nset minutes of {var} to {dt.minute}\nset seconds of {var} to 0\n"
    )


def _parse(value: str, name: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ToolError(f"{name} ISO biçiminde olmalı (ör. 2026-10-05T14:00): {value}")


# ---------------------------------------------------------------- Mail

@tool(
    "mail_list_inbox",
    "Mail uygulamasındaki gelen kutusunu listeler (tüm hesaplar). Her satır: id, tarih, gönderen, konu, okundu mu.",
    {
        "limit": {"type": "integer", "description": "Kaç e-posta (varsayılan 20, en fazla 100)"},
        "unread_only": {"type": "boolean"},
    },
)
def mail_list_inbox(limit: int = 20, unread_only: bool = False) -> str:
    limit = min(max(limit, 1), 100)
    source = "(messages of inbox whose read status is false)" if unread_only else "messages of inbox"
    script = f"""
tell application "Mail"
    set msgs to {source}
    set n to count of msgs
    if n > {limit} then set n to {limit}
    set out to ""
    repeat with i from 1 to n
        set m to item i of msgs
        set out to out & (id of m) & "{SEP}" & (date received of m as string) & "{SEP}" & (sender of m) & "{SEP}" & (subject of m) & "{SEP}" & (read status of m) & linefeed
    end repeat
    return out
end tell"""
    raw = run_osascript(script, timeout=180)
    rows = []
    for line in raw.splitlines():
        parts = line.split(SEP)
        if len(parts) == 5:
            rows.append(f"id {parts[0]} | {parts[1]} | {parts[2]} | {parts[3]} | {'okundu' if parts[4] == 'true' else 'OKUNMAMIŞ'}")
    return "\n".join(rows) or "E-posta bulunamadı."


@tool(
    "mail_read",
    "Mail uygulamasında id'si verilen e-postanın tam içeriğini okur.",
    {"message_id": {"type": "integer"}},
    ["message_id"],
)
def mail_read(message_id: int) -> str:
    script = f"""
tell application "Mail"
    set m to first message of inbox whose id is {int(message_id)}
    set atts to ""
    repeat with a in mail attachments of m
        set atts to atts & (name of a) & ", "
    end repeat
    return "Kimden: " & (sender of m) & linefeed & "Kime: " & (address of to recipients of m as string) & linefeed & "Tarih: " & (date received of m as string) & linefeed & "Konu: " & (subject of m) & linefeed & "Ekler: " & atts & linefeed & linefeed & (content of m)
end tell"""
    return run_osascript(script, timeout=120)[:40_000]


@tool(
    "mail_create_draft",
    "Mail uygulamasında yeni bir e-posta taslağı oluşturur ve pencerede açar (göndermez). "
    "Kullanıcı kontrol edip gönderir. Ek dosya eklenebilir.",
    {
        "to": {"type": "array", "items": {"type": "string"}},
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "cc": {"type": "array", "items": {"type": "string"}},
        "attachments": {"type": "array", "items": {"type": "string"}, "description": "Dosya yolları"},
    },
    ["to", "subject", "body"],
)
def mail_create_draft(to: list[str], subject: str, body: str, cc: list[str] | None = None, attachments: list[str] | None = None) -> str:
    return _compose(to, subject, body, cc or [], attachments or [], send=False)


@tool(
    "mail_send",
    "Mail uygulamasıyla e-posta GÖNDERİR. Geri alınamaz; yalnızca kullanıcı açıkça göndermeni "
    "istediyse kullan, aksi halde mail_create_draft ile taslak bırak.",
    {
        "to": {"type": "array", "items": {"type": "string"}},
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "cc": {"type": "array", "items": {"type": "string"}},
        "attachments": {"type": "array", "items": {"type": "string"}},
    },
    ["to", "subject", "body"],
    risk=lambda a: f"E-posta gönderilecek: {', '.join(a.get('to', []))} — {a.get('subject', '')}",
)
def mail_send(to: list[str], subject: str, body: str, cc: list[str] | None = None, attachments: list[str] | None = None) -> str:
    return _compose(to, subject, body, cc or [], attachments or [], send=True)


def _compose(to, subject, body, cc, attachments, send: bool) -> str:
    from . import resolve_path

    lines = [
        'tell application "Mail"',
        f'    set msg to make new outgoing message with properties {{subject:"{_q(subject)}", content:"{_q(body)}", visible:{"false" if send else "true"}}}',
        "    tell msg",
    ]
    for addr in to:
        lines.append(f'        make new to recipient at end of to recipients with properties {{address:"{_q(addr)}"}}')
    for addr in cc:
        lines.append(f'        make new cc recipient at end of cc recipients with properties {{address:"{_q(addr)}"}}')
    for path in attachments:
        p = resolve_path(path, must_exist=True)
        lines.append(f'        make new attachment with properties {{file name:POSIX file "{_q(str(p))}"}} at after the last paragraph')
    lines.append("    end tell")
    if send:
        lines.append("    send msg")
    else:
        lines.append("    activate")
    lines.append("end tell")
    run_osascript("\n".join(lines), timeout=120)
    return ("Gönderildi: " if send else "Taslak Mail uygulamasında açıldı: ") + f"{', '.join(to)} — {subject}"


# ---------------------------------------------------------------- Takvim

@tool(
    "calendar_list_events",
    "Takvim uygulamasındaki etkinlikleri listeler (tüm takvimler).",
    {
        "days": {"type": "integer", "description": "Bugünden itibaren kaç gün (varsayılan 7)"},
        "start": {"type": "string", "description": "İsteğe bağlı başlangıç, ISO (varsayılan: şimdi)"},
    },
)
def calendar_list_events(days: int = 7, start: str = "") -> str:
    d1 = _parse(start, "start") if start else datetime.now().replace(hour=0, minute=0)
    d2 = d1 + timedelta(days=max(days, 1))
    script = f"""
{_date_expr("d1", d1)}{_date_expr("d2", d2)}
tell application "Calendar"
    set out to ""
    repeat with c in calendars
        set evs to (every event of c whose start date ≥ d1 and start date ≤ d2)
        repeat with e in evs
            set out to out & (name of c) & "{SEP}" & (start date of e as string) & "{SEP}" & (end date of e as string) & "{SEP}" & (summary of e) & "{SEP}" & (location of e) & linefeed
        end repeat
    end repeat
    return out
end tell"""
    raw = run_osascript(script, timeout=180)
    rows = []
    for line in raw.splitlines():
        p = line.split(SEP)
        if len(p) == 5:
            loc = f" @ {p[4]}" if p[4] and p[4] != "missing value" else ""
            rows.append(f"[{p[0]}] {p[1]} → {p[2]} | {p[3]}{loc}")
    return "\n".join(rows) or f"{d1:%Y-%m-%d} - {d2:%Y-%m-%d} arasında etkinlik yok."


@tool(
    "calendar_create_event",
    "Takvim uygulamasına etkinlik ekler (Mac'teki iCloud/Google/Exchange takvimleriyle senkronize olur). "
    "Katılımcı e-postaları verilirse davet olarak eklenir.",
    {
        "title": {"type": "string"},
        "start": {"type": "string", "description": "ISO, ör. 2026-10-05T14:00"},
        "end": {"type": "string", "description": "ISO, ör. 2026-10-05T15:00"},
        "calendar": {"type": "string", "description": "Takvim adı (varsayılan: ilk takvim)"},
        "location": {"type": "string"},
        "notes": {"type": "string"},
        "attendees": {"type": "array", "items": {"type": "string"}},
        "alarm_minutes": {"type": "integer", "description": "Kaç dakika önce hatırlat"},
    },
    ["title", "start", "end"],
    risk=lambda a: f"Takvime eklenecek: {a.get('title')} ({a.get('start')})",
)
def calendar_create_event(
    title: str, start: str, end: str, calendar: str = "", location: str = "", notes: str = "",
    attendees: list[str] | None = None, alarm_minutes: int = 0,
) -> str:
    s, e = _parse(start, "start"), _parse(end, "end")
    if e <= s:
        raise ToolError("Bitiş, başlangıçtan sonra olmalı.")
    cal = f'calendar "{_q(calendar)}"' if calendar else "first calendar whose writable is true"
    lines = [_date_expr("d1", s), _date_expr("d2", e), 'tell application "Calendar"', f"    set c to {cal}",
             f'    set ev to make new event at end of events of c with properties {{summary:"{_q(title)}", start date:d1, end date:d2, location:"{_q(location)}", description:"{_q(notes)}"}}']
    for a in attendees or []:
        lines.append(f'    make new attendee at end of attendees of ev with properties {{email:"{_q(a)}"}}')
    if alarm_minutes:
        lines.append(f"    make new display alarm at end of display alarms of ev with properties {{trigger interval:-{int(alarm_minutes)}}}")
    lines += ["    return name of c", "end tell"]
    cal_name = run_osascript("\n".join(lines), timeout=120)
    return f"Etkinlik eklendi ({cal_name}): {title} {s:%Y-%m-%d %H:%M}–{e:%H:%M}"


# ---------------------------------------------------------------- Anımsatıcılar / Notlar / Kişiler

@tool(
    "reminders_add",
    "Anımsatıcılar uygulamasına görev ekler.",
    {
        "title": {"type": "string"},
        "due": {"type": "string", "description": "ISO tarih-saat (isteğe bağlı)"},
        "notes": {"type": "string"},
        "list_name": {"type": "string", "description": "Liste adı (varsayılan: varsayılan liste)"},
    },
    ["title"],
)
def reminders_add(title: str, due: str = "", notes: str = "", list_name: str = "") -> str:
    props = f'name:"{_q(title)}", body:"{_q(notes)}"'
    pre = ""
    if due:
        pre = _date_expr("d1", _parse(due, "due"))
        props += ", due date:d1"
    target = f'list "{_q(list_name)}"' if list_name else "default list"
    run_osascript(f'{pre}tell application "Reminders" to make new reminder at end of reminders of {target} with properties {{{props}}}')
    return f"Anımsatıcı eklendi: {title}"


@tool(
    "notes_create",
    "Notlar uygulamasında yeni not oluşturur (HTML biçimlendirme desteklenir).",
    {"title": {"type": "string"}, "body": {"type": "string"}, "folder": {"type": "string"}},
    ["title", "body"],
)
def notes_create(title: str, body: str, folder: str = "") -> str:
    from html import escape

    # Notlar, notun adını gövdenin ilk satırından alır; ayrıca "name" verilirse başlık iki kez görünür
    html = f"<h1>{escape(title)}</h1>" + "".join(f"<div>{escape(line) or '<br>'}</div>" for line in body.splitlines())
    target = f'folder "{_q(folder)}"' if folder else "default account"
    run_osascript(f'tell application "Notes" to make new note at {target} with properties {{body:"{_q(html)}"}}')
    return f"Not oluşturuldu: {title}"


@tool(
    "contacts_search",
    "Kişiler uygulamasında ad/şirket ile arama yapar; e-posta ve telefon döner.",
    {"query": {"type": "string"}},
    ["query"],
)
def contacts_search(query: str) -> str:
    script = f"""
tell application "Contacts"
    set out to ""
    set ppl to (every person whose name contains "{_q(query)}" or organization contains "{_q(query)}")
    repeat with p in ppl
        set em to ""
        repeat with e in emails of p
            set em to em & (value of e) & " "
        end repeat
        set ph to ""
        repeat with t in phones of p
            set ph to ph & (value of t) & " "
        end repeat
        set out to out & (name of p) & "{SEP}" & (organization of p) & "{SEP}" & em & "{SEP}" & ph & linefeed
    end repeat
    return out
end tell"""
    raw = run_osascript(script, timeout=60)
    rows = []
    for line in raw.splitlines()[:50]:
        p = line.split(SEP)
        if len(p) == 4:
            org = f" ({p[1]})" if p[1] and p[1] != "missing value" else ""
            rows.append(f"{p[0]}{org} | e-posta: {p[2].strip() or '-'} | tel: {p[3].strip() or '-'}")
    return "\n".join(rows) or "Kişi bulunamadı."
