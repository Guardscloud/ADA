"""ADA'nın ajan döngüsü: Claude'u çağırır, araçları çalıştırır, görev bitene kadar tekrarlar.

`agent_loop` genel döngüdür (ana ajan ve alt ajanlar kullanır); `run_turn` bir konuşma
üzerinde çalışan, hataları kullanıcı diline çeviren sarmalayıcıdır. Her ikisi de arayüze
akış halinde olaylar üretir: text, thinking, step, step_error, files, screenshot, approval,
approval_resolved, error, done.

Geçmiş yalnızca sona eklenir (düşünce blokları geçmiş düzenlenirse geçersiz olur); uzun
görevlerde eski araç sonuçları sunucu tarafında temizlenir (context editing).
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
from datetime import datetime
from typing import AsyncIterator, Callable

import anthropic

from . import approval, config, injection, safety
from .prompts import SUBAGENT_COMMON, SUBAGENT_ROLES, SYSTEM_PROMPT
from .store import Conversation
from .tools import REGISTRY, ToolError, ToolResult, computer, validate_input

_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


SERVER_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 20},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 20},
]
BETAS = ["server-side-fallback-2026-07-01", "context-management-2025-06-27"]
# Bağlam 200K'ya yaklaşınca en eski araç sonuçlarını (ekran görüntüleri dahil) sunucu temizler
CONTEXT_MANAGEMENT = {"edits": [{"type": "clear_tool_uses_20250919"}]}

SUBAGENT_EXCLUDE = {
    "delegate_tasks", "schedule_task", "cancel_scheduled_task", "memory_save", "memory_delete",
    "create_goal", "goal_set_plan", "goal_step_done", "goal_note", "goal_wait", "goal_complete", "goal_fail",
    "create_trigger", "delete_trigger", "skill_save", "skill_delete",
    "routine_create", "routine_update", "routine_delete", "routine_run_now", "routine_note",
}


# Yalnızca okuyan araçlar aynı turda paralel çalışabilir; diğerleri modelin sırasıyla çalışır
PARALLEL_SAFE = {
    "list_files", "read_document", "read_file", "list_directory", "web_search", "web_fetch",
    "email_list_inbox", "email_read", "mail_list_inbox", "mail_read", "calendar_list_events",
    "contacts_search", "memory_search", "ui_elements", "list_goals", "list_triggers", "list_scheduled_tasks",
    "skill_use", "skill_search", "list_backups", "routine_list", "routine_history",
}

# Claude'da Anthropic sunucu araçları kullanılır; yerel eşdeğerleri yalnızca diğer sağlayıcılar için
LOCAL_WEB_TOOLS = {"web_search", "web_fetch"}


# Kendi sunucu/yerel modellerde (ADA_LLM_TOOLSET=core) gönderilen temel araçlar
CORE_TOOLS = {
    "run_shell", "read_file", "write_file", "edit_file", "list_directory", "read_document", "open_item",
    "web_search", "web_fetch", "run_python", "create_word_document", "create_excel", "create_chart",
    "write_text_file", "create_pdf", "memory_save", "memory_search", "mail_list_inbox", "mail_read", "mail_create_draft",
    "calendar_list_events", "notify",
}


def build_tools(exclude: set[str] = frozenset()) -> list[dict]:
    """Deterministik sıra: araç listesi değişmezse istem önbelleği bozulmaz."""
    if config.PROVIDER == "selfhost" and config.LLM_TOOLSET == "core":
        return [REGISTRY[name].definition() for name in sorted(REGISTRY) if name in CORE_TOOLS and name not in exclude]
    tools: list[dict] = []
    if config.PROVIDER == "anthropic":
        tools += SERVER_TOOLS
        exclude = set(exclude) | LOCAL_WEB_TOOLS
    # Görsel okuyamayan kendi-sunucu modellerinde ekran araçları anlamsızdır
    if config.ENABLE_COMPUTER and (config.PROVIDER != "selfhost" or config.LLM_VISION):
        tools.append({"type": "computer_toolset_20260801"})
    tools += [REGISTRY[name].definition() for name in sorted(REGISTRY) if name not in exclude]
    return tools


# Geçmişi Gemini Content biçiminde tutan sağlayıcılar (ADA-AGI ortak biçim olarak bunu kullanır)
GEMINI_FORMAT = {"gemini", "adaagi"}


def initial_message(text: str) -> dict:
    """Alt ajanlar için sağlayıcı biçiminde ilk kullanıcı mesajı."""
    if config.PROVIDER in GEMINI_FORMAT:
        from .providers import gemini

        return gemini.text_message(text)
    return {"role": "user", "content": text}


def active_model() -> str:
    if config.PROVIDER == "adaagi":
        from . import engines

        n = sum(1 for e in engines.all_engines() if e.enabled and e.available)
        return f"{n} motor"
    return {"gemini": config.GEMINI_MODELS[0], "selfhost": config.LLM_MODEL}.get(config.PROVIDER, config.MODEL)


TOOLS = build_tools()
SUBAGENT_TOOLS = build_tools(SUBAGENT_EXCLUDE)


def subagent_tools() -> list[dict]:
    return SUBAGENT_TOOLS


def subagent_system(role: str) -> list[dict]:
    return [
        {"type": "text", "text": SUBAGENT_ROLES[role] + SUBAGENT_COMMON, "cache_control": {"type": "ephemeral"}},
        _context_block(),
    ]


AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


def _date_block() -> dict:
    now = datetime.now()
    prev_month, prev_year = (now.month - 1, now.year) if now.month > 1 else (12, now.year - 1)
    quarter = (now.month - 1) // 3 + 1
    return {"type": "text", "text": (
        f"Bugün: {now.day} {AYLAR[now.month - 1]} {now.year} {GUNLER[now.weekday()]}, saat {now:%H:%M} ({now:%Y-%m-%d}). "
        f"Bu ay: {AYLAR[now.month - 1]} {now.year}. Geçen ay: {AYLAR[prev_month - 1]} {prev_year}. "
        f"Bu çeyrek: {now.year} Ç{quarter}."
    )}


def _memory_digest(limit: int = 2500) -> str:
    """Kalıcı hafızanın kısa özeti; her istekte bağlama eklenir."""
    from .tools.memory import _load

    try:
        data = _load()
    except Exception:  # noqa: BLE001
        return ""
    lines, total = [], 0
    for key, v in sorted(data.items(), key=lambda kv: kv[1].get("updated", ""), reverse=True):
        line = f"- {key}: {v['content'][:300]}"
        total += len(line)
        if total > limit:
            break
        lines.append(line)
    return "Hafızandakiler (kullanıcı hakkında bilinenler):\n" + "\n".join(lines) if lines else ""


def _os_hint() -> str:
    """İşletim sistemine göre kısa kullanım ipucu (sistem talimatı sabit kalsın diye burada)."""
    if config.IS_WIN:
        return (
            "Sistem: Windows; kabuk komutları PowerShell 5.1 ile çalışır. Dosya aramak için "
            "Get-ChildItem -Path $HOME -Recurse -Filter '*ad*' -ErrorAction SilentlyContinue (mdfind yok); "
            "dosya/uygulama açmak için open_item veya Start-Process; pano: Get-Clipboard/Set-Clipboard; "
            "Masaüstü/Belgeler OneDrive altında olabilir ($HOME\\OneDrive\\Masaüstü)."
        )
    return "Sistem: macOS." if config.IS_MAC else "Sistem: Linux."


def _routines_index() -> str:
    from . import routines

    try:
        return routines.index_text()
    except Exception:  # noqa: BLE001
        return ""


def _autonomy_hint() -> str:
    """Tam özerklikte kullanıcı hiç sorulmasını istemez (sistem talimatı sabit kalsın diye burada)."""
    if config.AUTONOMY != "full":
        return ""
    return (
        "TAM ÖZERKLİK: Kullanıcı sana tam yetki verdi ve hiçbir şey sorulmasını istemiyor. Soru sorma, onay bekleme, "
        "seçenek sunup karar bırakma; belirsizlikte en makul yolu seç, işi bitir ve varsayımlarını sonda kısaca yaz. "
        "Veri bulamazsan sormak yerine doldurulabilir bir şablon hazırla ve eksikliği raporla. Bir adım hata verirse "
        "durma: hatayı oku, nedenini bul, farklı yollarla yeniden dene (en az 3 ayrı yaklaşım); ancak hepsi başarısız "
        "olursa ne denediğini raporla. Kendini bu bilgisayarda çalışan, işini sahiplenen deneyimli bir çalışan gibi gör: "
        "inisiyatif al, fark ettiğin eksikleri tamamla, işi yarım bırakma. Biri ciddi olarak yapay zekâ olup olmadığını "
        "sorarsa dürüst ol. Geri alınamaz işlemlerde (ödeme, hesap silme, başkalarına e-posta gönderme) Sınırlar "
        "bölümündeki kurallar yine geçerlidir."
    )


def _context_block() -> dict:
    """Değişken bağlam (tarih, sistem, hafıza, beceri ve rutin dizini). Önbellek noktasından SONRA gelir."""
    from . import skills

    parts = [_date_block()["text"], _os_hint(), _autonomy_hint(), _memory_digest(), skills.index_text(), _routines_index()]
    return {"type": "text", "text": "\n\n".join(p for p in parts if p)}


def _system() -> list[dict]:
    return [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}, _context_block()]


TOOL_LABELS = {
    "web_search": "Web'de arıyor",
    "web_fetch": "Sayfayı okuyor",
    "list_files": "Dosyaları listeliyor",
    "read_document": "Belgeyi okuyor",
    "write_text_file": "Dosya yazıyor",
    "create_word_document": "Word belgesi hazırlıyor",
    "create_excel": "Excel tablosu hazırlıyor",
    "create_presentation": "Sunum hazırlıyor",
    "create_chart": "Grafik çiziyor",
    "create_pdf": "PDF hazırlıyor",
    "pdf_merge": "PDF birleştiriyor",
    "pdf_extract_pages": "PDF sayfalarını ayırıyor",
    "run_python": "Analiz kodu çalıştırıyor",
    "run_shell": "Komut çalıştırıyor",
    "read_file": "Dosyayı okuyor",
    "write_file": "Dosya yazıyor",
    "edit_file": "Dosyayı düzenliyor",
    "list_directory": "Klasörü listeliyor",
    "delete_path": "Çöp Sepeti'ne taşıyor",
    "open_item": "Açıyor",
    "run_applescript": "Uygulamayı yönetiyor",
    "notify": "Bildirim gönderiyor",
    "speak": "Sesli okuyor",
    "email_list_inbox": "Gelen kutusuna bakıyor",
    "email_read": "E-postayı okuyor",
    "email_create_draft": "E-posta taslağı yazıyor",
    "mail_list_inbox": "Mail'e bakıyor",
    "mail_read": "E-postayı okuyor",
    "mail_create_draft": "E-posta taslağı hazırlıyor",
    "mail_send": "E-posta gönderiyor",
    "calendar_list_events": "Takvime bakıyor",
    "calendar_create_event": "Takvime ekliyor",
    "create_calendar_event": "Takvim daveti oluşturuyor",
    "reminders_add": "Anımsatıcı ekliyor",
    "notes_create": "Not oluşturuyor",
    "contacts_search": "Kişilerde arıyor",
    "memory_save": "Hafızaya kaydediyor",
    "memory_search": "Hafızaya bakıyor",
    "memory_delete": "Hafızadan siliyor",
    "delegate_tasks": "Alt ajanlara dağıtıyor",
    "schedule_task": "Görev planlıyor",
    "list_scheduled_tasks": "Planları listeliyor",
    "cancel_scheduled_task": "Planı iptal ediyor",
    "create_goal": "Hedef oluşturuyor",
    "list_goals": "Hedeflere bakıyor",
    "goal_set_plan": "Plan kuruyor",
    "goal_step_done": "Adımı tamamladı",
    "goal_note": "Not alıyor",
    "goal_wait": "Beklemeye alıyor",
    "goal_complete": "Denetçiye kontrol ettiriyor",
    "goal_fail": "Hedefi kapatıyor",
    "create_trigger": "Tetikleyici kuruyor",
    "list_triggers": "Tetikleyicilere bakıyor",
    "delete_trigger": "Tetikleyiciyi siliyor",
    "skill_save": "Beceri kaydediyor",
    "skill_use": "Beceriyi kullanıyor",
    "skill_search": "Becerilerde arıyor",
    "skill_delete": "Beceriyi siliyor",
    "routine_create": "Rutin oluşturuyor",
    "routine_list": "Rutinlere bakıyor",
    "routine_update": "Rutini güncelliyor",
    "routine_delete": "Rutini siliyor",
    "routine_run_now": "Rutini başlatıyor",
    "routine_note": "Rutine not ekliyor",
    "routine_history": "Rutin geçmişine bakıyor",
    "list_backups": "Yedeklere bakıyor",
    "restore_backup": "Yedeği geri yüklüyor",
    "ui_elements": "Uygulama öğelerini okuyor",
    "ui_click": "Öğeye tıklıyor",
    "ui_type": "Alana yazıyor",
    "screenshot": "Ekrana bakıyor",
    "zoom": "Ekranı yakınlaştırıyor",
    "left_click": "Tıklıyor",
    "right_click": "Sağ tıklıyor",
    "middle_click": "Orta tıklıyor",
    "double_click": "Çift tıklıyor",
    "triple_click": "Üç kez tıklıyor",
    "left_click_drag": "Sürüklüyor",
    "mouse_move": "Fareyi taşıyor",
    "left_mouse_down": "Fare tuşuna basıyor",
    "left_mouse_up": "Fare tuşunu bırakıyor",
    "cursor_position": "İmleç konumuna bakıyor",
    "scroll": "Kaydırıyor",
    "type": "Yazıyor",
    "key": "Tuşa basıyor",
    "hold_key": "Tuşu basılı tutuyor",
    "wait": "Bekliyor",
}

_MODEL_INTERNAL = {"thinking", "redacted_thinking", "tool_use"}


def _serialize(content) -> list[dict]:
    """Yanıt bloklarını kalıcı ve API'ye geri gönderilebilir dict'lere çevirir.

    Sunucu tarafı yedek model (fallback) devreye girdiyse, son `fallback` bloğundan önceki
    model-içi bloklar (düşünce, araç çağrısı, eşsiz sunucu aracı) geri gönderilmemelidir.
    """
    blocks = [b.model_dump(mode="json", exclude_none=True) for b in content]
    fallback_idx = [i for i, b in enumerate(blocks) if b["type"] == "fallback"]
    if not fallback_idx:
        return blocks
    boundary = fallback_idx[-1]
    result_ids = {b.get("tool_use_id") for b in blocks if b["type"].endswith("_tool_result")}
    kept = []
    for i, b in enumerate(blocks):
        if i < boundary and (
            b["type"] in _MODEL_INTERNAL
            or (b["type"] == "server_tool_use" and b.get("id") not in result_ids)
        ):
            continue
        kept.append(b)
    return kept


def _label(name: str) -> str:
    return TOOL_LABELS.get(name, name)


def _short(args) -> str:
    if not isinstance(args, dict):
        return ""
    for key in ("command", "filename", "path", "query", "url", "title", "subject", "name", "key", "uid", "text", "coordinate", "app", "routine_id"):
        if key in args and args[key] not in (None, ""):
            value = args[key]
            return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))[:160]
    return ""


# ---------------------------------------------------------------- araç çalıştırma

async def _run_tool(name: str, args) -> ToolResult:
    """Güvenlik kontrolleri + onay + çalıştırma; her çağrı işlem günlüğüne yazılır."""
    approved: list[bool] = []
    result = await _run_tool_inner(name, args, approved)
    safety.log(name, args, ok=not result.is_error, result=result.content, approved=approved[0] if approved else None)
    if name in injection.READ_TOOLS and not result.is_error:
        hits = injection.scan(result.content)
        if hits:
            result.content += injection.notice(name, hits)
            safety.log("şüpheli_talimat", {"araç": name, "kaynak": _short(args)}, ok=True, result=" | ".join(hits)[:300])
            approval.emit({"type": "step_error", "label": "Şüpheli talimat tespit edildi (uygulanmayacak)", "detail": hits[0][:200]})
    return result


async def _run_tool_inner(name: str, args, approved: list[bool]) -> ToolResult:
    tool = REGISTRY.get(name)
    if tool is None:
        return ToolResult(f"Bilinmeyen araç: {name}", is_error=True)
    if safety.is_halted():
        return ToolResult("ADA acil durdurma modunda; hiçbir işlem yapılmıyor. Kullanıcı 'Devam et' diyene kadar dur.", is_error=True)
    problem = validate_input(tool, args)
    if problem:
        return ToolResult(f"INVALID_INPUT: {problem} Girdiyi düzeltip tekrar deneyin.", is_error=True)
    limit = safety.check_limit(name)
    if limit:
        return ToolResult(limit, is_error=True)

    reason = None
    if tool.risk is not None and config.AUTONOMY != "full":
        try:
            reason = tool.risk(args)
        except Exception:  # noqa: BLE001
            reason = None
        if config.AUTONOMY == "ask" and not reason:
            reason = "Onay modu açık: yan etkisi olan her işlem onay ister."
    if reason:
        ok = await approval.request(
            _label(name), f"{reason}\n\n{json.dumps(args, ensure_ascii=False)[:800]}", key=approval.allow_key(name, reason)
        )
        approved.append(ok)
        if not ok:
            return ToolResult("Kullanıcı bu işlemi onaylamadı. Alternatif bir yol öner veya kullanıcıya sor.", is_error=True)

    try:
        if inspect.iscoroutinefunction(tool.handler):
            result = await tool.handler(**args)
        else:
            result = await asyncio.to_thread(tool.handler, **args)
    except ToolError as e:
        return ToolResult(str(e), is_error=True)
    except TypeError as e:
        return ToolResult(f"Geçersiz parametre: {e}", is_error=True)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - hatayı modele iletip kendini düzeltmesini sağlarız
        return ToolResult(f"{type(e).__name__}: {e}", is_error=True)
    return result if isinstance(result, ToolResult) else ToolResult(str(result))


async def _run_computer_batch(blocks: list[dict], results: dict[str, ToolResult]) -> None:
    """Ekran eylemleri sırayla çalışır; ilk hatadan sonrakiler çalıştırılmaz (API kuralı)."""
    if config.AUTONOMY == "ask":
        detail = "\n".join(f"- {_label(b['name'])} {_short(b.get('input'))}" for b in blocks)
        if not await approval.request("Ekran kontrolü", detail):
            for b in blocks:
                results[b["id"]] = ToolResult("Kullanıcı ekran kontrolünü onaylamadı.", is_error=True)
            return
    failed = False
    for b in blocks:
        if failed:
            results[b["id"]] = ToolResult("Not executed: an earlier computer action in this turn failed.", is_error=True)
            continue
        blocked = (
            "ADA acil durdurma modunda; ekran kontrolü yapılmıyor." if safety.is_halted() else safety.check_limit(b["name"])
        )
        r = ToolResult(blocked, is_error=True) if blocked else await asyncio.to_thread(computer.execute, b["name"], b.get("input") or {})
        safety.log(f"ekran:{b['name']}", b.get("input") or {}, ok=not r.is_error, result=r.content)
        results[b["id"]] = r
        if r.image:
            approval.emit({"type": "screenshot", "data": base64.b64encode(r.image).decode()})
        if r.is_error:
            failed = True


async def _run_custom(b: dict, results: dict[str, ToolResult]) -> None:
    results[b["id"]] = await _run_tool(b["name"], b.get("input"))


async def _execute_all(tool_uses: list[dict], results: dict[str, ToolResult]) -> None:
    computer_blocks = [b for b in tool_uses if b.get("toolset_name") == computer.TOOLSET_NAME]
    custom = [b for b in tool_uses if b.get("toolset_name") != computer.TOOLSET_NAME]
    if any(b["name"] not in PARALLEL_SAFE for b in custom):
        # Yan etkisi olan bir araç varsa tur, modelin verdiği sırayla çalışır
        # (ör. dosya yaz → düzenle → goal_complete dosyayı denetlesin)
        for b in custom:
            await _run_custom(b, results)
        custom = []
    jobs = [_run_custom(b, results) for b in custom]
    if computer_blocks:
        jobs.append(_run_computer_batch(computer_blocks, results))
    await asyncio.gather(*jobs)


async def _run_tools(tool_uses: list[dict], results: dict[str, ToolResult]) -> AsyncIterator[dict]:
    """Araçları arka planda çalıştırırken yan kanaldan gelen olayları (onay, alt ajan adımı,
    ekran görüntüsü) anında akışa verir."""
    side: asyncio.Queue = asyncio.Queue()
    token = approval.SIDE.set(side)
    try:
        task = asyncio.ensure_future(_execute_all(tool_uses, results))  # bağlam burada kopyalanır
    finally:
        approval.SIDE.reset(token)
    try:
        while True:
            if not side.empty():
                yield side.get_nowait()
                continue
            if task.done():
                break
            getter = asyncio.ensure_future(side.get())
            done, _ = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                yield getter.result()
            else:
                getter.cancel()
    finally:
        if not task.done():
            task.cancel()
    task.result()  # araç katmanı hataları yakalar; buraya yalnızca beklenmeyenler düşer


def _result_block(b: dict, r: ToolResult) -> dict:
    content: list[dict] = [{"type": "text", "text": r.content or "OK"}]
    if r.image:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(r.image).decode()},
        })
    block = {"type": "tool_result", "tool_use_id": b["id"], "content": content, "is_error": r.is_error}
    if b.get("toolset_name"):
        block["toolset_name"] = b["toolset_name"]
    return block


# ---------------------------------------------------------------- ana döngü

async def agent_loop(
    messages: list[dict],
    *,
    system: list[dict],
    tools: list[dict],
    ui_entry: dict,
    save: Callable[[], None],
    max_steps: int,
    kind: str | None = None,
    prefer: str | None = None,
    on_engine: Callable | None = None,
    previous_kind: str | None = None,
) -> AsyncIterator[dict]:
    """`messages` sona ekleme yapılarak güncellenir; her turda `save()` çağrılır.
    ADA-AGI modunda `kind` (görev türü), `prefer` (önceki motor) ve `previous_kind` (önceki turun türü)
    yönlendirmeye ipucudur."""
    if config.PROVIDER == "adaagi":
        from . import router

        async for ev in router.run(messages, system=system, tools=tools, ui_entry=ui_entry, save=save, max_steps=max_steps,
                                   kind=kind, prefer=prefer, on_engine=on_engine, previous_kind=previous_kind):
            yield ev
        return
    if config.PROVIDER == "gemini" and config.QWEN_FALLBACK:
        from .providers.qwen_fallback import gemini_with_qwen

        async for ev in gemini_with_qwen(messages, system=system, tools=tools, ui_entry=ui_entry, save=save, max_steps=max_steps):
            yield ev
        return
    if config.PROVIDER == "gemini":
        from .providers.gemini import gemini_loop

        async for ev in gemini_loop(messages, system=system, tools=tools, ui_entry=ui_entry, save=save, max_steps=max_steps):
            yield ev
        return
    if config.PROVIDER == "selfhost":
        from .providers.selfhost import selfhost_loop

        async for ev in selfhost_loop(messages, system=system, tools=tools, ui_entry=ui_entry, save=save, max_steps=max_steps):
            yield ev
        return

    json_retries = 0
    step = 0
    while step < max_steps:
        step += 1
        try:
            async with get_client().beta.messages.stream(
                model=config.MODEL,
                max_tokens=config.MAX_TOKENS,
                system=system,
                tools=tools,
                messages=messages,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": config.EFFORT},
                cache_control={"type": "ephemeral"},
                betas=BETAS,
                # Güvenlik sınıflandırıcısı reddederse istek sunucu tarafında uygun modele yönlenir
                fallbacks="default",
                context_management=CONTEXT_MANAGEMENT,
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            ui_entry["text"] += event.delta.text
                            yield {"type": "text", "text": event.delta.text}
                        elif event.delta.type == "thinking_delta" and event.delta.thinking:
                            yield {"type": "thinking", "text": event.delta.thinking}
                    elif event.type == "content_block_stop":
                        block = event.content_block
                        if block.type == "server_tool_use":
                            inp = block.model_dump().get("input") or {}
                            detail = inp.get("query") or inp.get("url") or ""
                            ui_entry["steps"].append({"label": _label(block.name), "detail": detail})
                            yield {"type": "step", "label": _label(block.name), "detail": detail}
                        elif block.type == "text" and ui_entry["text"] and not ui_entry["text"].endswith("\n"):
                            ui_entry["text"] += "\n\n"
                            yield {"type": "text", "text": "\n\n"}
                response = await stream.get_final_message()
            json_retries = 0
        except ValueError:
            # Araç girdisi hiç ayrıştırılamayan JSON geldi; turu yeniden dene (sınırlı)
            json_retries += 1
            if json_retries > 2:
                raise
            continue

        content = _serialize(response.content)
        tool_uses = [b for b in content if b["type"] == "tool_use"]

        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": content})
            save()
            continue

        if response.stop_reason in ("refusal", "max_tokens") or not tool_uses:
            if tool_uses:
                # Yarım kalmış araç çağrıları çalıştırılmaz ve geçmişe eklenmez
                content = [b for b in content if b["type"] != "tool_use"]
            if content:
                messages.append({"role": "assistant", "content": content})
            if response.stop_reason == "refusal":
                yield {"type": "error", "message": "Bu istek güvenlik politikası nedeniyle tamamlanamadı."}
            elif response.stop_reason == "max_tokens":
                yield {"type": "error", "message": "Yanıt uzunluk sınırına ulaştı; 'devam et' yazarak sürdürebilirsiniz."}
            save()
            return

        for b in tool_uses:
            step_ev = {"type": "step", "label": _label(b["name"]), "detail": _short(b.get("input"))}
            ui_entry["steps"].append({"label": step_ev["label"], "detail": step_ev["detail"]})
            yield step_ev

        results: dict[str, ToolResult] = {}
        async for ev in _run_tools(tool_uses, results):
            if ev["type"] in ("step", "step_error"):
                ui_entry["steps"].append({k: ev[k] for k in ("label", "detail", "agent") if k in ev})
            elif ev["type"] == "files":
                new = [f for f in ev["files"] if f not in ui_entry["files"]]
                if not new:
                    continue
                ui_entry["files"].extend(new)
                ev = {**ev, "files": new}
            yield ev

        tool_results = []
        for b in tool_uses:
            r = results.get(b["id"]) or ToolResult("Araç sonuç üretmedi.", is_error=True)
            tool_results.append(_result_block(b, r))
            if r.is_error:
                ui_entry["steps"].append({"label": _label(b["name"]) + " (hata)", "detail": r.content[:300]})
                yield {"type": "step_error", "label": _label(b["name"]), "detail": r.content[:300]}
            new_files = [f for f in r.files if f not in ui_entry["files"]]
            if new_files:
                ui_entry["files"].extend(new_files)
                yield {"type": "files", "files": new_files}

        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": tool_results})
        save()

    yield {"type": "error", "message": f"Adım sınırına ({max_steps}) ulaşıldı. 'devam et' yazarak sürdürebilirsiniz."}


async def run_turn(
    conv: Conversation, user_content: list[dict], display_text: str, max_minutes: float | None = None
) -> AsyncIterator[dict]:
    """Bir kullanıcı mesajını işler ve görev bitene kadar olay akışı üretir.
    `max_minutes` verilmezse ADA_MAX_TURN_MINUTES kullanılır (rutinler kendi sürelerini verir)."""
    limit_minutes = max_minutes or config.MAX_TURN_MINUTES
    if not conv.messages and conv.title == "Yeni görev":
        conv.title = (display_text.strip().splitlines() or ["Yeni görev"])[0][:60]
    if not conv.messages:
        conv.provider = config.PROVIDER
    conv.ui.append({"role": "user", "text": display_text})
    ui_entry = {"role": "assistant", "text": "", "steps": [], "files": []}
    conv.ui.append(ui_entry)

    def fail(msg: str) -> dict:
        ui_entry["text"] += f"\n\n_{msg}_"
        return {"type": "error", "message": msg}

    if {conv.provider, config.PROVIDER} <= GEMINI_FORMAT:
        conv.provider = config.PROVIDER  # aynı geçmiş biçimi: Gemini ↔ ADA-AGI arasında görev sürer
    elif conv.provider == "selfhost" and config.PROVIDER in GEMINI_FORMAT or conv.provider in GEMINI_FORMAT and config.PROVIDER == "selfhost":
        # Gemini/ADA-AGI ile kendi sunucu (Qwen) arasında geçmiş çevrilebilir: eski görevlere devam edilir
        from .providers import qwen_fallback

        if config.PROVIDER in GEMINI_FORMAT:
            conv.messages = qwen_fallback.to_gemini(conv.messages)
        else:
            conv.messages = qwen_fallback.to_openai(conv.messages, config.LLM_VISION)
        conv.provider = config.PROVIDER
    if conv.provider != config.PROVIDER:
        yield fail(f"Bu görev {conv.provider} ile başlatılmıştı; şu an {config.PROVIDER} etkin. Lütfen yeni bir görev açın.")
        conv.save()
        yield {"type": "done"}
        return

    if config.PROVIDER in GEMINI_FORMAT:
        from .providers import gemini

        msg = gemini.user_content(user_content)
        if conv.messages and conv.messages[-1].get("role") == "user":
            # Önceki tur araç sonucuyla bitmişse (ör. çevrilmiş eski görev) yeni mesaj aynı kullanıcı turuna eklenir
            conv.messages[-1]["parts"] = conv.messages[-1].get("parts", []) + msg.get("parts", [])
        else:
            conv.messages.append(msg)
    elif config.PROVIDER == "selfhost":
        from .providers import selfhost

        conv.messages.append(selfhost.user_content(user_content))
    else:
        conv.messages.append({"role": "user", "content": user_content})
    conv.save()

    try:
        # Süre sınırı: takılan aramalar/döngüler turu sonsuza kadar uzatmasın (geçmiş yalnızca
        # tamamlanan adımlarla güncellendiği için iptal tutarlı bir noktada kalır)
        def remember_engine(eng, kind: str = "") -> None:
            conv.engine = eng.id  # sonraki turda aynı motor öne çıkar (süreklilik)
            conv.kind = kind or conv.kind

        async with asyncio.timeout(limit_minutes * 60):
            async for ev in agent_loop(
                conv.messages, system=_system(), tools=TOOLS, ui_entry=ui_entry, save=conv.save, max_steps=config.MAX_STEPS,
                prefer=conv.engine or None, on_engine=remember_engine, previous_kind=conv.kind or None,
            ):
                if ev["type"] == "error":
                    ui_entry["text"] += f"\n\n_{ev['message']}_"
                yield ev
    except TimeoutError:
        yield fail(
            f"Görev {limit_minutes:g} dakikalık süre sınırına ulaştı ve durduruldu. Yapılanlar yukarıda; "
            "'devam et' yazarak sürdürebilir veya isteği daraltabilirsiniz."
        )
    except asyncio.CancelledError:
        ui_entry["text"] += "\n\n_Görev kullanıcı tarafından durduruldu._"
        conv.save()
        raise
    except anthropic.AuthenticationError:
        yield fail("API anahtarı geçersiz. .env dosyasında ANTHROPIC_API_KEY'i kontrol edin.")
    except anthropic.RateLimitError:
        yield fail("Hız sınırına takıldı. Biraz bekleyip tekrar deneyin.")
    except anthropic.APIStatusError as e:
        yield fail(f"API hatası ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        yield fail("Anthropic API'ye bağlanılamadı. İnternet bağlantınızı kontrol edin.")
    except (anthropic.AnthropicError, TypeError) as e:
        # Kimlik bilgisi hiç yoksa SDK istek anında TypeError fırlatır
        if isinstance(e, TypeError) and "authentication" not in str(e):
            raise
        yield fail("Anthropic API anahtarı bulunamadı. .env dosyasına ANTHROPIC_API_KEY ekleyip sunucuyu yeniden başlatın.")
    except Exception as e:  # noqa: BLE001 - Gemini / kendi sunucu hataları
        if config.PROVIDER == "adaagi":
            from . import router

            if isinstance(e, router.AllEnginesFailed):
                yield fail("Hiçbir yapay zekâ motoru bu turu bitiremedi: " + "; ".join(e.reasons)
                           + ". Biraz sonra tekrar deneyin ya da Ayarlar → Yapay zekâ'dan başka bir motor ekleyin.")
            elif isinstance(e, router.NoEngine):
                yield fail(str(e))
            else:
                raise
        elif config.PROVIDER == "gemini":
            yield fail(_gemini_error(e))
        elif config.PROVIDER == "selfhost":
            from .providers.selfhost import describe_error

            msg = describe_error(e)
            if msg is None:
                raise
            yield fail(msg)
        else:
            raise
    finally:
        conv.save()
    yield {"type": "done"}


def _gemini_error(e: Exception) -> str:
    from google.genai import errors

    from .providers.gemini import GeminiQuotaExhausted

    if config.QWEN_FALLBACK:
        from .providers.qwen_fallback import QwenFailed

        if isinstance(e, QwenFailed):
            return f"{e.gemini_reason}; yedek Qwen de başarısız oldu: {e.qwen_message}"
    if isinstance(e, GeminiQuotaExhausted):
        return "Gemini ücretsiz kotası doldu (tüm yedek modeller). Birkaç dakika sonra ya da yarın tekrar deneyin."
    if isinstance(e, RuntimeError) and "GEMINI_API_KEY" in str(e):
        return "GEMINI_API_KEY bulunamadı. .env dosyasına ekleyip sunucuyu yeniden başlatın."
    if isinstance(e, errors.ClientError):
        if e.code == 402:
            return ("Gemini projesinin ön ödemeli kredisi bitti. AI Studio'da (https://ai.studio/projects) kredi yükleyin "
                    "ya da faturalandırması olmayan bir projede yeni anahtar oluşturun (ücretsiz katman).")
        if e.code in (400, 401, 403) and "API key" in str(e):
            return "Gemini API anahtarı geçersiz. .env dosyasındaki GEMINI_API_KEY'i kontrol edin."
        return f"Gemini isteği reddetti ({e.code}): {e.message}"
    if isinstance(e, errors.ServerError):
        return f"Gemini sunucu hatası ({e.code}); biraz sonra tekrar deneyin."
    return f"{type(e).__name__}: {e}"


def build_user_content(text: str, attachments: list[str]) -> list[dict]:
    """Kullanıcı metnini ve eklerini API içerik bloklarına çevirir. Görseller doğrudan gösterilir."""
    from .tools import safe_path

    blocks: list[dict] = []
    listed = []
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
    for rel_path in attachments:
        p = safe_path(rel_path, must_exist=True)
        if p.suffix.lower() in media and p.stat().st_size < 5_000_000:
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media[p.suffix.lower()], "data": base64.standard_b64encode(p.read_bytes()).decode()},
            })
        listed.append(rel_path)
    if listed:
        text += "\n\n[Ekli dosyalar: " + ", ".join(listed) + "]"
    blocks.append({"type": "text", "text": text})
    return blocks


def dumps(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
