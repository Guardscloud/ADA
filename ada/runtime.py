"""Görev çalıştırma çekirdeği: arayüz, zamanlayıcı, tetikleyiciler ve hedefler aynı yolu kullanır.

Her görev (konuşma turu) arka planda bir asyncio görevi olarak çalışır; olaylar bellekte
tutulur ve arayüz SSE ile izler. Kullanıcı izlemiyorken onay istenirse masaüstü bildirimi gider.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar

from . import config, store
from .agent import build_user_content, run_turn

# Kullanıcının sohbetinden değil arka plandan (zamanlayıcı, tetikleyici, hedef, rutin) başlatılan
# çalışmalarda True. Görev oluşturulurken bağlam kopyalandığı için o çalışmanın araçları da görür.
UNATTENDED: ContextVar[bool] = ContextVar("UNATTENDED", default=False)


class Run:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.done = False
        self.changed = asyncio.Condition()
        self.task: asyncio.Task | None = None
        self.watchers = 0  # açık SSE bağlantısı sayısı

    async def push(self, event: dict) -> None:
        async with self.changed:
            self.events.append(event)
            if event["type"] == "done":
                self.done = True
            self.changed.notify_all()
        if event["type"] == "approval" and self.watchers == 0:
            notify("ADA onay bekliyor", event.get("title", ""))


RUNS: dict[str, Run] = {}


def is_running(conv_id: str) -> bool:
    run = RUNS.get(conv_id)
    # Başlamadan iptal edilen görev "done" olayı yazamaz; görevin kendisine de bakılır
    return bool(run and not run.done and not (run.task and run.task.done()))


def notify(title: str, message: str) -> None:
    """Masaüstü bildirimi (macOS ve Windows; engellemez, hata olursa sessizce geçer)."""
    from . import notify as _notify

    _notify.notify(title, message)


async def _execute(
    conv: store.Conversation, content: list[dict], display_text: str, run: Run, max_minutes: float | None = None
) -> None:
    try:
        async for event in run_turn(conv, content, display_text, max_minutes=max_minutes):
            await run.push(event)
    except asyncio.CancelledError:
        await run.push({"type": "error", "message": "Görev durduruldu."})
    except Exception as e:  # noqa: BLE001
        await run.push({"type": "error", "message": f"Beklenmeyen hata: {type(e).__name__}: {e}"})
    finally:
        if not run.done:
            await run.push({"type": "done"})


def start(
    conv: store.Conversation,
    text: str,
    attachments: list[str] | None = None,
    display_text: str | None = None,
    max_minutes: float | None = None,
    unattended: bool = False,
) -> Run:
    """Konuşmada yeni bir tur başlatır. Aynı konuşmada çalışan tur varsa RuntimeError.
    `unattended`: kullanıcı başında değil (arka plan işi); bazı araçlar buna göre kısıtlanır."""
    from . import safety

    if safety.is_halted():
        raise RuntimeError("ADA acil durdurma modunda. Önce 'Devam et' deyin.")
    if is_running(conv.id):
        raise RuntimeError("Bu konuşmada zaten çalışan bir görev var")
    content = build_user_content(text, attachments or [])
    run = Run()
    RUNS[conv.id] = run
    # Referansı tut; aksi halde görev çöp toplayıcı tarafından erken silinebilir
    token = UNATTENDED.set(unattended)
    try:
        run.task = asyncio.create_task(_execute(conv, content, display_text or text, run, max_minutes))
    finally:
        UNATTENDED.reset(token)
    return run


async def start_background(
    title: str,
    prompt: str,
    display_text: str | None = None,
    notify_done: bool = True,
    max_minutes: float | None = None,
) -> store.Conversation:
    """Yeni bir konuşma açıp görevi arka planda başlatır (zamanlayıcı, tetikleyiciler ve rutinler için)."""
    conv = store.create()
    conv.title = title
    conv.save()
    run = start(conv, prompt, display_text=display_text, max_minutes=max_minutes, unattended=True)

    async def finish() -> None:
        await run.task
        if notify_done:
            fresh = store.load(conv.id)
            summary = (fresh.ui[-1].get("text", "") if fresh and fresh.ui else "").strip().replace("\n", " ")
            notify(f"ADA: {title}", summary[:160] or "Görev tamamlandı")

    asyncio.create_task(finish())
    return conv
