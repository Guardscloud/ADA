"""Güvenlik katmanı: işlem günlüğü, dosya yedekleri (geri alma), günlük limitler, acil durdurma.

- Günlük: her araç çağrısı workspace/.ada/audit/YYYY-MM-DD.jsonl dosyasına yazılır.
- Yedek: var olan bir dosya değiştirilmeden/üzerine yazılmadan önce kopyası saklanır; arayüzden
  veya `restore_backup` aracıyla geri yüklenir.
- Limitler: günlük toplam araç çağrısı ve e-posta gönderimi sınırlıdır (kaçak döngülere karşı).
- Acil durdurma: tüm görevler iptal edilir, hedefler duraklatılır, tetikleyiciler ve zamanlanmış
  görevler durur; `resume` çağrılana kadar yeni görev başlamaz. Durum diskte saklanır.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

from . import config

AUDIT_DIR = config.DATA_DIR / "audit"
BACKUP_DIR = config.DATA_DIR / "backups"
HALT_FILE = config.DATA_DIR / "halted.json"

DAILY_TOOL_LIMIT = int(os.getenv("ADA_DAILY_TOOL_LIMIT", "3000"))
DAILY_EMAIL_LIMIT = int(os.getenv("ADA_DAILY_EMAIL_LIMIT", "20"))
MAX_BACKUP_BYTES = 50_000_000
EMAIL_TOOLS = {"mail_send"}

_lock = threading.Lock()
_counts: dict[str, int] = {}
_counts_day = ""


# ---------------------------------------------------------------- günlük

def _summ(args, limit: int = 400) -> str:
    try:
        text = json.dumps(args, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        text = str(args)
    return text if len(text) <= limit else text[:limit] + "…"


def log(tool: str, args, *, ok: bool, result: str = "", approved: bool | None = None) -> None:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "tool": tool,
        "args": _summ(args),
        "ok": ok,
        "result": (result or "")[:300],
    }
    if approved is not None:
        entry["approved"] = approved
    with _lock:
        with (AUDIT_DIR / f"{date.today():%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def recent(limit: int = 200) -> list[dict]:
    if not AUDIT_DIR.exists():
        return []
    out: list[dict] = []
    for p in sorted(AUDIT_DIR.glob("*.jsonl"), reverse=True):
        lines = p.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(out) >= limit:
                return out
    return out


# ---------------------------------------------------------------- limitler

def check_limit(tool: str) -> str | None:
    """Limit aşıldıysa hata mesajı döner; aşılmadıysa sayacı artırır."""
    global _counts_day
    today = f"{date.today():%Y-%m-%d}"
    with _lock:
        if _counts_day != today:
            _counts.clear()
            _counts_day = today
        total = _counts.get("*", 0)
        # 0 = sınırsız
        if DAILY_TOOL_LIMIT and total >= DAILY_TOOL_LIMIT:
            return f"Günlük araç çağrısı sınırına ({DAILY_TOOL_LIMIT}) ulaşıldı. Yarın veya ADA_DAILY_TOOL_LIMIT artırılınca devam edilebilir."
        if DAILY_EMAIL_LIMIT and tool in EMAIL_TOOLS and _counts.get("email", 0) >= DAILY_EMAIL_LIMIT:
            return f"Günlük e-posta gönderim sınırına ({DAILY_EMAIL_LIMIT}) ulaşıldı."
        _counts["*"] = total + 1
        if tool in EMAIL_TOOLS:
            _counts["email"] = _counts.get("email", 0) + 1
    return None


def usage() -> dict:
    with _lock:
        return {
            "day": _counts_day,
            "tool_calls": _counts.get("*", 0),
            "tool_limit": DAILY_TOOL_LIMIT,
            "emails": _counts.get("email", 0),
            "email_limit": DAILY_EMAIL_LIMIT,
        }


# ---------------------------------------------------------------- yedek / geri alma

def backup(path: Path) -> str | None:
    """Var olan dosyanın kopyasını saklar; yedek kimliğini döner (dosya yoksa/çok büyükse None)."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_BACKUP_BYTES:
            return None
        if path.resolve().is_relative_to(config.DATA_DIR):
            return None
    except OSError:
        return None
    bid = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    dest = BACKUP_DIR / bid
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest / path.name)
    (dest / "meta.json").write_text(
        json.dumps({"id": bid, "original": str(path.resolve()), "name": path.name, "time": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False),
        encoding="utf-8",
    )
    return bid


def list_backups(limit: int = 100) -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    items = []
    for d in sorted(BACKUP_DIR.iterdir(), reverse=True):
        meta = d / "meta.json"
        if meta.exists():
            items.append(json.loads(meta.read_text(encoding="utf-8")))
        if len(items) >= limit:
            break
    return items


def restore(backup_id: str) -> str:
    if not backup_id or "/" in backup_id or ".." in backup_id:
        raise ValueError("Geçersiz yedek kimliği")
    d = BACKUP_DIR / backup_id
    meta_file = d / "meta.json"
    if not meta_file.exists():
        raise ValueError(f"Yedek bulunamadı: {backup_id}")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    original = Path(meta["original"])
    # Geri yüklemeden önce şimdiki hali de yedeklenir (geri almanın geri alınabilmesi için)
    current = backup(original)
    original.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(d / meta["name"], original)
    return f"Geri yüklendi: {original}" + (f" (önceki hali yedek {current})" if current else "")


# ---------------------------------------------------------------- acil durdurma

def is_halted() -> bool:
    return HALT_FILE.exists()


def halt_info() -> dict | None:
    if not HALT_FILE.exists():
        return None
    try:
        return json.loads(HALT_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"time": "?", "source": "?"}


async def emergency_stop(source: str) -> dict:
    """Her şeyi durdurur. Çalışan görevler iptal edilir, hedefler duraklatılır."""
    from . import goals, runtime

    HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    HALT_FILE.write_text(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "source": source}, ensure_ascii=False), encoding="utf-8")
    cancelled = 0
    for run in list(runtime.RUNS.values()):
        if not run.done and run.task is not None:
            run.task.cancel()
            cancelled += 1
    paused = 0
    for g in goals.load_all():
        if g["status"] in ("active", "waiting", "verifying"):
            goals.set_status(g["id"], "paused")
            paused += 1
    from . import terminal

    # ADA'nın (yapay zekâ aracıyla) açtığı terminal oturumları da kapanır; kullanıcının kendi açtıkları kalır
    terminals = terminal.kill_all(source="ada")
    log("ACİL_DURDURMA", {"source": source}, ok=True, result=f"{cancelled} görev iptal, {paused} hedef duraklatıldı, {terminals} terminal kapatıldı")
    return {"cancelled": cancelled, "paused_goals": paused, "terminals": terminals}


def resume(source: str) -> None:
    HALT_FILE.unlink(missing_ok=True)
    log("DEVAM", {"source": source}, ok=True)
