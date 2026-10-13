"""Otonomi araçları: hedefler, tetikleyiciler ve beceriler."""

from __future__ import annotations

from datetime import datetime, timedelta

from . import ToolError, tool


def _goal(goal_id: str) -> dict:
    from .. import goals

    g = goals.get(goal_id)
    if g is None:
        raise ToolError(f"Hedef bulunamadı: {goal_id}")
    return g


# ---------------------------------------------------------------- hedefler

@tool(
    "create_goal",
    "Uzun soluklu, çok adımlı bir işi HEDEF olarak başlatır; ADA onu arka planda oturumlar halinde "
    "kendi başına planlayıp yürütür, sonunda bağımsız denetçiye kontrol ettirir. Kullanıcı 'şu projeyi "
    "üstlen', 'bunu takip et ve bitir', 'birkaç gün içinde hallet' gibi büyük işler verdiğinde kullan. "
    "Tek oturumda bitecek işler için kullanma, doğrudan yap.",
    {
        "title": {"type": "string", "description": "Kısa başlık"},
        "objective": {"type": "string", "description": "Ayrıntılı amaç: beklenen teslimatlar, kısıtlar, kalite ölçütü, bağlam"},
        "max_sessions": {"type": "integer", "description": "En fazla oturum (varsayılan 30)"},
    },
    ["title", "objective"],
)
def create_goal(title: str, objective: str, max_sessions: int = 30) -> str:
    from .. import goals

    g = goals.create(title, objective, max_sessions=max_sessions)
    return f"Hedef oluşturuldu [{g['id']}]: {title}. Arka planda birkaç saniye içinde başlayacak; ilerleme kenar çubuğundaki Hedefler bölümünde."


@tool("list_goals", "Hedefleri durumları ve ilerlemeleriyle listeler.", {})
def list_goals() -> str:
    from .. import goals

    items = goals.load_all()
    if not items:
        return "Hedef yok."
    return "\n".join(f"[{g['id']}] {g['title']} — {g['status']} — adımlar {goals.progress(g)} — oturum {g['sessions']}/{g['max_sessions']}" for g in items)


@tool(
    "goal_set_plan",
    "Hedefin planını kurar veya yeniden yazar (3-12 somut adım). Tamamlanmış adımların metni aynı kalırsa durumları korunur.",
    {"goal_id": {"type": "string"}, "steps": {"type": "array", "items": {"type": "string"}}},
    ["goal_id", "steps"],
)
def goal_set_plan(goal_id: str, steps: list[str]) -> str:
    from .. import goals

    _goal(goal_id)
    if not steps:
        raise ToolError("En az bir adım gerekli.")

    def fn(g):
        old = {s["step"]: s for s in g["plan"]}
        g["plan"] = [old.get(s, {"step": s, "status": "todo", "result": ""}) for s in steps[:20]]
        g["verify_feedback"] = g.get("verify_feedback", "")

    g = goals.update(goal_id, fn)
    return "Plan kaydedildi:\n" + "\n".join(f"{i}. [{s['status']}] {s['step']}" for i, s in enumerate(g["plan"]))


@tool(
    "goal_step_done",
    "Plandaki bir adımı tamamlandı (veya atlandı) olarak işaretler ve sonucunu kaydeder.",
    {
        "goal_id": {"type": "string"},
        "step_index": {"type": "integer", "description": "0'dan başlayan adım numarası"},
        "result": {"type": "string", "description": "Ne yapıldı, bulgular, üretilen dosyalar"},
        "skipped": {"type": "boolean"},
    },
    ["goal_id", "step_index", "result"],
)
def goal_step_done(goal_id: str, step_index: int, result: str, skipped: bool = False) -> str:
    from .. import goals

    g = _goal(goal_id)
    if not 0 <= step_index < len(g["plan"]):
        raise ToolError(f"Geçersiz adım: {step_index} (plan {len(g['plan'])} adım)")
    g = goals.update(goal_id, lambda x: x["plan"][step_index].update(status="skipped" if skipped else "done", result=result[:2000]))
    remaining = [i for i, s in enumerate(g["plan"]) if s["status"] not in ("done", "skipped")]
    return f"Adım {step_index} kaydedildi. İlerleme {goals.progress(g)}." + (
        f" Sıradaki: {remaining[0]}. {g['plan'][remaining[0]]['step']}" if remaining else " Tüm adımlar bitti: teslimatı kontrol edip goal_complete çağır."
    )


@tool(
    "goal_note",
    "Hedefe kalıcı not ekler (bulgular, kararlar, bağlantılar). Sonraki oturumlar bu notları görür.",
    {"goal_id": {"type": "string"}, "note": {"type": "string"}},
    ["goal_id", "note"],
)
def goal_note(goal_id: str, note: str) -> str:
    from .. import goals

    _goal(goal_id)
    goals.update(goal_id, lambda x: x["notes"].append(f"{datetime.now():%m-%d %H:%M}: {note[:1000]}"))
    return "Not eklendi."


@tool(
    "goal_wait",
    "Hedefi bir süre beklemeye alır (dış bir yanıt, tarih veya kullanıcı kararı beklenirken). Süre dolunca otomatik devam eder.",
    {
        "goal_id": {"type": "string"},
        "minutes": {"type": "integer", "description": "Kaç dakika sonra tekrar bakılsın (en az 5)"},
        "reason": {"type": "string"},
    },
    ["goal_id", "minutes", "reason"],
)
def goal_wait(goal_id: str, minutes: int, reason: str) -> str:
    from .. import goals, runtime

    g = _goal(goal_id)
    until = datetime.now() + timedelta(minutes=max(5, minutes))

    def fn(x):
        x["status"] = "waiting"
        x["next_run"] = until.isoformat(timespec="seconds")
        x["wait_reason"] = reason
        x["notes"].append(f"{datetime.now():%m-%d %H:%M}: beklemede — {reason}")

    goals.update(goal_id, fn)
    runtime.notify(f"ADA bekliyor: {g['title']}", reason)
    return f"Hedef {until:%d.%m %H:%M}'e kadar beklemede: {reason}"


@tool(
    "goal_complete",
    "Hedefi tamamlandı olarak bildirir. Bağımsız bir denetçi teslimatı kontrol eder; onaylarsa hedef kapanır, "
    "onaylamazsa düzeltme listesi döner ve hedef devam eder.",
    {
        "goal_id": {"type": "string"},
        "summary": {"type": "string", "description": "Ne teslim edildi, nasıl doğrulandı"},
        "deliverables": {"type": "array", "items": {"type": "string"}, "description": "Dosya yolları, bağlantılar"},
    },
    ["goal_id", "summary"],
)
async def goal_complete(goal_id: str, summary: str, deliverables: list[str] | None = None) -> str:
    import asyncio

    from .. import goals, runtime

    g = await asyncio.to_thread(_goal, goal_id)
    open_steps = [s["step"] for s in g["plan"] if s["status"] not in ("done", "skipped")]
    if open_steps:
        raise ToolError("Bitmemiş adımlar var: " + "; ".join(open_steps[:5]))

    if g.get("verifications", 0) >= goals.MAX_VERIFICATIONS:
        passed, report = True, "Denetim hakkı doldu; son hali teslim edildi (kullanıcı kontrol etmeli)."
    else:
        goals.update(goal_id, lambda x: x.update(status="verifying", verifications=x.get("verifications", 0) + 1))
        try:
            passed, report = await goals.verify(g, summary, deliverables or [])
        except Exception as e:  # noqa: BLE001
            goals.update(goal_id, lambda x: x.update(status="active"))
            raise ToolError(f"Denetim çalıştırılamadı: {type(e).__name__}: {e}")

    if passed:
        goals.update(goal_id, lambda x: x.update(status="done", summary=summary, verify_feedback=""))
        runtime.notify(f"ADA hedefi tamamladı: {g['title']}", summary[:160])
        return f"HEDEF TAMAMLANDI ve denetçi onayladı.\n\nDenetçi raporu:\n{report[-1500:]}"

    goals.update(goal_id, lambda x: x.update(status="active", verify_feedback=report[-3000:]))
    return (
        "Denetçi ONAYLAMADI. Aşağıdaki sorunları düzelt (gerekirse goal_set_plan ile düzeltme adımları ekle), "
        f"sonra tekrar goal_complete çağır.\n\n{report[-3000:]}"
    )


@tool(
    "goal_fail",
    "Hedefin gerçekleştirilemeyeceğini bildirir ve kapatır (neden ve kullanıcıya öneri ile).",
    {"goal_id": {"type": "string"}, "reason": {"type": "string"}},
    ["goal_id", "reason"],
)
def goal_fail(goal_id: str, reason: str) -> str:
    from .. import goals, runtime

    g = _goal(goal_id)
    goals.update(goal_id, lambda x: x.update(status="failed", summary=reason))
    runtime.notify(f"ADA hedefi bitiremedi: {g['title']}", reason[:160])
    return "Hedef başarısız olarak kapatıldı."


# ---------------------------------------------------------------- tetikleyiciler

@tool(
    "create_trigger",
    "Bir olay olduğunda ADA'nın kendiliğinden iş yapmasını sağlar. Türler: new_email (Mail'e yeni e-posta "
    "gelince; from_contains / subject_contains ile filtrelenebilir), file_added (bir klasöre yeni dosya "
    "düşünce; pattern ör. '*.pdf'). prompt: olay olunca yapılacak iş, tam talimat olarak.",
    {
        "name": {"type": "string"},
        "type": {"type": "string", "enum": ["new_email", "file_added"]},
        "prompt": {"type": "string"},
        "from_contains": {"type": "string"},
        "subject_contains": {"type": "string"},
        "folder": {"type": "string", "description": "file_added için klasör yolu (~ desteklenir)"},
        "pattern": {"type": "string", "description": "file_added için dosya adı kalıbı, ör. *.pdf"},
    },
    ["name", "type", "prompt"],
)
def create_trigger(name: str, type: str, prompt: str, from_contains: str = "", subject_contains: str = "", folder: str = "", pattern: str = "*") -> str:
    from .. import triggers

    cfg = {"from_contains": from_contains, "subject_contains": subject_contains} if type == "new_email" else {"folder": folder, "pattern": pattern or "*"}
    try:
        t = triggers.add(name, type, prompt, cfg)
    except ValueError as e:
        raise ToolError(str(e))
    return f"Tetikleyici kuruldu [{t['id']}]: {name} — {triggers.describe(t)}. Mevcut içerik görüldü sayılır; bundan sonra gelenlerde çalışır (her dakika kontrol edilir)."


@tool("list_triggers", "Tetikleyicileri listeler.", {})
def list_triggers() -> str:
    from .. import triggers

    items = triggers.load()
    return "\n".join(f"[{t['id']}] {t['name']} — {triggers.describe(t)} — talimat: {t['prompt'][:100]}" for t in items) or "Tetikleyici yok."


@tool("delete_trigger", "Tetikleyiciyi siler.", {"trigger_id": {"type": "string"}}, ["trigger_id"])
def delete_trigger(trigger_id: str) -> str:
    from .. import triggers

    if not triggers.remove(trigger_id):
        raise ToolError(f"Bulunamadı: {trigger_id}")
    return "Silindi."


# ---------------------------------------------------------------- beceriler

@tool(
    "skill_save",
    "Başarıyla tamamladığın, tekrar gelebilecek çok adımlı bir işin tarifini BECERİ olarak kaydeder "
    "(ör. 'aylık satış raporu hazırlama', 'fatura PDF'lerini Excel'e dökme'). İşe yarayan komutları, dosya "
    "yollarını, tuzakları ve kalite kontrollerini yaz. Aynı adla kaydetmek beceriyi günceller.",
    {
        "name": {"type": "string"},
        "description": {"type": "string", "description": "Ne işe yarar (tek cümle)"},
        "when_to_use": {"type": "string", "description": "Hangi isteklerde kullanılmalı"},
        "steps": {"type": "string", "description": "Adım adım tarif (markdown), somut komutlar/araçlarla"},
    },
    ["name", "description", "when_to_use", "steps"],
)
def skill_save(name: str, description: str, when_to_use: str, steps: str) -> str:
    from .. import skills

    s = skills.save(name, description, when_to_use, steps)
    return f"Beceri kaydedildi: {s['slug']} (sürüm {s['version']})"


@tool("skill_use", "Kayıtlı bir becerinin tam tarifini getirir.", {"slug": {"type": "string"}}, ["slug"])
def skill_use(slug: str) -> str:
    from .. import skills

    s = skills.get(slug, count_use=True)
    if s is None:
        raise ToolError(f"Beceri bulunamadı: {slug}")
    return f"# {s['name']} (sürüm {s['version']})\n{s['description']}\nNe zaman: {s['when']}\n\n{s['steps']}"


@tool("skill_search", "Becerilerde arama yapar.", {"query": {"type": "string"}}, ["query"])
def skill_search(query: str) -> str:
    from .. import skills

    hits = skills.search(query)
    return "\n".join(f"- {s['slug']}: {s['name']} — {s['description']}" for s in hits) or "Uygun beceri yok."


@tool("skill_delete", "Beceriyi siler (yanlış veya eskimişse).", {"slug": {"type": "string"}}, ["slug"])
def skill_delete(slug: str) -> str:
    from .. import skills

    if not skills.delete(slug):
        raise ToolError(f"Bulunamadı: {slug}")
    return "Silindi."
