"""Çoklu ajan: büyük işleri rol bazlı alt ajanlara paralel dağıtma."""

from __future__ import annotations

import asyncio

from .. import config
from ..approval import emit
from ..router import ROLE_KINDS
from . import ToolResult, tool

ROLE_LABELS = {
    "arastirmaci": "Araştırmacı",
    "analist": "Analist",
    "yazar": "Yazar",
    "denetci": "Denetçi",
    "operator": "Operatör",
}


@tool(
    "delegate_tasks",
    "Birden çok alt görevi rol bazlı alt ajanlara PARALEL olarak dağıtır ve sonuçlarını döndürür. "
    "Bağımsız parçalara bölünebilen büyük işler için kullanın: birkaç konuyu aynı anda araştırmak, "
    "uzun bir raporu bölümlere ayırıp yazdırmak, veriyi analiz ettirirken başka bir ajana taslak "
    "yazdırmak, çıktıyı bir denetçiye kontrol ettirmek. Her alt ajan kendi bağlamıyla sıfırdan "
    "başlar; ihtiyaç duyduğu her bilgiyi (dosya yolları, üslup, kısıtlar) 'context' alanında verin. "
    "Roller: arastirmaci (web), analist (veri/Python), yazar (belge), denetci (kalite), operator (bilgisayar).",
    {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": list(ROLE_LABELS)},
                    "task": {"type": "string", "description": "Net, tek başına anlaşılır görev"},
                    "context": {"type": "string", "description": "Gerekli arka plan bilgisi, dosya yolları, kısıtlar"},
                },
                "required": ["role", "task"],
            },
        }
    },
    ["tasks"],
)
async def delegate_tasks(tasks: list[dict]) -> ToolResult:
    from ..agent import agent_loop, initial_message, subagent_system, subagent_tools

    if not tasks:
        return ToolResult("Görev listesi boş.", is_error=True)
    tasks = tasks[:6]

    async def run_one(idx: int, t: dict) -> tuple[str, list[str]]:
        role = t.get("role") if t.get("role") in ROLE_LABELS else "arastirmaci"
        label = f"{ROLE_LABELS[role]} {idx + 1}"
        task = str(t.get("task", "")).strip()
        context = str(t.get("context", "")).strip()
        prompt = task + (f"\n\nBağlam:\n{context}" if context else "")
        ui = {"text": "", "steps": [], "files": []}
        emit({"type": "step", "label": "Alt ajan başladı", "detail": task[:120], "agent": label})
        messages = [initial_message(prompt)]
        async for ev in agent_loop(
            messages,
            system=subagent_system(role),
            tools=subagent_tools(),
            ui_entry=ui,
            save=lambda: None,
            max_steps=config.SUBAGENT_MAX_STEPS,
            kind=ROLE_KINDS.get(role),  # ADA-AGI: her rol kendi işine uygun motoru alır
        ):
            if ev["type"] in ("step", "step_error", "files", "approval", "approval_resolved", "screenshot"):
                emit({**ev, "agent": ev.get("agent") or label})
        emit({"type": "step", "label": "Alt ajan bitti", "detail": task[:120], "agent": label})
        return f"### {label}: {task[:100]}\n\n{ui['text'].strip() or '(çıktı yok)'}", ui["files"]

    outcomes = await asyncio.gather(*(run_one(i, t) for i, t in enumerate(tasks)), return_exceptions=True)
    parts, files = [], []
    for i, out in enumerate(outcomes):
        if isinstance(out, BaseException):
            parts.append(f"### Alt ajan {i + 1}: HATA — {type(out).__name__}: {out}")
        else:
            parts.append(out[0])
            files.extend(f for f in out[1] if f not in files)
    return ToolResult("\n\n".join(parts), files=files)
