"""Rutinler: ADA'nın her gün (veya seçilen gün/saatlerde) kullanıcı yokken kendi başına yaptığı işler."""

from __future__ import annotations

import asyncio

from . import ToolError, safe_path, tool


def _attended(action: str) -> None:
    """Rutin, tetikleyici, hedef ya da zamanlanmış görev çalışmaları (kullanıcı başında değil) rutin kuramaz,
    değiştiremez, silemez, başlatamaz: okunan bir içeriğe gizlenmiş talimat kendine kalıcı bir iş açamasın."""
    from .. import runtime

    if runtime.UNATTENDED.get():
        raise ToolError(
            f"Kullanıcı başında değilken (arka plan çalışması) rutin {action}. "
            "Gerekiyorsa önerini rapora yaz; kullanıcı sohbetten karar versin."
        )


def _get(routine_id: str) -> dict:
    from .. import routines

    r = routines.get(routine_id)
    if r is None:
        raise ToolError(f"Rutin bulunamadı: {routine_id}")
    return r


def _line(r: dict) -> str:
    from .. import routines

    s = routines.summary(r)
    state = "şu an çalışıyor" if s["running"] else ("aktif" if s["enabled"] else "kapalı")
    mark = {"ok": "✓", "error": "✗", "cancelled": "■ iptal"}.get(s["last_status"] or "", "")
    last = f"{mark} {s['last_run']}".strip() if s["last_run"] else "yok"
    text = (
        f"[{s['id']}] {s['name']} — {s['description']} — {state} — sonraki: {s['next_run'] or '-'}"
        f" — son çalışma: {last} — dosya: {s['file']}"
    )
    if s["invalid"]:
        text += f"\n  GEÇERSİZ (çalışmaz): {s['invalid']}"
    return text


@tool(
    "routine_create",
    "Düzenli tekrarlanacak gerçek bir işi RUTİN olarak kaydeder: ADA o işi her gün (veya seçilen günlerde, saatlerde "
    "ya da aralıkla) kullanıcı başında olmadan, soru sormadan kendi başına yapar; her çalışmanın raporunu tutar, "
    "önceki çalışmalardan öğrenir, hata olursa bir kez yeniden dener. Kullanıcı bir işin düzenli yapılmasını "
    "istediğinde ('her gün', 'her sabah', 'hafta içi her akşam', 'bu dosyadaki işi her gün yap') ya da işi anlatan "
    "bir .md dosyası yükleyip her gün yapılmasını istediğinde kullan. Gerçek tekrarlayan işlerde schedule_task "
    "yerine bunu tercih et (schedule_task basit hatırlatmalar ve tek seferlik zamanlı işler içindir). instructions "
    "veya source'tan tam olarak birini ver. instructions eksiksiz ve kendi başına anlaşılır olmalı (kaynaklar, "
    "adımlar, çıktı biçimi, kalite ölçütü): rutin kullanıcı yokken çalışır.",
    {
        "name": {"type": "string", "description": "Kısa ad, ör. 'Sabah haber özeti'"},
        "instructions": {"type": "string", "description": "Yapılacak işin markdown talimatı (kullanıcının isteğinden eksiksiz yazılmış)"},
        "source": {"type": "string", "description": "Yüklenmiş .md/.txt iş dosyasının çalışma alanı yolu, ör. uploads/is.md"},
        "time": {"type": "string", "description": "Saat 'SS:DD'; birden çok için '09:00, 18:00' (varsayılan 09:00)"},
        "weekdays": {"type": "array", "items": {"type": "integer"}, "description": "0=Pzt ... 6=Paz; boş = her gün"},
        "interval_minutes": {"type": "integer", "description": "Saat yerine her N dakikada bir çalışsın (en az 5)"},
        "run_now": {"type": "boolean", "description": "Kaydedince hemen bir kez de çalıştır"},
    },
)
async def routine_create(
    name: str = "", instructions: str = "", source: str = "", time: str = "",
    weekdays: list[int] | None = None, interval_minutes: int = 0, run_now: bool = False,
) -> str:
    from .. import routines

    _attended("oluşturulamaz")
    if bool(instructions.strip()) == bool(source.strip()):
        raise ToolError("instructions veya source alanlarından tam olarak birini verin.")
    content, filename = instructions, None
    if source.strip():
        p = safe_path(source, must_exist=True)
        if not p.is_file() or p.suffix.lower() not in (".md", ".markdown", ".txt"):
            raise ToolError("source bir .md, .markdown veya .txt dosyası olmalı.")
        try:
            content = routines.decode(await asyncio.to_thread(p.read_bytes))
        except ValueError as e:
            raise ToolError(str(e))
        filename = p.name
    overrides = {k: v for k, v in {"name": name.strip(), "time": time.strip(), "interval_minutes": interval_minutes}.items() if v}
    if weekdays is not None:
        overrides["weekdays"] = weekdays
    try:
        r = await asyncio.to_thread(routines.create, content, filename, overrides)
    except ValueError as e:
        raise ToolError(str(e))
    msg = (
        f"Rutin kaydedildi [{r['id']}]: {r['name']} — {routines.describe(r['spec'])}; "
        f"sonraki çalışma {r['next_run'] or '-'}; dosya: {r['file']}"
    )
    if run_now:
        try:
            await routines.run_now(r["id"])
        except (RuntimeError, LookupError) as e:
            return f"{msg} — şimdi başlatılamadı: {e}"
        msg += " — şimdi başlatıldı"
    return msg


@tool("routine_list", "Kayıtlı rutinleri takvimleri, durumları ve son çalışmalarıyla listeler.", {})
def routine_list() -> str:
    from .. import routines

    items = routines.sync()
    if not items:
        return "Kayıtlı rutin yok (rutin dosyaları workspace/rutinler/ klasöründe durur)."
    return "\n".join(_line(r) for r in sorted(items, key=lambda x: x["name"].casefold()))


@tool(
    "routine_history",
    "Bir rutinin son çalışmalarını (durum, özet, rapor, üretilen dosyalar, hata) ve öğrenilen notlarını gösterir.",
    {"routine_id": {"type": "string"}, "limit": {"type": "integer", "description": "Kaç çalışma (varsayılan 5)"}},
    ["routine_id"],
)
def routine_history(routine_id: str, limit: int = 5) -> str:
    r = _get(routine_id)
    lines = [_line(r), "", "Son çalışmalar:"]
    runs = r.get("runs", [])[: max(1, min(int(limit), 30))]
    for x in runs:
        mark = {"ok": "✓ tamamlandı", "error": "✗ hata", "cancelled": "■ iptal", "running": "… çalışıyor"}.get(x.get("status"), x.get("status"))
        extra = " · gecikmeli" if x.get("late") else ""
        if x.get("duration_s") is not None:
            extra += f" · {round(x['duration_s'])} sn"
        lines.append(f"- {x.get('started')} {mark}{extra} — {(x.get('summary') or x.get('error') or '')[:300]}")
        if x.get("report"):
            lines.append(f"  rapor: {x['report']}")
        if x.get("files"):
            lines.append(f"  dosyalar: {', '.join(x['files'][:10])}")
        if x.get("retry_scheduled"):
            lines.append("  (15 dakika içinde yeniden denendi/denenecek)")
    if not runs:
        lines.append("- henüz çalışmadı")
    notes = r.get("notes", [])
    lines += ["", "Öğrenilen notlar:"] + ([f"- {n['time'][:16]}: {n['text']}" for n in notes] or ["- yok"])
    return "\n".join(lines)


@tool(
    "routine_update",
    "Rutini değiştirir: durdurma/yeniden açma (enabled), saat, günler, aralık, ad ya da talimat metni. instructions "
    "verilirse yalnızca talimat değişir, ayarlar korunur. 'Sabah rutinini durdur', 'saatini 8'e al', 'cumartesileri "
    "de çalışsın' gibi isteklerde kullan.",
    {
        "routine_id": {"type": "string"},
        "enabled": {"type": "boolean", "description": "false: kendiliğinden çalışmaz (dosya ve geçmiş kalır)"},
        "time": {"type": "string", "description": "'SS:DD' ya da '09:00, 18:00'"},
        "weekdays": {"type": "array", "items": {"type": "integer"}, "description": "0=Pzt ... 6=Paz; boş = her gün"},
        "interval_minutes": {"type": "integer", "description": "Saat yerine her N dakikada bir (en az 5)"},
        "name": {"type": "string"},
        "instructions": {"type": "string", "description": "Yeni talimat metni (markdown); ayarlar korunur"},
    },
    ["routine_id"],
)
def routine_update(
    routine_id: str, enabled: bool | None = None, time: str | None = None, weekdays: list[int] | None = None,
    interval_minutes: int | None = None, name: str | None = None, instructions: str | None = None,
) -> str:
    from .. import routines

    _attended("değiştirilemez")
    _get(routine_id)
    try:
        r = routines.update(
            routine_id, enabled=enabled, time=time, weekdays=weekdays, interval_minutes=interval_minutes,
            name=name, body=instructions,
        )
    except ValueError as e:
        raise ToolError(str(e))
    if r is None:
        raise ToolError(f"Rutin bulunamadı: {routine_id}")
    return "Rutin güncellendi: " + _line(r)


@tool(
    "routine_delete",
    "Rutini ve dosyasını siler (dosyanın yedeği alınır; eski raporlar outputs/rutinler/ altında kalır). Yalnızca "
    "durdurmak için routine_update ile enabled=false kullan.",
    {"routine_id": {"type": "string"}},
    ["routine_id"],
)
def routine_delete(routine_id: str) -> str:
    from .. import routines

    _attended("silinemez")
    r = _get(routine_id)
    try:
        if not routines.delete(routine_id):
            raise ToolError(f"Rutin bulunamadı: {routine_id}")
    except ValueError as e:
        raise ToolError(str(e))
    return f"Rutin silindi: {r['name']} ({r['file']}; yedeği alındı)."


@tool(
    "routine_run_now",
    "Rutini beklemeden şimdi bir kez çalıştırır (arka planda, ayrı bir görev olarak; sonucu rutin geçmişine yazılır).",
    {"routine_id": {"type": "string"}},
    ["routine_id"],
)
async def routine_run_now(routine_id: str) -> str:
    from .. import routines

    _attended("başlatılamaz")
    r = await asyncio.to_thread(_get, routine_id)
    try:
        entry = await routines.run_now(routine_id)
    except (RuntimeError, LookupError) as e:
        raise ToolError(str(e))
    return f"Rutin başlatıldı: {r['name']} (konuşma {entry['conv_id']}). Sonucu bitince routine_history ile görebilirsin."


@tool(
    "routine_note",
    "Rutine kalıcı bir ders/not ekler; sonraki her çalışmada talimatla birlikte verilir. Rutin çalışırken öğrendiğin, "
    "ertesi gün işi kolaylaştıracak bilgileri kaydet: işe yarayan kaynak veya komut, kaçınılacak tuzak, kullanıcı "
    "tercihi. Kısa ve genel geçer yaz (o güne özel sonuçları değil).",
    {"routine_id": {"type": "string"}, "note": {"type": "string"}},
    ["routine_id", "note"],
)
def routine_note(routine_id: str, note: str) -> str:
    from .. import routines, runtime

    current = routines.CURRENT.get()
    if current and current != routine_id:
        raise ToolError(f"Bu çalışma yalnızca kendi rutinine ({current}) not ekleyebilir.")
    if runtime.UNATTENDED.get() and not current:
        raise ToolError("Kullanıcı başında değilken yalnızca çalışan rutin kendine not ekleyebilir.")
    _get(routine_id)
    try:
        r = routines.add_note(routine_id, note)
    except ValueError as e:
        raise ToolError(str(e))
    if r is None:
        raise ToolError(f"Rutin bulunamadı: {routine_id}")
    return f"Not kaydedildi; sonraki çalışmalarda kullanılacak (toplam {len(r['notes'])} not)."
