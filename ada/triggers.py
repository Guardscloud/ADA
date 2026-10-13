"""Tetikleyiciler: bir olay olunca ADA'nın kendiliğinden iş başlatması.

Türler:
  new_email  - Mail.app gelen kutusuna yeni e-posta düşünce (gönderen/konu filtresiyle)
  file_added - bir klasöre yeni dosya eklenince (isim kalıbıyla, ör. *.pdf)

İlk kontrolde mevcut durum "görüldü" sayılır; yalnızca sonradan gelenler tetikler. Kaçak maliyeti
önlemek için her tetikleyici saatte en fazla MAX_FIRES_PER_HOUR kez çalışır.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from . import config

FILE = config.DATA_DIR / "triggers.json"
POLL_SECONDS = 60
MAX_FIRES_PER_HOUR = 10
SEEN_LIMIT = 1000
TYPES = {"new_email", "file_added"}
SEP = "␟"

_lock = threading.RLock()


def load() -> list[dict]:
    with _lock:
        if FILE.exists():
            return json.loads(FILE.read_text(encoding="utf-8"))
        return []


def _save(items: list[dict]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(FILE)


def add(name: str, kind: str, prompt: str, cfg: dict) -> dict:
    if kind not in TYPES:
        raise ValueError(f"Geçersiz tür: {kind}")
    if kind == "file_added":
        folder = Path(cfg.get("folder", "")).expanduser()
        if not folder.is_dir():
            raise ValueError(f"Klasör bulunamadı: {folder}")
        cfg = {**cfg, "folder": str(folder.resolve())}
    item = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "type": kind,
        "config": cfg,
        "prompt": prompt,
        "enabled": True,
        "seen": None,  # None: henüz ilk tarama yapılmadı
        "fires": [],
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    with _lock:
        items = load()
        items.append(item)
        _save(items)
    return item


def remove(trigger_id: str) -> bool:
    with _lock:
        items = load()
        kept = [t for t in items if t["id"] != trigger_id]
        if len(kept) == len(items):
            return False
        _save(kept)
    return True


def describe(t: dict) -> str:
    c = t["config"]
    if t["type"] == "new_email":
        parts = ["yeni e-posta"]
        if c.get("from_contains"):
            parts.append(f"gönderen ~ '{c['from_contains']}'")
        if c.get("subject_contains"):
            parts.append(f"konu ~ '{c['subject_contains']}'")
        return ", ".join(parts)
    return f"{c.get('folder')} klasörüne yeni dosya ({c.get('pattern') or '*'})"


# ---------------------------------------------------------------- olay kaynakları

def _latest_emails(count: int = 30) -> list[dict]:
    from .tools.system import run_osascript

    script = f"""
tell application "Mail"
    set n to count of messages of inbox
    if n > {count} then set n to {count}
    if n = 0 then return ""
    set msgs to messages 1 thru n of inbox
    set out to ""
    repeat with m in msgs
        set out to out & (id of m) & "{SEP}" & (sender of m) & "{SEP}" & (subject of m) & linefeed
    end repeat
    return out
end tell"""
    rows = []
    for line in run_osascript(script, timeout=120).splitlines():
        p = line.split(SEP)
        if len(p) == 3:
            rows.append({"id": p[0], "from": p[1], "subject": p[2]})
    return rows


def _folder_files(folder: str, pattern: str) -> list[str]:
    root = Path(folder)
    if not root.is_dir():
        return []
    return sorted(
        str(p) for p in root.iterdir()
        if p.is_file() and not p.name.startswith(".") and fnmatch.fnmatch(p.name.lower(), (pattern or "*").lower())
    )


def poll(t: dict) -> tuple[list[str], list[dict]]:
    """(tüm güncel anahtarlar, yeni olaylar) döner. Engelleyici; thread içinde çağrılır."""
    c = t["config"]
    if t["type"] == "new_email":
        if not config.IS_MAC:
            return [], []
        mails = _latest_emails()
        keys = [m["id"] for m in mails]
        fc, sc = c.get("from_contains", "").casefold(), c.get("subject_contains", "").casefold()
        events = [
            m for m in mails
            if (not fc or fc in m["from"].casefold()) and (not sc or sc in m["subject"].casefold())
        ]
        return keys, events
    files = _folder_files(c["folder"], c.get("pattern", "*"))
    return files, [{"path": f} for f in files]


def event_prompt(t: dict, events: list[dict]) -> tuple[str, str]:
    if t["type"] == "new_email":
        lines = "\n".join(f"- Mail id {e['id']} | Kimden: {e['from']} | Konu: {e['subject']}" for e in events)
        detail = f"{len(events)} yeni e-posta: " + "; ".join(e["subject"][:50] for e in events[:3])
        body = f"Yeni e-posta(lar) geldi (mail_read ile message_id vererek okuyabilirsin):\n{lines}"
    else:
        lines = "\n".join(f"- {e['path']}" for e in events)
        detail = f"{len(events)} yeni dosya: " + ", ".join(Path(e["path"]).name for e in events[:3])
        body = f"Klasöre yeni dosya(lar) eklendi:\n{lines}"
    prompt = f"[TETİKLEYİCİ: {t['name']}]\n{body}\n\nYapılacak iş (kullanıcının talimatı):\n{t['prompt']}"
    return prompt, detail


class Watcher:
    def __init__(self, poll_seconds: float = POLL_SECONDS) -> None:
        self.poll_seconds = poll_seconds

    async def loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001
                print(f"[tetikleyiciler] hata: {type(e).__name__}: {e}")
            await asyncio.sleep(self.poll_seconds)

    async def tick(self) -> None:
        from . import runtime, safety

        if safety.is_halted():
            return

        for t in load():
            if not t.get("enabled", True):
                continue
            try:
                keys, events = await asyncio.to_thread(poll, t)
            except Exception as e:  # noqa: BLE001
                print(f"[tetikleyiciler] {t['name']}: {e}")
                continue
            seen = t.get("seen")
            fresh = [] if seen is None else [
                e for e in events if (e.get("id") or e.get("path")) not in set(seen)
            ]
            recent = [f for f in t.get("fires", []) if datetime.fromisoformat(f) > datetime.now() - timedelta(hours=1)]
            fire = bool(fresh) and len(recent) < MAX_FIRES_PER_HOUR

            def upd(x, keys=keys, fire=fire, recent=recent):
                x["seen"] = list(dict.fromkeys((x.get("seen") or []) + keys))[-SEEN_LIMIT:]
                x["fires"] = recent + ([datetime.now().isoformat(timespec="seconds")] if fire else [])

            with _lock:
                items = load()
                for x in items:
                    if x["id"] == t["id"]:
                        upd(x)
                _save(items)

            if fire:
                prompt, detail = event_prompt(t, fresh[:10])
                await runtime.start_background(f"⚡ {t['name']}", prompt, display_text=f"⚡ Tetikleyici: {t['name']} — {detail}")
