"""ADA kalite değerlendirmesi.

Her görevi gerçek ajan döngüsünden geçirir, ardından bir LLM-hakem (Claude) ürünleri ve
yanıtı rubrik maddelerine göre puanlar. Sonuçlar evals/results/<zaman>.json'a yazılır.

Kullanım:
    uv run python evals/run.py                # tüm görevler
    uv run python evals/run.py kisa-yanit     # tek görev
    ADA_AUTONOMY=full uv run python evals/run.py   # onay beklemeden

Her çalıştırma gerçek API çağrısı yapar (maliyet!). Görevler ayrı bir workspace'te çalışır.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Yalıtım: ADA'nın ev dizini (~) geçici klasör olur, böylece görevler kullanıcının gerçek dosyalarını
# taramaz. Özerklik "smart" ve onay beklemesi 1 sn: riskli işlemler "kullanıcı başında değil" gibi reddedilir.
_EVAL_HOME = tempfile.mkdtemp(prefix="ada-eval-")
os.environ.setdefault("ADA_WORKSPACE", os.path.join(_EVAL_HOME, "workspace"))
os.environ["HOME"] = _EVAL_HOME
for _d in ("Desktop", "Documents", "Downloads"):
    os.makedirs(os.path.join(_EVAL_HOME, _d), exist_ok=True)
os.environ.setdefault("ADA_AUTONOMY", "smart")
os.environ.setdefault("ADA_APPROVAL_TIMEOUT", "1")
sys.path.insert(0, str(ROOT))

from pydantic import BaseModel  # noqa: E402

from ada import config, store  # noqa: E402
from ada.agent import build_user_content, run_turn  # noqa: E402
from ada.tools import REGISTRY  # noqa: E402

JUDGE_MODEL = "claude-opus-5-5"  # Claude sağlayıcısında
GEMINI_JUDGE_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]


class Grade(BaseModel):
    criteria: list[bool]
    score: int  # 0-10
    reasoning: str


async def judge(prompt: str) -> "Grade":
    """Hakem, etkin sağlayıcıyla aynı aileden seçilir (API kredisi olmayan kullanıcı için Gemini)."""
    if config.PROVIDER == "anthropic":
        import anthropic

        resp = await anthropic.AsyncAnthropic().messages.parse(
            model=JUDGE_MODEL, max_tokens=4000, output_format=Grade,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.parsed_output

    from google.genai import errors, types

    from ada.providers.gemini import get_client

    last: Exception | None = None
    for model in GEMINI_JUDGE_MODELS:
        try:
            resp = await get_client().aio.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Grade),
            )
            return resp.parsed if isinstance(resp.parsed, Grade) else Grade.model_validate_json(resp.text)
        except (errors.ServerError, errors.ClientError) as e:  # yoğunluk/kota: sıradaki model
            last = e
    raise RuntimeError(f"Hakem çalıştırılamadı: {last}")


def read_output_files() -> str:
    parts = []
    for p in sorted(config.OUTPUTS_DIR.rglob("*")):
        if p.is_file():
            try:
                text = REGISTRY["read_document"].handler(path=str(p))[:6000]
            except Exception as e:  # noqa: BLE001
                text = f"(okunamadı: {e})"
            parts.append(f"### {p.relative_to(config.WORKSPACE)} ({p.stat().st_size} bayt)\n{text}")
    return "\n\n".join(parts) or "(çıktı dosyası yok)"


async def run_goal(task: dict, limit_minutes: float = 20) -> tuple[list[str], list[str], str]:
    """Hedef modunu uçtan uca çalıştırır: hedef oluşturulur, yürütücü bitene (veya duraklayana) kadar döner."""
    from ada import goals, runtime

    g = goals.create(task.get("title", task["id"]), task["prompt"], max_sessions=task.get("max_sessions", 8))
    runner = goals.Runner()
    end = time.time() + limit_minutes * 60
    while time.time() < end:
        await runner.tick()
        while any(not r.done for r in runtime.RUNS.values()):
            await asyncio.sleep(1)
        cur = goals.get(g["id"])
        if cur["status"] in ("done", "failed", "paused"):
            break
        goals.update(g["id"], lambda x: x.update(next_run=None), progress=False)  # beklemeden sonraki oturum
    cur = goals.get(g["id"])
    conv = store.load(cur["conv_id"])
    steps = [s["label"] + (" · " + s.get("detail", "") if s.get("detail") else "") for u in conv.ui if u["role"] == "assistant" for s in u["steps"]]
    errors = [s["detail"] for u in conv.ui if u["role"] == "assistant" for s in u["steps"] if "(hata)" in s["label"]]
    plan = "\n".join(f"- [{s['status']}] {s['step']}: {s.get('result', '')[:200]}" for s in cur["plan"])
    final = (
        f"HEDEF DURUMU: {cur['status']} · oturum {cur['sessions']} · denetim {cur.get('verifications', 0)}\n"
        f"PLAN:\n{plan}\nÖZET: {cur.get('summary', '')}\nNOTLAR: {cur['notes'][-5:]}\n\n"
        f"SON OTURUM YANITI:\n{conv.ui[-1].get('text', '') if conv.ui else ''}"
    )
    return steps, errors, final


async def run_task(task: dict) -> dict:
    # Temiz workspace: outputs/ ve uploads/ boşalt
    for d in (config.OUTPUTS_DIR, config.UPLOADS_DIR):
        for p in sorted(d.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
    if task.get("setup_python"):
        REGISTRY["run_python"].handler(code=task["setup_python"])

    started = time.time()
    if task.get("mode") == "goal":
        steps, errors, final_text = await run_goal(task)
    else:
        conv = store.create()
        steps, errors, final_text = [], [], ""
        async for ev in run_turn(conv, build_user_content(task["prompt"], task.get("attachments", [])), task["prompt"]):
            if ev["type"] == "step":
                steps.append(ev["label"] + (" · " + ev["detail"] if ev.get("detail") else ""))
            elif ev["type"] == "step_error":
                errors.append(ev["detail"])
            elif ev["type"] == "text":
                final_text += ev["text"]
            elif ev["type"] == "error":
                errors.append(ev["message"])
    duration = time.time() - started

    rubric = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(task["rubric"]))
    prompt = (
        "Sen titiz bir kalite denetçisisin. Aşağıdaki görev için ajanın yanıtını ve ürettiği dosyaları "
        "rubrik maddelerine göre değerlendir. Her madde için true/false ver (criteria listesi, rubrikle aynı "
        "sırada), 0-10 arası genel puan ve kısa gerekçe yaz.\n\n"
        f"## Görev\n{task['prompt']}\n\n## Rubrik\n{rubric}\n\n"
        f"## Ajanın adımları\n" + "\n".join(steps[:60]) + "\n\n"
        f"## Hatalar\n" + ("\n".join(errors) or "-") + "\n\n"
        f"## Ajanın son yanıtı\n{final_text[:8000]}\n\n"
        f"## Üretilen dosyalar\n{read_output_files()[:20000]}"
    )
    grade = await judge(prompt)
    return {
        "id": task["id"],
        "score": grade.score,
        "criteria": dict(zip(task["rubric"], grade.criteria)),
        "reasoning": grade.reasoning,
        "duration_s": round(duration, 1),
        "steps": len(steps),
        "errors": errors,
        "final_text": final_text,
    }


async def main() -> None:
    tasks = json.loads((ROOT / "evals" / "tasks.json").read_text(encoding="utf-8"))
    only = set(sys.argv[1:])
    if only:
        tasks = [t for t in tasks if t["id"] in only]
    results = []
    for task in tasks:
        print(f"\n▶ {task['id']}", flush=True)
        try:
            r = await run_task(task)
        except Exception as e:  # noqa: BLE001
            r = {"id": task["id"], "score": 0, "criteria": {}, "reasoning": f"HATA: {type(e).__name__}: {e}", "duration_s": 0, "steps": 0, "errors": [str(e)], "final_text": ""}
        results.append(r)
        passed = sum(1 for v in r["criteria"].values() if v)
        print(f"   puan {r['score']}/10 · {passed}/{len(r['criteria'])} madde · {r['duration_s']}s · {r['steps']} adım")
        print(f"   {r['reasoning'][:300]}")

    out_dir = ROOT / "evals" / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"model": config.MODEL, "effort": config.EFFORT, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    avg = sum(r["score"] for r in results) / max(len(results), 1)
    print(f"\nOrtalama: {avg:.1f}/10 · sonuçlar: {out}")


if __name__ == "__main__":
    asyncio.run(main())
