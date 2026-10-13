"""Arayüzden düzenlenebilen ayarlar (.env dosyasına yazılır).

Masaüstü uygulamasını kuran kullanıcı .env dosyasıyla uğraşmasın diye; gizli değerler
(API anahtarları, parola) arayüze yalnızca maskelenmiş olarak gönderilir. Değişiklikler
uygulama yeniden başlatılınca geçerli olur (`restart`).
"""

from __future__ import annotations

import os
import re
import sys
import threading

from . import config


def engines_catalog():
    from .engines import CLOUD

    return CLOUD

# anahtar: (etiket, tür, seçenekler/None, gizli mi, varsayılan)
FIELDS: dict[str, tuple[str, str, list[str] | None, bool, str]] = {
    "ADA_PROVIDER": ("Yapay zekâ", "select", ["adaagi", "gemini", "anthropic", "selfhost"], False, "adaagi"),
    "GEMINI_API_KEY": ("Gemini API anahtarı", "text", None, True, ""),
    "ANTHROPIC_API_KEY": ("Claude (Anthropic) API anahtarı", "text", None, True, ""),
    "ADA_GEMINI_MODELS": ("Gemini modelleri (sırayla denenir)", "text", None, False, ",".join(config.GEMINI_MODELS)),
    "ADA_QWEN_API_KEY": ("Qwen yedeği: API anahtarı (DashScope)", "text", None, True, ""),
    "ADA_QWEN_MODEL": ("Qwen yedeği: model", "text", None, False, config.QWEN_MODEL),
    "ADA_QWEN_BASE_URL": ("Qwen yedeği: adres", "text", None, False, config.QWEN_BASE_URL),
    "ADA_QWEN_FALLBACK": ("Qwen yedeği açık (Gemini yapamazsa)", "bool", None, False, "true" if config.QWEN_FALLBACK else "false"),
    "ADA_QWEN_VISION": ("Qwen yedeği görsel okuyabilir (qwen-vl)", "bool", None, False, "true" if config.QWEN_VISION else "false"),
    "ADA_MODEL": ("Claude modeli", "text", None, False, "claude-opus-5-5"),
    "ADA_EFFORT": ("Claude çaba düzeyi", "select", ["low", "medium", "high", "xhigh", "max"], False, "high"),
    "ADA_LLM_BASE_URL": ("Kendi sunucum: adres", "text", None, False, "http://localhost:11434/v1"),
    "ADA_LLM_MODEL": ("Kendi sunucum: model", "text", None, False, "gpt-oss:20b"),
    "ADA_LLM_API_KEY": ("Kendi sunucum: API anahtarı", "text", None, True, ""),
    "ADA_AUTONOMY": ("Özerklik", "select", ["smart", "ask", "full"], False, "smart"),
    "ADA_ENABLE_SHELL": ("Kabuk ve dosya sistemi", "bool", None, False, "true"),
    "ADA_ENABLE_COMPUTER": ("Ekran kontrolü (fare/klavye)", "bool", None, False, "true"),
    "ADA_ENABLE_MAC_APPS": ("Mail / Takvim / Notlar (macOS)", "bool", None, False, "true" if config.IS_MAC else "false"),
    "ADA_ENABLE_PYTHON": ("Python ile veri analizi", "bool", None, False, "true"),
    "ADA_WATCH": ("Gözcü: bilgisayarı sürekli denetle", "bool", None, False, "true"),
    "ADA_WATCH_AI_MINUTES": ("Gözcü kontrol listesi aralığı (dakika, 0: kapalı)", "number", None, False, str(config.WATCH_AI_MINUTES)),
    "ADA_WATCH_AI_FIX": ("Gözcü yeni sorunları kendisi incelesin/düzeltsin", "bool", None, False, "true"),
    "ADA_DAILY_TOOL_LIMIT": ("Günlük araç çağrısı sınırı (0: sınırsız)", "number", None, False, "3000"),
    "ADA_DAILY_EMAIL_LIMIT": ("Günlük e-posta gönderim sınırı (0: sınırsız)", "number", None, False, "20"),
    "ADA_UPDATE_URL": ("Güncelleme adresi (github:sahip/depo ya da https://…/latest.json)", "text", None, False, config.UPDATE_URL),
    "ADA_UPDATE_AUTO_CHECK": ("Yeni sürümü kendiliğinden denetle", "bool", None, False, "true"),
    "ADA_LANG": ("Arayüz dili", "select", ["auto", *config.LANGS], False, "auto"),
    "ADA_PASSWORD": ("Arayüz parolası (boş: parola yok)", "text", None, True, ""),
}

# Bulut sağlayıcı anahtarları (Ayarlar → Yapay zekâ bölümünde sağlayıcı kartlarından girilir)
for _p in engines_catalog():
    FIELDS.setdefault(_p.key_env, (f"{_p.label} API anahtarı", "text", None, True, ""))

# "Yapay zekâ" sekmesinde gösterilen alanlar; geri kalanı "Genel" sekmesindedir
AI_FIELDS = {
    "ADA_PROVIDER", "ADA_GEMINI_MODELS", "ADA_QWEN_API_KEY", "ADA_QWEN_MODEL", "ADA_QWEN_BASE_URL", "ADA_QWEN_FALLBACK",
    "ADA_QWEN_VISION", "ADA_MODEL", "ADA_EFFORT", "ADA_LLM_BASE_URL", "ADA_LLM_MODEL", "ADA_LLM_API_KEY",
} | {p.key_env for p in engines_catalog()}
# Kullanıcının eklediği özel (OpenAI uyumlu) sunucuların anahtarları
CUSTOM_KEY = re.compile(r"^ADA_CUSTOM_[A-Z0-9]+_KEY$")

_lock = threading.Lock()


def _mask(value: str) -> str:
    if not value:
        return ""
    return "••••" + value[-4:] if len(value) > 8 else "••••"


def current() -> list[dict]:
    out = []
    for key, (label, kind, options, secret, default) in FIELDS.items():
        if not config.QWEN_ENABLED and (key.startswith("ADA_QWEN_") or key == "DASHSCOPE_API_KEY"):
            continue  # Qwen şimdilik pasif: ayarları gösterilmez
        if key == "ADA_LANG":
            continue  # dil, Kontrol menüsündeki seçiciden değiştirilir (yeniden başlatma gerekmez)
        value = os.getenv(key, "")
        out.append({
            "key": key,
            "label": label,
            "type": kind,
            "options": options,
            "secret": secret,
            "set": bool(value),
            "value": _mask(value) if secret else (value or default),
            "group": "ai" if key in AI_FIELDS else "general",
        })
    return out


def mask(value: str) -> str:
    return _mask(value)


def _quote(value: str) -> str:
    return value if re.fullmatch(r"[\w\-.,:/@+]*", value) else '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def update(changes: dict[str, str]) -> list[str]:
    """Verilen anahtarları .env dosyasına yazar; bilinmeyen anahtarlar reddedilir. Değişen anahtarları döner."""
    unknown = {k for k in changes if k not in FIELDS and not CUSTOM_KEY.match(k)}
    if unknown:
        raise ValueError("Bilinmeyen ayar: " + ", ".join(sorted(unknown)))
    for key, value in changes.items():
        _, kind, options, _, _ = FIELDS.get(key, ("", "text", None, True, ""))
        value = str(value).strip()
        if "\n" in value:
            raise ValueError(f"{key}: çok satırlı değer olamaz")
        if kind == "select" and value not in (options or []):
            raise ValueError(f"{key}: geçersiz seçim '{value}'")
        if kind == "bool" and value not in ("true", "false"):
            raise ValueError(f"{key}: true/false olmalı")
        if kind == "number" and not value.isdigit():
            raise ValueError(f"{key}: sayı olmalı")
        changes[key] = value

    with _lock:
        lines = config.ENV_FILE.read_text(encoding="utf-8").splitlines() if config.ENV_FILE.exists() else []
        done: set[str] = set()
        for i, line in enumerate(lines):
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=", line)
            if m and m.group(1) in changes:
                lines[i] = f"{m.group(1)}={_quote(changes[m.group(1)])}"
                done.add(m.group(1))
        for key in changes:
            if key not in done:
                lines.append(f"{key}={_quote(changes[key])}")
        config.ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            os.chmod(config.ENV_FILE, 0o600)  # anahtarları yalnızca kullanıcı okuyabilsin
        except OSError:
            pass
    for key, value in changes.items():
        os.environ[key] = value
    return sorted(changes)


def restart() -> None:
    """Süreci aynı komutla yeniden başlatır (ayarlar yeniden okunur)."""
    # Paketli uygulamada argv[0] zaten exe'nin kendisidir; pencere yeniden görünsün diye --hidden atılır
    args = [a for a in (sys.argv[1:] if config.FROZEN else sys.argv) if a != "--hidden"]
    os.environ["ADA_RESTARTING"] = "1"  # yeni süreç: eski kopyayı "çalışan ADA" sanma, portun boşalmasını bekle
    if config.IS_WIN:
        # Windows'ta os.execv boşluklu yolları böler ve eski süreç portu bırakmadan yenisi açılır
        import subprocess

        try:  # os._exit temizlik yapmaz: açık terminal oturumları yetim kalmasın
            from . import terminal

            terminal.kill_all()
        except Exception:  # noqa: BLE001
            pass
        subprocess.Popen([sys.executable, *args], close_fds=True)
        os._exit(0)
    os.execv(sys.executable, [sys.executable, *args])
