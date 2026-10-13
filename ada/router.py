"""ADA-AGI üst katmanı: her işi en uygun yapay zekâ motoruna verir, motor yapamazsa diğerine devreder.

Akış (bir tur):
  1. Görev türü belirlenir (sohbet, araştırma, kod, ofis, ekran, derin düşünme, gizli) — kelime
     kurallarıyla, ek model çağrısı yapmadan; alt ajanlar rolünden gelen türü kullanır.
  2. Açık ve kullanılabilir motorlar o türe göre puanlanır: kalite, hız, maliyet, uzmanlık (kod/görsel/
     akıl yürütme), geçmiş başarı oranı ve aynı görevde önceki motor (süreklilik). Kullanıcı bir türe
     motor sabitlediyse o başa alınır.
  3. Tur sıradaki motorla çalışır. Motor turu bitiremezse (kota, bağlantı, anahtar, sunucu, güvenlik
     filtresi) kısa bir süre dinlenmeye alınır ve aynı tur KALDIĞI YERDEN sonraki motorla sürer.

İşlerin karışmaması için: geçmiş tek bir ortak biçimde (Gemini Content) tutulur; her motor kendi
biçimine çevrilmiş bir kopya görür, yalnızca tamamlanan adımları geri yazar. Bir motor yarıda
kaldığında yarım metni/adımı geçmişe girmez. Koordinat biçimi, araç seti ve görsel desteği her motor
için ayrı ayarlanır. Gizli işler yalnızca yerel motorlarla yapılır.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from datetime import datetime
from typing import AsyncIterator, Callable

from . import config, engines
from .engines import Engine

STATS_FILE = config.DATA_DIR / "ai_stats.json"
_lock = threading.Lock()

KINDS: dict[str, str] = {
    "sohbet": "Sohbet ve kısa sorular",
    "arastirma": "Web araştırması ve özet",
    "kod": "Kod, terminal ve otomasyon",
    "ofis": "Belge, tablo, sunum, e-posta",
    "ekran": "Ekran ve uygulama kontrolü",
    "derin": "Derin düşünme, plan ve analiz",
    "gizli": "Gizli işler (yalnız yerel model)",
}

# Alt ajan rolü → görev türü
ROLE_KINDS = {"arastirmaci": "arastirma", "analist": "kod", "yazar": "ofis", "denetci": "derin", "operator": "ekran"}

# Türlere göre ağırlıklar: (kalite, hız, ucuzluk)
WEIGHTS: dict[str, tuple[float, float, float]] = {
    "sohbet": (1.0, 1.6, 1.0),
    "arastirma": (1.5, 1.0, 0.8),
    "kod": (2.2, 0.6, 0.5),
    "ofis": (1.6, 0.8, 0.7),
    "ekran": (1.8, 0.9, 0.5),
    "derin": (2.6, 0.3, 0.3),
    "gizli": (1.6, 0.6, 0.0),
}

_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Yalnızca açık gizlilik isteği ("gizli klasör/dosya" gibi Windows terimleri sayılmaz)
    ("gizli", re.compile(r"gizli (tut|kalsın|kalmalı|bilgi|veri|belge)|gizlidir|özel veri|kişisel veri|"
                         r"internete (gönderme|çıkma|yükleme)|buluta (gönderme|yükleme)|yerel model(le)?|@yerel|çevrimdışı|offline", re.I)),
    ("ekran", re.compile(r"ekran|tıkla|fare|imleç|pencere|butona|düğme|masaüstünde|uygulamay[ıi] (aç|kullan)|"
                         r"screenshot|klavyeyle|sürükle|click|\bui\b|whatsapp|telegram|instagram|facebook|twitter|youtube|"
                         r"spotify|discord|\bedge|chrome|tarayıcı|sekme", re.I)),
    ("kod", re.compile(r"\bkod|python|javascript|typescript|\bscript|betik|terminal|powershell|komut satırı|"
                       r"\bhata (ayıkla|veriyor|alıyorum)|debug|derle|\bbuild\b|\bgit\b|github|\bapi\b|fonksiyon|"
                       r"yazılım|program yaz|regex|\bsql\b|\bjson\b|docker|npm|pip |kurulum yap|otomasyon|godot|unity", re.I)),
    ("derin", re.compile(r"analiz et|strateji|planla|plan çıkar|kanıtla|ispat|matemati|hesapla|optimiz|mimari|"
                         r"tasarla|değerlendir|derinlemesine|adım adım düşün|karar ver|risk|iş planı|neden.*\?", re.I)),
    ("ofis", re.compile(r"\bword\b|excel|sunum|powerpoint|\bpdf\b|rapor|tablo|belge|dilekçe|e-?posta|\bmail\b|"
                        r"takvim|toplantı|fatura|teklif|sözleşme|özgeçmiş|cv\b|mektup", re.I)),
    ("arastirma", re.compile(r"araştır|internette|\bweb\b|haber|karşılaştır|fiyat|kaynak|güncel|son durum|"
                             r"\bnedir\b|\bkimdir\b|bul bana|ara bul|incele", re.I)),
]


# Görev başka dilde yazıldığında da türü tanınsın: İngilizce ağırlıklı, ayrıca Almanca, Fransızca, İspanyolca,
# Portekizce, İtalyanca ve Rusça anahtar sözcükler. Eşleşme olmazsa tür uzunluğa göre seçilir (her dilde çalışır).
_INTL: dict[str, re.Pattern] = {
    "gizli": re.compile(r"\b(confidential|keep (it|this) (private|secret)|private data|personal data|local model|"
                        r"do(n't| not) (upload|send)|vertraulich|confidentiel|confidencial|riservato)\b|конфиденциальн", re.I),
    "ekran": re.compile(r"\b(screen|click|mouse|cursor|window|button|desktop|open the app|drag|keyboard|browser|tab|"
                        r"bildschirm|klick|maus|fenster|écran|cliqu|souris|fenêtre|pantalla|clic|ratón|ventana|tela|"
                        r"janela|schermo|finestra)\w*|экран|щёлкн|щелкн|мыш|окн[оа]|кнопк", re.I),
    "kod": re.compile(r"\b(code|script|command line|shell|compile|function|software|program|automation|bug|"
                      r"programm|skript|befehl|código|codice|commande|logiciel)\w*|\bкод|скрипт|програм", re.I),
    "derin": re.compile(r"\b(analy[sz]e|strategy|plan out|prove|proof|math|calculate|optimi[sz]e|architecture|design|"
                        r"evaluate|in depth|step by step|decide|risk|business plan|analysier|strategie|berechn|entwerf|"
                        r"stratégie|calcul|concev|estrategia|analiza|diseña|estratégia|analis|strategia|progett)\w*|"
                        r"анализ|стратег|рассчита|спроектир|докаж", re.I),
    "ofis": re.compile(r"\b(word|excel|presentation|slides?|powerpoint|pdf|report|spreadsheet|table|document|letter|"
                       r"e-?mail|calendar|meeting|invoice|quote|contract|resume|cv|bericht|tabelle|dokument|brief|"
                       r"rechnung|vertrag|rapport|tableau|lettre|facture|contrat|informe|tabla|documento|carta|factura|"
                       r"contrato|relatório|tabela|fatura|relazione|tabella|lettera|fattura|contratto)\b|"
                       r"отч[её]т|таблиц|документ|письм|презентац|сч[её]т|договор", re.I),
    "arastirma": re.compile(r"\b(research|search the web|online|news|compare|price|source|latest|current|what is|who is|"
                            r"find me|look up|investigate|recherch|vergleich|preis|nachricht|actualité|compar|prix|"
                            r"investiga|noticias|precio|pesquis|notícias|preço|ricerca|notizie|prezzo)\w*|"
                            r"исследу|найди|новост|сравн|цен[аы]", re.I),
}


def classify(text: str, has_image: bool = False, previous: str | None = None) -> str:
    """`previous`: aynı görevin önceki turunun türü — "olmadı, tekrar dene" gibi devam mesajları onu sürdürür."""
    t = text or ""
    for kind, pattern in _PATTERNS:
        if pattern.search(t) or _INTL[kind].search(t):
            return kind
    if has_image:
        return "ekran" if re.search(r"ekran|görüntü", t, re.I) else "ofis"
    if previous in KINDS and previous != "sohbet" and len(t) < 200:
        return previous
    if _ACTION.search(t):
        return "ofis"  # bir iş isteniyor: sohbet modeli değil, iş yapabilen model
    return "sohbet" if len(t) < 160 else "ofis"


# ---------------------------------------------------------------- istatistik ve dinlenme

_COOLDOWN: dict[str, tuple[float, str]] = {}  # motor → (şu zamana kadar atla, neden)


def _load_stats() -> dict:
    try:
        return json.loads(STATS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"engines": {}, "decisions": []}


def _save_stats(data: dict) -> None:
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATS_FILE)


def _record(engine_id: str, ok: bool, seconds: float = 0.0, error: str = "") -> None:
    with _lock:
        data = _load_stats()
        s = data["engines"].setdefault(engine_id, {"ok": 0, "fail": 0, "secs": 0.0})
        if ok:
            s["ok"] += 1
            s["secs"] = round(s["secs"] + seconds, 1)
        else:
            s["fail"] += 1
            s["last_error"] = error[:200]
            s["last_error_at"] = datetime.now().isoformat(timespec="seconds")
        s["last_used"] = datetime.now().isoformat(timespec="seconds")
        _save_stats(data)


def _log_decision(entry: dict) -> None:
    with _lock:
        data = _load_stats()
        data["decisions"] = ([entry] + data.get("decisions", []))[:40]
        _save_stats(data)


def _finish_decision(at: str, engine_label: str, outcome: str) -> None:
    with _lock:
        data = _load_stats()
        for d in data.get("decisions", []):
            if d.get("at") == at:
                d["engine"], d["outcome"] = engine_label, outcome
                break
        _save_stats(data)


def stats() -> dict:
    _ensure_cooldowns()
    data = _load_stats()
    now = time.time()
    data["cooldowns"] = {k: {"seconds": round(until - now), "reason": why} for k, (until, why) in _COOLDOWN.items() if until > now}
    return data


_cooldowns_loaded = False


def _persist_cooldowns() -> None:
    with _lock:
        data = _load_stats()
        now = time.time()
        data["cooldowns"] = {k: [until, why] for k, (until, why) in _COOLDOWN.items() if until > now}
        _save_stats(data)


def _ensure_cooldowns() -> None:
    """Dinlenmeler yeniden başlatmada kaybolmasın (ör. 13 saatlik günlük kota)."""
    global _cooldowns_loaded
    if _cooldowns_loaded:
        return
    _cooldowns_loaded = True
    now = time.time()
    for k, v in (_load_stats().get("cooldowns") or {}).items():
        if isinstance(v, list) and len(v) == 2 and v[0] > now:
            _COOLDOWN.setdefault(k, (float(v[0]), str(v[1])))


def cool(engine_id: str, seconds: float, reason: str) -> None:
    _ensure_cooldowns()
    if seconds > 0:
        _COOLDOWN[engine_id] = (time.time() + seconds, reason)
        _persist_cooldowns()


def clear_cooldowns(prefix: str | None = None) -> None:
    """Sağlayıcının (ör. "gemini") ya da tek bir motorun (ör. "gemini:gemini-3.8-flash") dinlenmesini bitirir."""
    _ensure_cooldowns()
    for k in list(_COOLDOWN):
        if prefix is None or k == prefix or k.startswith(prefix + ":"):
            _COOLDOWN.pop(k, None)
    _persist_cooldowns()


def _cooling(engine_id: str) -> bool:
    _ensure_cooldowns()
    until = _COOLDOWN.get(engine_id, (0, ""))[0]
    return until > time.time()


def _reliability(engine_id: str, data: dict) -> float:
    s = data["engines"].get(engine_id) or {}
    ok, fail = s.get("ok", 0), s.get("fail", 0)
    return (ok + 2) / (ok + fail + 3)  # yeni motor ~0.67, sürekli başarılı → 1, sürekli hata → 0


# ---------------------------------------------------------------- seçim

def score(e: Engine, kind: str, *, needs_vision: bool, background: bool, prefer: str | None, data: dict) -> float:
    q, s, c = WEIGHTS.get(kind, WEIGHTS["sohbet"])
    if background:
        c += 1.0  # arka plan işlerinde ücretsiz/ucuz motorlar öne çıkar
        s *= 0.5
    total = q * e.tier + s * e.speed + c * (3 - e.cost)
    if kind in e.specialty:
        total += 2.5
    if (kind == "ekran" or needs_vision) and not e.vision:
        total -= 8
    if e.local and kind != "gizli":
        total -= 0.5  # bulut motoru varsa yerel olanı yedekte tutar (bu bilgisayarda yavaş)
    total += 3.0 * _reliability(e.id, data)
    if prefer and e.id == prefer:
        total += 1.5  # aynı görev aynı motorla sürsün (süreklilik)
    return round(total, 2)


def plan(kind: str, *, needs_vision: bool = False, background: bool = False, prefer: str | None = None,
         limit: int = 5) -> list[Engine]:
    """Bu tür için denenecek motorlar, en uygundan başlayarak."""
    data = _load_stats()
    pool = [e for e in engines.all_engines() if e.enabled and e.available and e.tools]
    if kind == "gizli":
        pool = [e for e in pool if e.local]
    ready = [e for e in pool if not _cooling(e.id)]
    resting = [e for e in pool if _cooling(e.id)]
    # Eşit puanda yeni sürüm öne geçer (gemini-3.8-flash > gemini-3.5-flash)
    ranked = sorted(ready, key=lambda e: (score(e, kind, needs_vision=needs_vision, background=background, prefer=prefer, data=data),
                                          e.version), reverse=True)
    pinned = engines.routes().get(kind)
    if pinned:
        ranked.sort(key=lambda e: e.id != pinned)
    chain = ranked[:limit]
    # Zincirin tamamı tek aileden olmasın: o aile toptan çökerse (anahtar, ağ) son çare başka aileden
    if len(chain) == limit and len({e.provider for e in chain}) == 1:
        other = next((e for e in ranked[limit:] if e.provider != chain[0].provider), None)
        if other:
            chain[-1] = other
    # Hepsi dinlenmedeyse yine de en az kalan süreli olanlar denenir
    if len(chain) < 2 and resting:
        chain += sorted(resting, key=lambda e: _COOLDOWN[e.id][0])[: 2 - len(chain)]
    return chain


def preview() -> dict[str, list[dict]]:
    """Arayüz için: her tür için şu an seçilecek motor zinciri."""
    out = {}
    for kind in KINDS:
        out[kind] = [{"id": e.id, "label": e.label, "provider": e.provider} for e in plan(kind, limit=3)]
    return out


# ---------------------------------------------------------------- çalıştırma

class NoEngine(Exception):
    """Kullanılabilir motor yok."""


class AllEnginesFailed(Exception):
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _quota(message: str) -> tuple[str, float]:
    """Kota hatası: sağlayıcı "N sa M dk sonra tekrar deneyin" diyorsa tam o kadar dinlenir.
    "limit: 0" bu modelin ücretsiz kotası hiç yok demektir (ör. ücretsiz katmanda pro modeller)."""
    if re.search(r"limit:\s*0\b", message):
        return "bu modelin ücretsiz kotası yok", 12 * 3600
    m = re.search(r"retry in\s*(?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*(?:([\d.]+)s)?", message, re.I)
    wait = 0.0
    if m and any(m.groups()):
        h, mi, s = m.groups()
        wait = int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)
    if wait >= 3600:
        return f"günlük kota doldu ({round(wait / 3600, 1)} sa sonra açılır)", wait
    return "kota doldu", max(wait, 15 * 60)


def _failure(e: BaseException, eng: Engine) -> tuple[str, float] | None:
    """Motor kaynaklı hata ise (kısa açıklama, dinlenme süresi sn); değilse None (hata yukarı iletilir)."""
    import httpx
    import openai

    from .providers.claude_code import ClaudeCodeError

    if isinstance(e, ClaudeCodeError):
        return e.reason, e.rest
    if eng.family == "gemini":
        from google.genai import errors

        from .providers.gemini import GeminiDeclined, GeminiQuotaExhausted
        from .providers.qwen_fallback import gemini_failed

        reason = gemini_failed(e)
        if reason is None:
            return None
        if isinstance(e, GeminiQuotaExhausted) or (isinstance(e, errors.ClientError) and e.code == 429):
            return _quota(str(e))
        if isinstance(e, GeminiDeclined):
            return reason, 0
        if isinstance(e, errors.ClientError):
            return reason, (24 * 3600 if e.code in (401, 403, 404) else 6 * 3600 if e.code == 402 else 10 * 60)
        if isinstance(e, RuntimeError):
            return reason, 3600
        return reason, 3 * 60
    if isinstance(e, openai.APIConnectionError):  # zaman aşımı da buna dahil
        return ("yanıt vermedi (zaman aşımı)" if isinstance(e, openai.APITimeoutError) else "sunucuya bağlanılamadı"), 3 * 60
    if isinstance(e, openai.AuthenticationError):
        return "API anahtarı reddedildi", 24 * 3600
    if isinstance(e, openai.PermissionDeniedError):
        return "erişim izni yok", 24 * 3600
    if isinstance(e, openai.NotFoundError):
        return "model bulunamadı", 24 * 3600
    if isinstance(e, openai.RateLimitError):
        return _quota(str(e))
    if isinstance(e, openai.APIStatusError):
        if e.status_code == 402:
            return "kredi bitti", 6 * 3600
        if e.status_code >= 500:
            return f"sunucu hatası ({e.status_code})", 3 * 60
        return f"isteği reddetti ({e.status_code}): {str(e.message)[:120]}", 30 * 60
    if isinstance(e, (httpx.HTTPError, asyncio.TimeoutError)):
        return "bağlantı hatası", 3 * 60
    return None


def _provider_wide(e: BaseException, eng: Engine) -> bool:
    """Hata sağlayıcının tamamını mı etkiliyor (anahtar yok/geçersiz, kredi bitti, sunucuya ulaşılamıyor)?
    Kota ve "model yok" hataları modele özeldir."""
    import httpx
    import openai

    from .providers.claude_code import ClaudeCodeError

    if isinstance(e, ClaudeCodeError):
        return e.provider_wide
    if eng.family == "gemini":
        from google.genai import errors

        if isinstance(e, errors.ClientError):
            return e.code in (401, 402, 403) or (e.code == 400 and "API key" in str(e))
        return isinstance(e, (RuntimeError, httpx.ConnectError))
    return isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)) or (
        isinstance(e, openai.APIConnectionError) and not isinstance(e, openai.APITimeoutError)
    ) or (isinstance(e, openai.APIStatusError) and e.status_code == 402)


def _tools_for(eng: Engine, kind: str, tools: list[dict]) -> list[dict]:
    from .agent import CORE_TOOLS

    screen_ok = eng.vision and config.ENABLE_COMPUTER
    if eng.toolset == "core":
        keep_screen = screen_ok and kind == "ekran"
        return [t for t in tools if t.get("name") in CORE_TOOLS or (keep_screen and t.get("type") == "computer_toolset_20260801")]
    if not screen_ok:
        return [t for t in tools if t.get("type") != "computer_toolset_20260801"]
    return tools


async def _run_engine(
    eng: Engine, kind: str, messages: list[dict], *, system: list[dict], tools: list[dict], ui_entry: dict,
    save: Callable[[], None], max_steps: int,
) -> AsyncIterator[dict]:
    from .tools import computer

    computer.ENGINE_COORDS.set(eng.coords)
    tools = _tools_for(eng, kind, tools)
    if eng.family == "gemini":
        from .providers.gemini import gemini_loop

        async for ev in gemini_loop(messages, system=system, tools=tools, ui_entry=ui_entry, save=save,
                                    max_steps=max_steps, models=[eng.model], handoff=True):
            yield ev
        return

    from .providers.qwen_fallback import to_gemini, to_openai
    from .providers.selfhost import Endpoint, selfhost_loop

    if eng.family == "claudecode":
        # Bu bilgisayardaki Claude Code (abonelik oturumu): düşünen o, araçları çalıştıran yine ADA
        from .providers.claude_code import claude_code_loop

        cc = to_openai(messages, eng.vision)
        cc_g, cc_o = len(messages), len(cc)

        def cc_sync() -> None:
            messages[cc_g:] = to_gemini(cc[cc_o:])
            save()

        async for ev in claude_code_loop(cc, system=system, tools=tools, ui_entry=ui_entry, save=cc_sync,
                                         max_steps=max_steps, model=eng.model):
            yield ev
        return

    if eng.provider == "ollama":
        if not await engines.ollama_up():
            yield {"type": "step", "label": "Yerel model sunucusu başlatılıyor", "detail": "Ollama"}
            await engines.ensure_ollama()  # başlatılamazsa ilk istek bağlantı hatası verir ve sıradaki motora geçilir
        # Bağlam küçükse istem kırpılır ve model görevi göremez: ADA bunu kendisi düzeltir
        fixed = await engines.ensure_ollama_context()
        if fixed:
            yield {"type": "step", "label": "Kendi sorununu düzeltti", "detail": fixed}
    key = engines.api_key(eng.provider)
    ep = Endpoint(eng.base_url, key, eng.model, eng.vision, eng.max_tokens,
                  config.LLM_TIMEOUT if eng.local else 240.0, eng.token_param)
    oa = to_openai(messages, ep.vision)
    base_g, base_o = len(messages), len(oa)

    def sync() -> None:
        messages[base_g:] = to_gemini(oa[base_o:])
        save()

    if eng.provider == "ollama":
        engines.OLLAMA_BUSY += 1
    try:
        async for ev in selfhost_loop(oa, system=system, tools=tools, ui_entry=ui_entry, save=sync, max_steps=max_steps, endpoint=ep):
            yield ev
    finally:
        if eng.provider == "ollama":
            engines.OLLAMA_BUSY -= 1


def _last_user_input(messages: list[dict]) -> tuple[str, bool]:
    """Ortak (Gemini) biçimdeki geçmişte kullanıcının son yazdığı metin ve görsel olup olmadığı."""
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        parts = m.get("parts") or []
        texts = [p["text"] for p in parts if p.get("text") and not p.get("thought")]
        if texts:
            return "\n".join(texts), any(p.get("inline_data") or p.get("inlineData") for p in parts)
    return "", False


async def run(
    messages: list[dict],
    *,
    system: list[dict],
    tools: list[dict],
    ui_entry: dict,
    save: Callable[[], None],
    max_steps: int,
    kind: str | None = None,
    prefer: str | None = None,
    on_engine: Callable[[Engine, str], None] | None = None,
    previous_kind: str | None = None,
) -> AsyncIterator[dict]:
    """Turu en uygun motorla yürütür; motor bitiremezse kaldığı yerden sıradakine devreder,
    tur bitmemiş görünürse (hata, işlem yapmama, "yapamadım") öz-denetimle yeniden dener."""
    from .runtime import UNATTENDED

    text, has_image = _last_user_input(messages)
    kind = kind if kind in KINDS else classify(text, has_image, previous_kind)
    background = UNATTENDED.get()
    chain = plan(kind, needs_vision=has_image, background=background, prefer=prefer)
    if not chain:
        if kind == "gizli":
            raise NoEngine("Gizli işler için açık bir yerel model yok. Ayarlar → Yapay zekâ'dan yerel modelleri tarayın (ör. Ollama).")
        raise NoEngine("Kullanılabilir yapay zekâ motoru yok. Ayarlar → Yapay zekâ'dan yerel modelleri tarayın veya bir API anahtarı ekleyin.")

    at = datetime.now().isoformat(timespec="seconds")
    _log_decision({"at": at, "kind": kind, "text": text[:90], "chain": [e.label for e in chain], "engine": chain[0].label,
                   "outcome": "çalışıyor", "background": background})
    backups = ", ".join(e.label for e in chain[1:3])
    ev = {"type": "step", "label": "ADA-AGI yönlendirdi",
          "detail": f"{KINDS[kind]} → {chain[0].label}" + (f" (yedek: {backups})" if backups else "")}
    ui_entry["steps"].append({"label": ev["label"], "detail": ev["detail"]})
    yield ev

    start = len(messages)
    reasons: list[str] = []
    skipped: set[str] = set()
    prev: Engine | None = None
    idx = 0          # zincirde sıradaki motor
    fixes = 0        # öz-denetimle kaç kez yeniden denendi
    while True:
        done: Engine | None = None
        while idx < len(chain):
            eng = chain[idx]
            if eng.id in skipped:
                idx += 1
                continue
            if prev is not None and prev.id != eng.id and reasons:
                ev = {"type": "step", "label": "Motor değişti", "detail": f"{prev.label}: {reasons[-1].split(': ', 1)[-1]} → {eng.label} devraldı"}
                ui_entry["steps"].append({"label": ev["label"], "detail": ev["detail"]})
                yield ev
            prev = eng
            used = sum(1 for m in messages[start:] if m.get("role") == "model")
            mark = len(ui_entry["text"])

            def tracked_save() -> None:
                nonlocal mark
                mark = len(ui_entry["text"])  # bu noktaya kadarki metin geçmişe yazıldı
                save()

            t0 = time.monotonic()
            try:
                async for ev in _run_engine(eng, kind, messages, system=system, tools=tools, ui_entry=ui_entry,
                                            save=tracked_save, max_steps=max(1, max_steps - used)):
                    yield ev
            except Exception as e:  # noqa: BLE001 - yalnızca motor kaynaklı hatalar devredilir
                failure = _failure(e, eng)
                if failure is None:
                    _finish_decision(at, eng.label, f"hata: {type(e).__name__}")
                    raise
                reason, rest = failure
                cool(eng.id, rest, reason)
                _record(eng.id, False, error=reason)
                reasons.append(f"{eng.label}: {reason}")
                if _provider_wide(e, eng):
                    # Anahtar/ağ/sunucu hatası tüm aileyi etkiler: aynı sağlayıcının diğer modelleri boşuna denenmez
                    for other in chain[idx + 1:]:
                        if other.provider == eng.provider:
                            cool(other.id, rest, reason)
                            skipped.add(other.id)
                if len(ui_entry["text"]) > mark:
                    ui_entry["text"] = ui_entry["text"][:mark]  # yarım kalan yanıt ekrandan da silinir
                yield {"type": "text_set", "text": ui_entry["text"]}
                idx += 1
                continue
            _record(eng.id, True, time.monotonic() - t0)
            done = eng
            break
        if done is None:
            _finish_decision(at, "-", "hepsi başarısız")
            raise AllEnginesFailed(reasons)

        # Öz-denetim: tur gerçekten bitti mi, yoksa model hatada durdu / işi hiç yapmadı / "yapamadım" mı dedi?
        problem = assess(messages, start, kind=kind, background=background, task=text)
        used = sum(1 for m in messages[start:] if m.get("role") == "model")
        if problem and fixes < SELF_FIX_ROUNDS and used < max_steps:
            fixes += 1
            # İlk düzeltmeyi aynı motor dener; olmazsa sıradaki (farklı) motor devralır
            if fixes >= 2:
                nxt = next((j for j in range(idx + 1, len(chain)) if chain[j].id not in skipped), None)
                if nxt is not None:
                    idx = nxt
                    reasons.append(f"{done.label}: öz-denetimde düzeltemedi")
            ev = {"type": "step", "label": "Öz-denetim: bitmemiş iş, yeniden deniyor", "detail": f"{problem} ({fixes}/{SELF_FIX_ROUNDS})"}
            ui_entry["steps"].append({"label": ev["label"], "detail": ev["detail"]})
            yield ev
            messages.append(_supervisor_message(problem, fixes))
            if ui_entry["text"] and not ui_entry["text"].endswith("\n"):
                ui_entry["text"] += "\n\n"
                yield {"type": "text", "text": "\n\n"}
            save()
            continue

        outcome = "tamam" if idx == 0 else f"tamam ({idx + 1}. motor)"
        if fixes:
            outcome += f", {fixes} öz-düzeltme" + (" — hâlâ sorunlu" if problem else "")
        _finish_decision(at, done.label, outcome)
        ui_entry["engine"] = done.label
        if on_engine:
            on_engine(done, kind)
        return


# ---------------------------------------------------------------- öz-denetim

SELF_FIX_ROUNDS = int(__import__("os").getenv("ADA_SELF_FIX_ROUNDS", "3"))

_GIVE_UP = re.compile(
    r"yapamad|yapamıyorum|yapamam\b|gerçekleştiremedi|başaramad|başarısız oldu|başarısız oldum|mümkün değil|imkans[ıi]z|imkâns[ıi]z|"
    r"erişimim yok|erişemiyorum|manuel olarak|elle (yap|gir|gönder)|kendiniz|sizin (yapmanız|tarafınızdan)|tamamlanamadı|"
    r"gönderilemedi|açılamadı|bulamadım|hata (aldım|verdi|oluştu)|nasıl yardımcı olabilirim|anlaşılamadı|"
    r"\bcan't\b|\bcannot\b|unable to|i'm sorry|i was(n't| not) able|could(n't| not) (complete|do|finish)|failed to|"
    r"not possible|do it yourself|how can i help|"
    r"konnte nicht|nicht möglich|je n'ai pas pu|impossible de|no pude|no fue posible|não consegui|não foi possível|"
    r"non sono riuscit|non è possibile|не удалось|не могу|невозможно", re.I)
_ACTION = re.compile(
    r"\b(gönder|aç|kapat|kur|indir|yükle|sil|oluştur|hazırla|çalıştır|kaydet|ayarla|düzelt|başlat|taşı|kopyala|yaz|ekle|"
    r"tıkla|bul|kontrol et|temizle|güncelle|dene|tekrar dene)\w{0,4}\b|"
    # Başka dillerde iş isteyen fiiller (emir kipi): sohbet değil, iş yapabilen model seçilsin
    r"\b(send|open|close|install|download|upload|delete|remove|create|make|prepare|run|save|set up|fix|start|move|copy|"
    r"write|add|click|find|check|clean|update|try again|retry|schick|sende|öffne|erstell|lösch|speicher|schreib|starte|"
    r"envoie|ouvre|crée|supprime|enregistre|écris|lance|envía|abre|crea|elimina|guarda|escribe|ejecuta|envie|abra|crie|"
    r"exclua|salve|escreva|invia|apri|cancella|salva|scrivi|esegui|"
    r"shut ?down|restart|reboot|turn off|power off|log ?off|herunterfahren|neu starten|éteins|redémarre|apaga|reinicia|"
    r"desliga|reinicie|spegni|riavvia)\b|выключи|перезагрузи|"
    r"отправ|открой|созда|удали|сохрани|напиши|запусти|установи|скачай|найди|проверь", re.I)


def _parts(m: dict) -> list[dict]:
    return m.get("parts") or []


def assess(messages: list[dict], start: int, *, kind: str, background: bool, task: str) -> str | None:
    """Turun sonunu denetler; iş bitmemiş görünüyorsa nedenini döner."""
    new = messages[start:]
    if not new or new[-1].get("role") != "model":
        return None  # adım sınırı vb.: tur zaten yarıda
    last = new[-1]
    if any(p.get("function_call") or p.get("functionCall") for p in _parts(last)):
        return None
    final = " ".join(p.get("text", "") for p in _parts(last) if p.get("text") and not p.get("thought")).strip()
    calls, last_error = 0, None
    for m in new:
        for p in _parts(m):
            if p.get("function_call") or p.get("functionCall"):
                calls += 1
            fr = p.get("function_response") or p.get("functionResponse")
            if fr:
                resp = fr.get("response") or {}
                last_error = str(resp["error"])[:220] if isinstance(resp, dict) and "error" in resp else None
    gave_up = bool(_GIVE_UP.search(final))
    if last_error:
        return "son adım hata verdi ve model orada durdu: " + last_error.replace("\n", " ")
    if calls == 0:
        if not final:
            return "model boş yanıt verdi"
        if re.search(r"nasıl yardımcı olabilirim|anlaşılamadı|how can i help", final, re.I) and (background or len(task) > 40):
            return "model görevi görmeden genel bir yanıt verdi"
        if gave_up and (background or _ACTION.search(task)):
            return "hiçbir araç denemeden 'yapamam' dedi"
        if background or (kind in ("ekran", "kod", "ofis") and _ACTION.search(task) and len(task) < 400):
            return "istenen iş için hiçbir işlem yapmadan bitirdi"
        return None
    if gave_up and re.search(r"yapamad|başaramad|gerçekleştiremedi|gönderilemedi|tamamlanamadı|başarısız|"
                             r"was(n't| not) able|could(n't| not) (complete|do|finish)|failed to|konnte nicht|"
                             r"je n'ai pas pu|no pude|não consegui|non sono riuscit|не удалось", final, re.I):
        return "işi bitirmeden 'yapamadım' dedi"
    return None


def _supervisor_message(problem: str, round_no: int) -> dict:
    from .providers.gemini import text_message

    windows = (" Bu bilgisayar Windows; run_shell PowerShell 5.1 çalıştırır: ls -la yerine Get-ChildItem -Force, "
               "tail yerine Get-Content -Tail 50, grep yerine Select-String, cat yerine Get-Content, '&&' yerine ';'."
               if config.IS_WIN else "")
    return text_message(
        f"[ADA öz-denetim #{round_no} — kullanıcı mesajı değil, iç denetim] Görev henüz tamamlanmadı: {problem}. "
        "Durma, işi kullanıcıya bırakma ve sadece hatayı açıklamakla yetinme. Şimdi: 1) Hatanın gerçek nedenini bul. "
        "2) Farklı bir yöntemle yeniden dene (başka komut, başka araç, ekran kontrolü, Python)." + windows +
        " 3) Bitti demeden önce sonucu doğrula (dosyayı oku, ekran görüntüsü al, mesajın gerçekten gittiğini gör). "
        "Yalnızca gerçekten imkânsızsa (izin, ödeme, eksik hesap/şifre) denediğin her yolu kanıtıyla kısaca raporla. "
        "Asıl görevi unutma: önceki kullanıcı isteğini yerine getir."
    )


# ---------------------------------------------------------------- arka plan tarama

async def discovery_loop() -> None:
    """Açılışta ve düzenli aralıklarla yeni kurulan yerel modelleri/anahtarları fark eder."""
    import os

    if os.getenv("ADA_NO_DISCOVERY"):  # testler gerçek sunuculara/API'lere dokunmasın
        return
    await asyncio.sleep(3)
    first = True
    while True:
        try:
            if first:
                await engines.scan_all()
            else:
                await engines.scan_local()
                await engines.scan_claude_code()  # sonradan kurulmuş ya da oturumu açılmış olabilir
        except Exception:  # noqa: BLE001 - tarama hatası ADA'yı durdurmasın
            pass
        try:
            await engines.ensure_ollama_context()  # görev beklemeden önce de kendi ayarını onarır
        except Exception:  # noqa: BLE001
            pass
        first = False
        await asyncio.sleep(15 * 60)
