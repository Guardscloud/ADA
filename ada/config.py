"""Ortam değişkenlerinden okunan ayarlar (.env dosyası da desteklenir).

Geliştirme modunda .env ve workspace/ proje klasöründedir. Paketlenmiş masaüstü uygulamasında
(PyInstaller, sys.frozen) kullanıcı verisi için işletim sisteminin uygulama veri klasörü
kullanılır: macOS ~/Library/Application Support/ADA, Windows %APPDATA%\\ADA.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(__file__).resolve().parent.parent


def _app_home() -> Path:
    if os.getenv("ADA_HOME"):
        return Path(os.environ["ADA_HOME"]).expanduser()
    if not FROZEN:
        return ROOT
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ADA"
    if sys.platform.startswith("win"):
        return Path(os.getenv("APPDATA", Path.home())) / "ADA"
    return Path.home() / ".ada"


HOME_DIR = _app_home()
HOME_DIR.mkdir(parents=True, exist_ok=True)
ENV_FILE = HOME_DIR / ".env"
load_dotenv(ENV_FILE)
# İlk açılış soruları yanıtlanınca oluşturulur (bkz. server.py /api/setup); yoksa arayüz kurulum sihirbazını açar
SETUP_FLAG = HOME_DIR / ".setup_done"


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "evet", "on")


IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

# Yapay zekâ sağlayıcısı:
#   "adaagi"    - ADA-AGI üst katmanı: bu bilgisayardaki yerel modeller + anahtarı girilen tüm bulut
#                 sağlayıcıları (Gemini, Claude, OpenAI, DeepSeek...) görev türüne göre yönlendirir (router.py)
#   "anthropic" (yalnız Claude), "gemini" (yalnız Gemini + Qwen yedeği), "selfhost" (tek bir kendi sunucu)
#   "auto"      - "adaagi" ile aynı
_provider = os.getenv("ADA_PROVIDER", "auto").strip().lower()
if _provider == "auto":
    _provider = "adaagi"
PROVIDER = _provider

# Gemini: sırayla denenir; ücretsiz katmanda bir modelin günlük limiti dolunca sonrakine geçilir
GEMINI_MODELS = [m.strip() for m in os.getenv("ADA_GEMINI_MODELS", "gemini-3.8-flash,gemini-3.7-flash,gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite").split(",") if m.strip()]
GEMINI_KEEP_SCREENSHOTS = int(os.getenv("ADA_GEMINI_KEEP_SCREENSHOTS", "3"))
# Gemini/kendi sunucu: modele gönderilen kopyada son N araç sonucu tam, eskileri kısaltılır
KEEP_FULL_TOOL_RESULTS = int(os.getenv("ADA_KEEP_FULL_TOOL_RESULTS", "6"))
OLD_TOOL_RESULT_CHARS = int(os.getenv("ADA_OLD_TOOL_RESULT_CHARS", "600"))

# Kendi sunucunuz (selfhost): vLLM / Ollama / LM Studio gibi OpenAI uyumlu uç nokta
LLM_BASE_URL = os.getenv("ADA_LLM_BASE_URL", "http://localhost:11434/v1")
LLM_API_KEY = os.getenv("ADA_LLM_API_KEY", "")
LLM_MODEL = os.getenv("ADA_LLM_MODEL", "gpt-oss:20b")
LLM_VISION = _bool("ADA_LLM_VISION", False)  # model görsel okuyabiliyorsa ekran kontrolü açılır
LLM_MAX_TOKENS = int(os.getenv("ADA_LLM_MAX_TOKENS", "16000"))
LLM_TIMEOUT = float(os.getenv("ADA_LLM_TIMEOUT_S", "600"))
# Küçük yerel modellerde istem uzunluğu hızı belirler: "core" temel ~20 aracı, "full" hepsini gönderir
LLM_TOOLSET = os.getenv("ADA_LLM_TOOLSET", "core").strip().lower()

# Qwen yedeği (yalnızca ADA_PROVIDER=gemini iken): öncelik Gemini'dedir; Gemini bir turu yapamazsa
# (tüm modellerin kotası dolu, sunucu yoğun, güvenlik filtresi, boş yanıt) aynı tur Qwen ile sürdürülür.
# Varsayılan uç nokta Alibaba DashScope'un OpenAI uyumlu API'sidir; Ollama/vLLM'deki bir Qwen de olur.
#
# Qwen şimdilik PASİF: yedek devre dışıdır, ADA-AGI katmanı da Qwen modellerini (yerel "qwen*" ve DashScope)
# kullanmaz; arayüzde "pasif" olarak görünürler. Yeniden açmak için ADA_QWEN_ENABLED=true.
QWEN_ENABLED = _bool("ADA_QWEN_ENABLED", False)
QWEN_API_KEY = os.getenv("ADA_QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
QWEN_BASE_URL = os.getenv("ADA_QWEN_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.getenv("ADA_QWEN_MODEL", "qwen-plus")
QWEN_VISION = _bool("ADA_QWEN_VISION", False)  # qwen-vl-* gibi görsel okuyan modellerde true
# Qwen-VL ekran koordinatları: "pixel" (Qwen2.5-VL) veya "norm1000" (Qwen3-VL)
QWEN_SCREEN_COORDS = os.getenv("ADA_QWEN_SCREEN_COORDS", "pixel").strip().lower()
QWEN_MAX_TOKENS = int(os.getenv("ADA_QWEN_MAX_TOKENS", "16000"))
QWEN_TIMEOUT = float(os.getenv("ADA_QWEN_TIMEOUT_S", "300"))
# Yerel Qwen'in bağlamı küçüktür (Ollama varsayılanı 16K; tüm araçlar ~10K token): yerelde "core", bulutta "full"
_qwen_local = any(h in QWEN_BASE_URL for h in ("localhost", "127.0.0.1", "0.0.0.0"))
QWEN_TOOLSET = os.getenv("ADA_QWEN_TOOLSET", "core" if _qwen_local else "full").strip().lower()
# Anahtar girilince (ya da anahtarsız yerel bir sunucu adresi verilince) kendiliğinden açılır
QWEN_FALLBACK = QWEN_ENABLED and _bool("ADA_QWEN_FALLBACK", bool(QWEN_API_KEY) or "dashscope" not in QWEN_BASE_URL)

MODEL = os.getenv("ADA_MODEL", "claude-opus-5-5")
# low | medium | high | xhigh | max - çok adımlı ofis işleri için "high" iyi bir denge
EFFORT = os.getenv("ADA_EFFORT", "high")
MAX_TOKENS = int(os.getenv("ADA_MAX_TOKENS", "64000"))
# Tek bir görevde en fazla kaç model turu (araç çağrısı döngüsü) çalışsın
MAX_STEPS = int(os.getenv("ADA_MAX_STEPS", "80"))
SUBAGENT_MAX_STEPS = int(os.getenv("ADA_SUBAGENT_MAX_STEPS", "40"))
# Tek bir tur (kullanıcı mesajı/hedef oturumu) en fazla bu kadar dakika sürer; sonra durup durumu raporlar
MAX_TURN_MINUTES = float(os.getenv("ADA_MAX_TURN_MINUTES", "20"))
# Süre/adım/uzunluk sınırına gelen görev durmak yerine kendiliğinden "devam et" ile sürer (rutinler hariç:
# onların "sure:" değeri kesin sınırdır). ADA_AUTO_CONTINUE_MAX: en fazla kaç kez (0 = sınırsız).
AUTO_CONTINUE = os.getenv("ADA_AUTO_CONTINUE", "true").lower() in ("1", "true", "yes", "on")
AUTO_CONTINUE_MAX = int(os.getenv("ADA_AUTO_CONTINUE_MAX", "0"))

# Özerklik seviyesi:
#   full  - hiçbir şey sormaz (yalnızca felaket komutları engellenir: diski silme vb.)
#   smart - yalnızca tehlikeli işlemlerde onay ister (sudo, rm -rf, e-posta gönderme...)
#   ask   - yan etkisi olan her araçta onay ister
AUTONOMY = os.getenv("ADA_AUTONOMY", "smart").strip().lower()


def safety_mode() -> str:
    """Tehlikeli komutlar (bilgisayarı kapatma, disk biçimlendirme, sistemi silme) için güvenlik modu.

    "safe"    - güvenli mod (varsayılan): bu komutlar hiçbir koşulda çalıştırılmaz
    "approve" - onayla her şey: ADA her seferinde sorar, kullanıcı onaylarsa çalıştırır (tam özerk modda bile sorar;
                başında kimsenin olmadığı rutin/arka plan işlerinde yine çalıştırılmaz)
    Ayar yeniden başlatmadan geçerli olur."""
    return "approve" if os.getenv("ADA_SAFETY_MODE", "safe").strip().lower() == "approve" else "safe"
APPROVAL_TIMEOUT = int(os.getenv("ADA_APPROVAL_TIMEOUT", "900"))

WORKSPACE = Path(os.getenv("ADA_WORKSPACE", HOME_DIR / "workspace")).expanduser().resolve()
UPLOADS_DIR = WORKSPACE / "uploads"
OUTPUTS_DIR = WORKSPACE / "outputs"
DATA_DIR = WORKSPACE / ".ada"
CONVERSATIONS_DIR = DATA_DIR / "conversations"
MEMORY_FILE = DATA_DIR / "memory.json"
SCHEDULES_FILE = DATA_DIR / "schedules.json"

# Rutinler: workspace/rutinler/ içine bırakılan (veya arayüzden yüklenen) her .md dosyası bir iştir;
# ADA onu dosyadaki takvime göre (varsayılan her gün) kendi başına yapar, raporunu outputs/rutinler/ altına yazar.
ROUTINES_DIR = WORKSPACE / "rutinler"
ROUTINES_FILE = DATA_DIR / "routines.json"
ROUTINE_REPORTS_DIR = OUTPUTS_DIR / "rutinler"
ROUTINE_MAX_MINUTES = float(os.getenv("ADA_ROUTINE_MAX_MINUTES", "30"))  # bir rutin çalışmasının süre sınırı
ROUTINE_MAX_PARALLEL = int(os.getenv("ADA_ROUTINE_MAX_PARALLEL", "2"))
ROUTINE_DEFAULT_TIME = os.getenv("ADA_ROUTINE_DEFAULT_TIME", "09:00")  # dosyada saat yoksa
ROUTINE_KEEP_RUNS = int(os.getenv("ADA_ROUTINE_KEEP_RUNS", "30"))  # rutin başına saklanan çalışma geçmişi

# Gözcü: ADA açıkken bilgisayarı ve kendi işlerini sürekli denetler (bkz. watchdog.py)
WATCH = _bool("ADA_WATCH", True)
WATCH_SECONDS = max(15, int(os.getenv("ADA_WATCH_SECONDS", "60")))  # ücretsiz ölçümlerin aralığı
# workspace/gozcu.md kontrol listesinin yapay zekâ ile denetlenme aralığı (dakika; 0: kapalı)
WATCH_AI_MINUTES = int(os.getenv("ADA_WATCH_AI_MINUTES", "60"))
WATCH_AI_FIX = _bool("ADA_WATCH_AI_FIX", True)  # yeni bir sorunu ADA kendisi incelesin ve güvenliyse düzeltsin

# Bilgisayar kontrolü
ENABLE_SHELL = _bool("ADA_ENABLE_SHELL", True)        # kabuk komutları, dosya sistemi, uygulama açma
ENABLE_COMPUTER = _bool("ADA_ENABLE_COMPUTER", True)  # ekran görüntüsü + fare/klavye
ENABLE_MAC_APPS = _bool("ADA_ENABLE_MAC_APPS", IS_MAC)  # Mail, Takvim, Anımsatıcılar, Notlar (AppleScript)
ENABLE_PYTHON = _bool("ADA_ENABLE_PYTHON", True)
SHELL_TIMEOUT = int(os.getenv("ADA_SHELL_TIMEOUT", "300"))
PYTHON_TIMEOUT = int(os.getenv("ADA_PYTHON_TIMEOUT", "300"))
SCREENSHOT_MAX_WIDTH = int(os.getenv("ADA_SCREENSHOT_MAX_WIDTH", "1280"))

# E-posta (IMAP) - Mac dışı sistemler veya Mail.app kullanmayanlar için
IMAP_HOST = os.getenv("ADA_IMAP_HOST", "imap.gmail.com")
IMAP_USER = os.getenv("ADA_IMAP_USER", "")
IMAP_PASSWORD = os.getenv("ADA_IMAP_PASSWORD", "")
IMAP_DRAFTS_FOLDER = os.getenv("ADA_IMAP_DRAFTS_FOLDER", "[Gmail]/Drafts")

# Sunucu
HOST = os.getenv("ADA_HOST", "127.0.0.1")
PORT = int(os.getenv("ADA_PORT", "8000"))
# Boş bırakılırsa giriş istenmez (yalnızca 127.0.0.1 için güvenli). Ağa açıyorsanız mutlaka ayarlayın.
PASSWORD = os.getenv("ADA_PASSWORD", "")
# Masaüstü uygulaması: pencere kapatılınca arka planda çalışmaya devam et
KEEP_RUNNING = _bool("ADA_KEEP_RUNNING", True)
# Uygulama içi güncelleme kaynağı: "github:sahip/depo" ya da https://…/latest.json (bkz. updater.py)
from . import REPO as _REPO  # noqa: E402

UPDATE_URL = os.getenv("ADA_UPDATE_URL", f"github:{_REPO}")
# Geri bildirim: kullanıcının yazdığı metin bu adreste önceden doldurulmuş bir GitHub kaydı olarak açılır
FEEDBACK_URL = os.getenv("ADA_FEEDBACK_URL", f"https://github.com/{_REPO}/issues/new")
RELEASES_URL = f"https://github.com/{_REPO}/releases"
# Bağış bağlantısı (boşsa arayüzde bağış bölümü görünmez). "{amount}" varsa seçilen tutarla (1-5 $) değiştirilir.
DONATE_URL = os.getenv("ADA_DONATE_URL", "")
DONATE_AMOUNTS = (1, 2, 3, 4, 5)
UPDATE_AUTO_CHECK = _bool("ADA_UPDATE_AUTO_CHECK", True)
# Arayüz dilleri (sözlükler: frontend/public/assets/i18n/<dil>.json). ADA_LANG: "auto" (sistem dili) ya da bir kod.
LANGS = ("tr", "en", "de", "fr", "es", "pt", "it", "ru", "zh", "ja")
LANG_NAMES = {"tr": "Türkçe", "en": "English", "de": "Deutsch", "fr": "Français", "es": "Español", "pt": "Português",
              "it": "Italiano", "ru": "Русский", "zh": "中文", "ja": "日本語"}


def ui_language() -> str:
    """Geçerli arayüz dili kodu. "auto" ise işletim sisteminin dili; desteklenmiyorsa İngilizce."""
    value = os.getenv("ADA_LANG", "auto").strip().lower()
    if value in LANGS:
        return value
    code = ""
    try:
        import locale

        if IS_WIN:
            import ctypes

            code = (locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage()) or "")[:2].lower()
        else:
            code = (locale.getlocale()[0] or os.getenv("LANG", ""))[:2].lower()
    except Exception:  # noqa: BLE001 - dil okunamazsa İngilizce
        code = ""
    return code if code in LANGS else "en"


# Masaüstü bildirimleri (rutin bitti, onay bekleniyor, hedef durumu...)
NOTIFICATIONS = _bool("ADA_NOTIFICATIONS", True)

for _d in (UPLOADS_DIR, OUTPUTS_DIR, CONVERSATIONS_DIR, ROUTINES_DIR):
    _d.mkdir(parents=True, exist_ok=True)
