"""Hedef (proje) modu: ADA'nın büyük bir işi kendi başına, oturumlar halinde bitirmesi.

Bir hedefin kendi konuşması vardır. `Runner` etkin hedefler için sırayla "hedef oturumları"
başlatır; model her oturumda planı günceller, adımları yapar ve `goal_*` araçlarıyla ilerlemeyi
kaydeder. Tüm adımlar bitince `goal_complete` bağımsız bir denetçi alt ajana işi kontrol ettirir;
onaylanmazsa geri bildirimle devam edilir.

Kaçak maliyeti önlemek için: oturum sınırı (max_sessions), ilerleme kaydetmeyen iki oturumdan
sonra otomatik duraklatma ve aynı anda en fazla MAX_PARALLEL hedef.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import datetime, timedelta

from . import config, store

FILE = config.DATA_DIR / "goals.json"
MAX_PARALLEL = 2
SESSION_GAP_SECONDS = 5
IDLE_LIMIT = 2
MAX_VERIFICATIONS = 3
STATUSES = {"active", "paused", "waiting", "verifying", "done", "failed"}

_lock = threading.RLock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_all() -> list[dict]:
    with _lock:
        if FILE.exists():
            return json.loads(FILE.read_text(encoding="utf-8"))
        return []


def _save_all(items: list[dict]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(FILE)


def get(goal_id: str) -> dict | None:
    return next((g for g in load_all() if g["id"] == goal_id), None)


def update(goal_id: str, fn, progress: bool = True) -> dict | None:
    """Hedefi atomik olarak günceller; `fn(goal)` sözlüğü yerinde değiştirir.

    progress=False: yürütücünün iç kayıtları (oturum sayacı, zamanlama) ilerleme sayılmaz.
    """
    with _lock:
        items = load_all()
        for g in items:
            if g["id"] == goal_id:
                fn(g)
                g["updated"] = _now()
                if progress:
                    g["rev"] = g.get("rev", 0) + 1  # ilerleme ölçümü (zaman damgası saniye hassasiyetinde)
                _save_all(items)
                return g
    return None


def create(title: str, objective: str, max_sessions: int = 30) -> dict:
    conv = store.create()
    conv.title = f"🎯 {title}"[:60]
    conv.save()
    goal = {
        "id": uuid.uuid4().hex[:8],
        "title": title,
        "objective": objective,
        "status": "active",
        "plan": [],
        "notes": [],
        "conv_id": conv.id,
        "created": _now(),
        "updated": _now(),
        "sessions": 0,
        "max_sessions": max(1, min(int(max_sessions), 200)),
        "idle_sessions": 0,
        "next_run": None,
        "wait_reason": "",
        "summary": "",
        "verify_feedback": "",
        "verifications": 0,
    }
    with _lock:
        items = load_all()
        items.append(goal)
        _save_all(items)
    return goal


def set_status(goal_id: str, status: str) -> dict | None:
    if status not in STATUSES:
        return None

    def fn(g):
        g["status"] = status
        if status == "active":
            g["idle_sessions"] = 0
            g["next_run"] = None
            if g["sessions"] >= g["max_sessions"]:
                g["max_sessions"] = g["sessions"] + 10  # kullanıcı devam ettirdi: ek oturum hakkı

    return update(goal_id, fn, progress=False)


def delete(goal_id: str) -> bool:
    with _lock:
        items = load_all()
        kept = [g for g in items if g["id"] != goal_id]
        if len(kept) == len(items):
            return False
        _save_all(kept)
    return True


def progress(g: dict) -> str:
    done = sum(1 for s in g["plan"] if s["status"] in ("done", "skipped"))
    return f"{done}/{len(g['plan'])}" if g["plan"] else "plan yok"


def session_prompt(g: dict) -> str:
    plan = "\n".join(
        f"  {i}. [{s['status']}] {s['step']}" + (f" → {s['result'][:200]}" if s.get("result") else "")
        for i, s in enumerate(g["plan"])
    ) or "  (henüz plan yok)"
    notes = "\n".join(f"  - {n}" for n in g["notes"][-10:]) or "  -"
    feedback = f"\nDENETÇİ GERİ BİLDİRİMİ (düzeltilmeli):\n{g['verify_feedback']}\n" if g.get("verify_feedback") else ""
    return (
        f"[HEDEF MODU] Hedef kimliği: {g['id']}\n"
        f"Başlık: {g['title']}\nAmaç: {g['objective']}\n"
        f"Oturum: {g['sessions']}/{g['max_sessions']}\n\nPlan:\n{plan}\n\nNotlar:\n{notes}\n{feedback}\n"
        "Bu oturumda hedefe doğru gerçek ilerleme kaydet:\n"
        "1. Plan yoksa önce goal_set_plan ile 3-12 somut adımlık plan kur.\n"
        "2. Sıradaki adım(lar)ı gerçekten yap (araçları kullan). Her adım bitince goal_step_done ile sonucu kaydet. "
        "Önemli bulguları goal_note ile not al — sonraki oturumlar bu notları görür.\n"
        "3. Dışarıdan bir şey bekliyorsan (e-posta yanıtı, bir tarih, kullanıcı kararı) goal_wait ile beklemeye al ve nedenini yaz.\n"
        "4. Tüm adımlar bitince teslimatları kendin kontrol et, sonra goal_complete çağır (denetçi onaylamazsa geri bildirimi düzelt).\n"
        "5. Hedef imkânsızsa goal_fail ile nedenini açıkla.\n"
        "Bir oturumda en fazla 2-4 adım yap; oturumun sonunda ne yaptığını kısaca özetle."
    )


class Runner:
    """Etkin hedefleri arka planda yürütür."""

    def __init__(self, tick_seconds: float = 10) -> None:
        self.tick_seconds = tick_seconds
        self.session_marks: dict[str, int] = {}  # goal_id -> oturum başındaki revizyon

    async def loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001
                print(f"[hedefler] hata: {type(e).__name__}: {e}")
            await asyncio.sleep(self.tick_seconds)

    async def tick(self) -> None:
        from . import runtime, safety

        if safety.is_halted():
            return

        now = datetime.now()
        items = load_all()
        running = [g for g in items if runtime.is_running(g["conv_id"])]

        # Biten oturumları değerlendir: ilerleme yoksa boşta sayacı
        for g in items:
            mark = self.session_marks.get(g["id"])
            if mark is None or runtime.is_running(g["conv_id"]):
                continue
            self.session_marks.pop(g["id"])
            fresh = get(g["id"])
            if fresh is None:
                continue
            progressed = fresh.get("rev", 0) != mark
            if fresh["status"] == "active":
                idle = 0 if progressed else fresh["idle_sessions"] + 1

                def after(x, idle=idle):
                    x["idle_sessions"] = idle
                    x["next_run"] = (datetime.now() + timedelta(seconds=SESSION_GAP_SECONDS)).isoformat(timespec="seconds")
                    if idle >= IDLE_LIMIT:
                        x["status"] = "paused"
                        x["notes"].append(f"{_now()}: {IDLE_LIMIT} oturumdur ilerleme kaydedilmedi; duraklatıldı.")

                updated = update(g["id"], after, progress=False)
                if updated and updated["status"] == "paused":
                    runtime.notify(f"ADA hedefi duraklattı: {g['title']}", "İlerleme kaydedilemedi; hedef konuşmasına bakın.")

        items = load_all()
        for g in items:
            if len(running) >= MAX_PARALLEL:
                break
            if runtime.is_running(g["conv_id"]) or g["id"] in self.session_marks:
                continue
            due = g["next_run"] is None or datetime.fromisoformat(g["next_run"]) <= now
            if g["status"] == "waiting" and due:
                g = set_status(g["id"], "active") or g
            if g["status"] != "active" or not due:
                continue
            if g["sessions"] >= g["max_sessions"]:
                update(g["id"], lambda x: (x.update(status="paused"), x["notes"].append(f"{_now()}: oturum sınırına ulaşıldı.")), progress=False)
                runtime.notify(f"ADA hedefi duraklattı: {g['title']}", "Oturum sınırına ulaşıldı.")
                continue
            conv = store.load(g["conv_id"])
            if conv is None:
                update(g["id"], lambda x: x.update(status="failed", summary="Hedef konuşması bulunamadı."))
                continue
            g = update(g["id"], lambda x: x.update(sessions=x["sessions"] + 1), progress=False)
            self.session_marks[g["id"]] = g.get("rev", 0)
            runtime.start(
                conv, session_prompt(g), display_text=f"⚙ Hedef oturumu #{g['sessions']} ({progress(g)})", unattended=True
            )
            running.append(g)


async def verify(g: dict, summary: str, deliverables: list[str]) -> tuple[bool, str]:
    """Bağımsız denetçi alt ajan teslimatı amaca göre kontrol eder."""
    from .agent import agent_loop, initial_message, subagent_system, subagent_tools

    plan = "\n".join(f"- [{s['status']}] {s['step']}: {s.get('result', '')[:300]}" for s in g["plan"])
    prompt = (
        f"Bir hedefin tamamlandığı bildirildi. Teslimatı bağımsız olarak kontrol et.\n\n"
        f"AMAÇ:\n{g['objective']}\n\nPLAN VE SONUÇLAR:\n{plan}\n\nÖZET:\n{summary}\n\n"
        f"TESLİMATLAR (dosya yolları vb.):\n" + ("\n".join(deliverables) or "-") + "\n\n"
        "Dosyaları gerçekten açıp oku, iddiaları doğrula. Yanıtının SON satırı tam olarak 'KARAR: ONAY' ya da "
        "'KARAR: RED' olsun; RED ise öncesinde somut, maddeli düzeltme listesi yaz."
    )
    ui = {"text": "", "steps": [], "files": []}
    async for _ in agent_loop(
        [initial_message(prompt)],
        system=subagent_system("denetci"),
        tools=subagent_tools(),
        ui_entry=ui,
        save=lambda: None,
        max_steps=config.SUBAGENT_MAX_STEPS,
        kind="derin",
    ):
        pass
    text = ui["text"].strip()
    last = text.splitlines()[-1].upper() if text else ""
    return ("ONAY" in last and "RED" not in last), text
