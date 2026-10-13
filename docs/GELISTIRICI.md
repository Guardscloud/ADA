# ADA — Yapay Zeka Çalışanı

ADA, bir beyaz yaka profesyonelinin yaptığı işleri bilgisayarınızda **uçtan uca** yapan otonom
bir ajandır. Ona bir iş verirsiniz; araştırır, dosyaları okur, komut çalıştırır, uygulamaları
kullanır, gerekirse ekrana bakıp tıklar ve **bitmiş çıktıyı** teslim eder.

İki yapay zekâ ile çalışır: **Google Gemini** (ücretsiz katman) veya **Claude** (`claude-opus-5-5`,
en güçlüsü, API kredisi gerekir). Python/FastAPI arka uç + React arayüz.

## Neler yapabilir?

| Alan | Yetenekler |
|---|---|
| Bilgisayar | Kabuk komutları (zsh), dosya okuma/yazma/düzenleme/taşıma (tüm disk), uygulama ve URL açma, AppleScript, bildirim |
| Ekran | Ekran görüntüsü alma, yakınlaştırma, tıklama, yazma, tuş, kaydırma, sürükleme (Anthropic computer toolset) |
| macOS uygulamaları | Mail (okuma, taslak, gönderme), Takvim (listeleme, etkinlik ekleme), Anımsatıcılar, Notlar, Kişiler |
| Araştırma | Web arama, sayfa okuma, kaynaklı rapor |
| Belgeler | PDF / Word / PowerPoint / Excel / CSV okuma; Word raporu, Excel tablosu, PowerPoint, grafik üretme |
| Veri analizi | Python + pandas ile analiz, hesaplama, grafik |
| Çoklu ajan | Büyük işleri araştırmacı / analist / yazar / denetçi / operatör alt ajanlarına paralel dağıtma |
| Otomasyon | **Rutinler**: bir .md dosyasıyla tarif edilen işi her gün kendiliğinden yapma; zamanlanmış görevler; bitince masaüstü bildirimi (macOS ve Windows) |
| Hafıza | Şirket, rol, üslup tercihi gibi bilgileri görevler arası hatırlama |

## Otonomi

| Özellik | Nasıl çalışır | Örnek istek |
|---|---|---|
| **Rutinler** | İşi anlatan bir `.md` dosyası yüklenir (veya `workspace/rutinler/` klasörüne bırakılır); ADA o işi dosyadaki takvime göre (varsayılan her gün 09:00) kullanıcı yokken, soru sormadan yapar. Her çalışmanın raporu ve geçmişi tutulur, öğrendikleri bir sonraki çalışmaya taşınır. Ayrıntılar aşağıda. | "Ekteki dosyadaki işi her sabah 9'da yap" · "Hafta içi her akşam 18:00'de satış özetini çıkar" |
| **Hedefler** | Büyük işler arka planda oturumlar halinde yürür: ADA plan kurar, adımları yapar, notlar alır, gerekirse bekler (`goal_wait`), bitince **bağımsız bir denetçi alt ajan** teslimatı kontrol eder; onaylamazsa düzeltme listesiyle devam eder. | "Rakip analizi projesini üstlen, bitince rapor ve sunum ver" |
| **Tetikleyiciler** | Mail'e yeni e-posta (gönderen/konu filtreli) veya bir klasöre yeni dosya düşünce ADA kendiliğinden iş başlatır. Her dakika kontrol edilir; saatte en fazla 10 kez tetiklenir. | "ali@musteri.com'dan e-posta gelince özetle ve yanıt taslağı hazırla" · "İndirilenler'e PDF fatura düşünce Excel listesine ekle" |
| **Beceriler** | Başarıyla yapılan çok adımlı işlerin tarifi kaydedilir; benzer istekte ADA tarifi açıp izler, eksikse günceller. | (otomatik) |
| **Hafıza** | Kalıcı hafıza özeti her isteğe eklenir. | "Şirketim ACME, raporlarda resmi dil kullan — hatırla" |

Kenar çubuğunda Hedefler (durum, ilerleme, duraklat/devam/sil, **+** ile yeni hedef), Tetikleyiciler
ve Beceriler bölümleri vardır. Hedef ve tetikleyici işleri bitince/onay beklerken macOS bildirimi gelir.

Kaçak maliyete karşı: hedef başına oturum sınırı (varsayılan 30), iki oturum ilerleme olmazsa otomatik
duraklatma, aynı anda en fazla 2 hedef, tetikleyici saat limiti.

### Rutinler (her gün kendiliğinden yapılan işler)

Kenar çubuğundaki **Rutinler** bölümünden (veya **+** ile açılan panelden) bir `.md` dosyası yükleyin ya da
dosyayı `workspace/rutinler/` klasörüne bırakın (masaüstü uygulamasında **Kontrol → Rutin klasörünü aç**).
Dosyanın başına isteğe bağlı ayarlar yazılabilir; gerisi yapılacak işin talimatıdır:

```markdown
---
ad: Günlük döviz kurları
saat: 09:15            # birden çok saat: 09:00, 18:00
gunler: hafta içi      # her gün · hafta sonu · Pzt, Çar, Cum · Pazartesi-Cuma
aktif: evet
bildirim: evet
sure: 15               # bir çalışmanın süre sınırı (dakika)
---
# Günlük döviz kurları
İnternetten bugünkü USD/TRY, EUR/TRY ve gram altın değerlerini kaynaklarıyla bul,
Excel'e kaydet, dünkü değerle karşılaştır.
```

- Ayar yazılmazsa her gün 09:00'da çalışır; ad ilk `#` başlığından gelir. `aralik: 30 dk` / `aralik: 2 saat`
  saat yerine belli aralıklarla çalıştırır (en az 5 dakika).
- Her çalışma kendi görevinde (konuşmasında) yürür; rapor `outputs/rutinler/<rutin>/<tarih>-rapor.md`
  dosyasına yazılır, ürettiği dosyalar da aynı klasöre gider. Panelde geçmiş, raporlar ve "Şimdi çalıştır" vardır.
- **Öğrenme:** ADA çalışırken işe yarayan kaynakları, tuzakları `routine_note` ile not eder; notlar ve son
  çalışmaların özeti bir sonraki çalışmanın talimatına eklenir.
- **Dayanıklılık:** ilk başarısız çalışma 15 dakika içinde bir kez yeniden denenir; bilgisayar kapalıyken
  kaçırılan çalışma ADA açılınca bir kez yapılır; uygulama kapanınca yarıda kalan çalışma açılışta yeniden
  denenir; aynı anda en fazla 2 rutin çalışır (`ADA_ROUTINE_MAX_PARALLEL`).
- Sohbetten de kurulur: "Her sabah 08:30'da gelen kutumu özetle, bunu rutin yap" → ADA `routine_create` kullanır.
  "Sabah rutinini durdur", "saatini 8'e al" gibi istekler de anlaşılır.
- **Güvenlik:** rutin dosyası kullanıcının talimatıdır; ama rutin, tetikleyici ve hedef gibi arka plan
  çalışmaları yeni rutin kuramaz, rutinleri değiştiremez veya silemez. Dosya araçları rutin klasörüne yazamaz.
  Talimat gibi görünen notlar kaydedilmez. Klasöre dışarıdan düşen her yeni rutin için bildirim gelir.
- Rutinlerin zamanında çalışması için ADA'nın açık olması gerekir: Ayarlar'daki "açılınca ADA'yı başlat"
  seçeneğini açın (pencere kapatılınca ADA arka planda çalışmaya devam eder).

## Masaüstü uygulaması

**macOS:** `scripts/build_mac.sh` → `dist/ADA.app` (Uygulamalar klasörüne sürükleyin). Uygulama ADA'yı yerel
bir pencerede açar; menü çubuğunda **Kontrol → Pencereyi göster / Acil durdur / Devam et / klasörleri aç /
ADA'dan çık** vardır. Kırmızı kapat düğmesi ADA'yı durdurmaz, Dock'a küçültür: hedefler, tetikleyiciler ve
zamanlanmış görevler arka planda çalışmaya devam eder. Tamamen kapatmak için **Cmd+Q**. Ayarlar'da
"Mac açılınca ADA'yı başlat" seçeneği vardır.

- Ayarlar ve veriler: `~/Library/Application Support/ADA/` (`.env`, `workspace/`). API anahtarı ve diğer
  ayarlar uygulamadaki **⚙ Ayarlar** penceresinden girilir; "Yeniden başlat" ile geçerli olur.
- İlk açılışta macOS "tanınmayan geliştirici" uyarısı verirse: sağ tık → **Aç**. (Dağıtım için Apple
  Developer ID imzası ve notarization gerekir.)
- Ekran kontrolü için **Ekran Kaydı** ve **Erişilebilirlik** izinlerini ADA.app'e verin.

**Windows:** Windows bir bilgisayarda `scripts\build_windows.ps1` → `dist\ADA\ADA.exe`. Kabuk komutları
PowerShell ile çalışır (Türkçe çıktı UTF-8); Mail/Takvim (AppleScript) araçları yalnızca macOS'ta vardır, e-posta
için IMAP ayarlarını kullanın. Bildirimler Windows bildirimleri olarak gelir; Ayarlar'da "Windows açılınca
ADA'yı başlat" seçeneği vardır (arka planda, pencere küçük açılır). ADA ikinci kez açılırsa yeni kopya
başlamaz, çalışan pencere öne gelir. Silinen dosyalar Geri Dönüşüm Kutusu'na gider.
Ayrıntılı kurulum: `ADA-Windows-Kurulum.md`.

**Geliştirme:** `uv run ada-app` (pencere) veya `uv run ada` (yalnızca tarayıcı arayüzü).

## Kurulum

Gereksinimler: [uv](https://docs.astral.sh/uv/) (Python 3.12'yi kendisi indirir). Arayüz derlenmiş
halde depoda gelir; yalnızca arayüzü değiştirecekseniz Node 18+ gerekir.

```bash
cd ADA-AGI
uv sync
cp .env.example .env      # ANTHROPIC_API_KEY değerini girin
uv run ada
```

Tarayıcıda **http://127.0.0.1:8000** adresini açın.

### macOS izinleri (ekran kontrolü için)

Sistem Ayarları → Gizlilik ve Güvenlik → **Ekran Kaydı** ve **Erişilebilirlik** altında ADA'yı
çalıştırdığınız terminale (Terminal / iTerm / VS Code) izin verin. Mail, Takvim vb. ilk
kullanımda macOS "Otomasyon" izni soracaktır; onaylayın.

### API anahtarı

**Gemini (ücretsiz):** https://aistudio.google.com/apikey → *Create API key* → `.env` içinde
`GEMINI_API_KEY=` satırına yapıştırın, `ADA_PROVIDER=gemini` olsun. Ücretsiz katmanda günlük istek
limiti vardır; bir modelin kotası dolunca ADA otomatik olarak sıradaki modele geçer
(`ADA_GEMINI_MODELS`). Not: Google ücretsiz katmandaki içerikleri ürünlerini geliştirmek için
kullanabilir; gizli belgeler için Claude'u veya Gemini'nin ücretli katmanını tercih edin.

**Qwen şimdilik pasiftir** (yedek kapalı, ADA-AGI Qwen modellerini kullanmaz); açmak için `ADA_QWEN_ENABLED=true`.

**Qwen yedeği (Gemini ile birlikte, isteğe bağlı):** Öncelik her zaman Gemini'dedir. Gemini bir
görevi yapamazsa (tüm modellerin kotası dolu, sunucu yoğun, güvenlik filtresi, boş yanıt) görev
kaldığı yerden Qwen ile sürer; sonraki mesaj yine önce Gemini'ye gider. Açmak için
https://modelstudio.console.alibabacloud.com adresinden bir DashScope API anahtarı alıp `.env`
içine `ADA_QWEN_API_KEY=` olarak yazın (ya da ⚙ Ayarlar → "Qwen yedeği"). Model `ADA_QWEN_MODEL`
(varsayılan `qwen-plus`). Görsel okuyan bir model (`qwen-vl-…`) seçerseniz `ADA_QWEN_VISION=true`
yapın; Qwen devraldığında ekran kontrolü de çalışır (Qwen3-VL için `ADA_QWEN_SCREEN_COORDS=norm1000`).
Qwen devraldığında konuşma Alibaba sunucularına gider; istemiyorsanız Ollama'daki yerel bir Qwen
kullanın: `ADA_QWEN_BASE_URL=http://localhost:11434/v1` (anahtar gerekmez).

**Claude:** https://console.anthropic.com → *Settings → API Keys* → anahtar oluşturun, *Billing*'den
kredi yükleyin → `.env` içinde `ANTHROPIC_API_KEY=` ve `ADA_PROVIDER=anthropic`. (Claude.ai / Claude
Code aboneliği API kredisi yerine geçmez; aboneliği kullanmak için aşağıdaki Claude Code motoruna bakın.)

**Claude Code (abonelik, API anahtarı gerekmez):** Bu bilgisayarda [Claude Code](https://claude.com/claude-code)
kuruluysa ve oturumu açıksa (`claude` yazıp `/login`), ADA-AGI onu kendiliğinden motor olarak tanır
(⚙ Ayarlar → Yapay zekâ → "Claude Code (abonelik)"; modeller: Opus, Sonnet, Haiku). Düşünen Claude Code'dur,
işi yapan yine ADA'dır: Claude Code'un kendi araçları kapatılır, ADA'nın araçları ona yalnızca o tur için açılan
yerel bir MCP ucundan verilir. Böylece onaylar, işlem günlüğü, yedekler, günlük sınırlar ve acil durdurma aynen
geçerlidir. Abonelik kullanım sınırı dolarsa motor sınır açılana kadar dinlenir ve iş sıradaki motora geçer.
Gizli işler bu motora verilmez (yalnız yerel model). Kapatmak için `ADA_CLAUDE_CODE=false`.

Sağlayıcı değiştirdiğinizde eski görevlere devam edilemez (geçmiş biçimleri farklıdır); yeni görev açın.
Gemini'de web araması DuckDuckGo ile, ekran kontrolü aynı yerel araçlarla yapılır.

## Güvenlik katmanı

| Özellik | Açıklama |
|---|---|
| **Acil durdur** | Kenar çubuğundaki kırmızı düğme: çalışan tüm görevler iptal edilir, hedefler duraklatılır, tetikleyiciler ve zamanlanmış görevler durur. "Devam et" denene kadar hiçbir iş başlamaz (durum diskte saklanır, yeniden başlatmada da geçerli). |
| **İşlem günlüğü** | Her araç çağrısı (komut, dosya, e-posta, ekran eylemi…) ve onay/ret kararları `workspace/.ada/audit/` altına günlük olarak yazılır; "Günlük / geri al" penceresinde aranabilir. |
| **Geri alma** | ADA var olan bir dosyayı değiştirmeden/üzerine yazmadan önce eski halini yedekler; arayüzden tek tıkla veya "şu dosyayı geri al" diyerek geri yüklenir. (Kabuk komutlarının yaptığı değişiklikler yedeklenemez; silme işlemleri Çöp Sepeti'ne gider.) |
| **Günlük limitler** | `ADA_DAILY_TOOL_LIMIT` (varsayılan 3000 araç çağrısı) ve `ADA_DAILY_EMAIL_LIMIT` (varsayılan 20 e-posta gönderimi). |
| **Sıralı yürütme** | Aynı turdaki yan etkili işlemler modelin verdiği sırayla çalışır; yalnızca okuma/arama araçları paralel çalışır. |

## Özerklik ve güvenlik

`.env` içindeki `ADA_AUTONOMY`:

| Mod | Davranış |
|---|---|
| `full` | Hiç sormaz. Yalnızca diski/sistemi yok eden komutlar (`rm -rf /`, `rm -rf ~`, `mkfs`, `diskutil erase`, `shutdown`…) her koşulda engellenir. Bu engel listesi macOS/Linux komutlarını tanır; Windows'ta (PowerShell) böyle bir liste yoktur — orada koruma işlem günlüğü, dosya yedekleri, Geri Dönüşüm Kutusu, günlük limitler ve acil durdurmadır. |
| `smart` (varsayılan) | Tehlikeli işlemlerde arayüzde **Onayla / Reddet** kartı çıkar: `sudo`, `rm -r`, süreç öldürme, sistem ayarı, e-posta gönderme, takvime yazma, Çöp'e taşıma, sistem dizinine yazma… |
| `ask` | Yan etkisi olan her işlem ve her ekran-eylem paketi onay ister. |

Diğer korumalar:
- Ekran kontrolü sırasında **fareyi ekranın sol üst köşesine götürmek** eylemi anında durdurur (pyautogui fail-safe).
- Arayüzdeki **Durdur** düğmesi çalışan görevi iptal eder.
- `delete_path` dosyaları kalıcı silmez, Çöp Sepeti'ne taşır.
- Sunucu varsayılan olarak yalnızca `127.0.0.1`'den erişilebilir. Ağa açacaksanız `ADA_PASSWORD` ayarlayın (giriş ekranı gelir).
- Onay kartlarına yanıt vermezseniz 15 dakika sonra işlem reddedilmiş sayılır.

ADA'nın yazdığı komutlar ve Python kodu **bu bilgisayarda, sizin yetkinizle** çalışır; bu bir sandbox
değildir. Özellikleri tek tek kapatabilirsiniz: `ADA_ENABLE_SHELL`, `ADA_ENABLE_COMPUTER`,
`ADA_ENABLE_MAC_APPS`, `ADA_ENABLE_PYTHON`.

## Mimari

```
ada/
├── server.py          FastAPI: React arayüzü, giriş, SSE akışı, onaylar, zamanlayıcı, dosyalar
├── agent.py           Ajan döngüsü: model → araçlar (paralel) → model … ; ekran eylem paketi; alt ajanlar
├── providers/gemini.py Gemini döngüsü (aynı olaylar ve araç katmanı; kota dolunca model değiştirme)
├── providers/qwen_fallback.py Gemini yapamazsa turu Qwen'e devreder (geçmiş Gemini biçiminde kalır)
├── providers/selfhost.py OpenAI uyumlu döngü (kendi sunucunuz ve Qwen yedeği)
├── providers/claude_code.py Claude Code motoru: `claude` komutu + ADA araçları için tur başına yerel MCP ucu
├── approval.py        Onay mekanizması ve olay yan kanalı
├── safety.py          Güvenlik: işlem günlüğü, yedek/geri alma, günlük limitler, acil durdurma
├── runtime.py         Görev başlatma çekirdeği (arayüz, zamanlayıcı, tetikleyici ve hedefler ortak kullanır)
├── goals.py           Hedef modu: kayıt, oturum yürütücü, denetçi doğrulaması
├── triggers.py        Tetikleyiciler: yeni e-posta, klasöre yeni dosya
├── skills.py          Beceri kütüphanesi (workspace/.ada/skills/)
├── scheduler.py       Zamanlanmış görevler (workspace/.ada/schedules.json)
├── prompts.py         ADA'nın rolü + alt ajan rolleri
├── store.py           Konuşma geçmişi (workspace/.ada/conversations/*.json)
├── config.py          .env ayarları (paketli uygulamada ~/Library/Application Support/ADA)
├── settings.py        Arayüzden ayar düzenleme (.env'e yazar)
├── desktop.py         Masaüstü uygulaması (pywebview penceresi + yerel sunucu)
├── tools/             Yetenekler — her biri @tool ile kaydedilir
│   ├── system.py          kabuk, dosya sistemi, open, AppleScript, bildirim (+ tehlike filtreleri)
│   ├── computer.py        ekran görüntüsü / fare / klavye (computer_toolset_20260801 üyeleri)
│   ├── mac_apps.py        Mail, Takvim, Anımsatıcılar, Notlar, Kişiler (AppleScript)
│   ├── files.py           belge okuma, dosya listeleme/yazma
│   ├── office.py          Word, Excel, PowerPoint, grafik
│   ├── code.py            Python ile veri analizi
│   ├── web.py             web_search / web_fetch (Gemini için; Claude'da sunucu araçları kullanılır)
│   ├── agents.py          delegate_tasks — rol bazlı paralel alt ajanlar
│   ├── scheduling.py      schedule_task / list / cancel
│   ├── autonomy.py        hedef, tetikleyici ve beceri araçları
│   ├── email_tools.py     IMAP (Mac dışı)
│   ├── calendar_tools.py  .ics davet dosyası
│   └── memory.py          kalıcı hafıza
└── static/dist/       Derlenmiş React arayüzü
frontend/              React + Vite kaynak kodu (src/App.jsx, components/…)
evals/                 Kalite değerlendirme seti (tasks.json + run.py)
workspace/
├── uploads/           yüklediğiniz dosyalar
└── outputs/           ADA'nın ürettiği dosyalar (arayüzde indirme bağlantısı)
```

Web arama/okuma Anthropic sunucularında çalışan hazır araçlardır. Görevler arka planda çalışır;
tarayıcıyı kapatsanız da devam eder. Uzun görevlerde eski araç sonuçları sunucu tarafında
temizlenir (context editing), geçmiş yalnızca sona eklenir.

### Arayüzü geliştirmek

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173, /api istekleri 8000'e yönlenir (ADA'yı ayrıca çalıştırın)
npm run build    # ada/static/dist güncellenir; commit edin
```

### Yeni yetenek eklemek

`ada/tools/` altında bir modül oluşturun, fonksiyonu `@tool(...)` ile işaretleyin ve modül adını
`ada/tools/__init__.py` içindeki `_MODULES` listesine ekleyin. `risk` fonksiyonu bir gerekçe
döndürürse işlem `smart` modunda onaya düşer:

```python
from . import tool

@tool("crm_delete_customer", "CRM'de müşteri siler.", {"id": {"type": "string"}}, ["id"],
      risk=lambda a: f"Müşteri silinecek: {a.get('id')}")
def crm_delete_customer(id: str) -> str:
    ...
```

Async handler'lar (`async def`) da desteklenir.

## Kalite ölçümü (evals)

```bash
ADA_AUTONOMY=full uv run python evals/run.py            # 6 görevi çalıştırır, Claude hakem puanlar
uv run python evals/run.py kisa-yanit excel-butce        # seçili görevler
```

Sonuçlar `evals/results/` altına yazılır. Her çalıştırma gerçek API çağrısı yapar (maliyet).

## Maliyet

Her görev, düşünme ve araç turlarıyla birden çok API çağrısı yapar; ekran kontrolü görev başına
çok sayıda görüntü gönderir. Maliyeti azaltmak için `ADA_EFFORT=medium` veya
`ADA_MODEL=claude-sonnet-5-5` deneyebilirsiniz.

## Arayüz dilleri

Bileşenler Türkçe yazılır; diğer diller sözlükle gelir (`frontend/public/assets/i18n/<dil>.json`, `{"Türkçe metin": "çeviri"}`).
`frontend/src/i18n.js` ekrandaki metinleri ve `title`/`placeholder` gibi özellikleri DOM düzeyinde çevirir; sohbet içeriği, kod ve
terminal çevrilmez. Sözlükte olmayan metin Türkçe kalır. Dil `ADA_LANG` ayarıdır (`auto`: sistem dili; desteklenmiyorsa İngilizce) ve
Kontrol menüsündeki seçiciden değiştirilir. ADA'nın yanıt dili ayrıdır: kullanıcı hangi dilde yazarsa o dilde yanıt verir.

Yeni metin ekledikten sonra:

```powershell
uv run python scripts/i18n_extract.py           # kataloğu yeniler (catalog.json)
uv run python scripts/i18n_extract.py --check   # her dilde eksik çevirileri sayar
```

Eksikleri sözlük dosyalarına ekleyin; değişkenli metinlerde `{0}`, `{1}` yer tutucuları korunmalıdır. Yeni dil eklemek için
`<dil>.json` sözlüğünü oluşturup kodu `ada/config.py` içindeki `LANGS` ve `frontend/src/i18n.js` içindeki `LANGS` listelerine ekleyin.

## Güvenlik modu

Tehlikeli komutlar (bilgisayarı kapatma/yeniden başlatma, disk biçimlendirme, sürücü kökünü ya da sistemi silme) için
`ADA_SAFETY_MODE` ayarı vardır (Ayarlar → Genel → Güvenlik modu; yeniden başlatma gerekmez):

- `safe` (varsayılan, **güvenli mod**): bu komutlar hiçbir koşulda çalıştırılmaz.
- `approve` (**onayla her şey**): ADA her seferinde onay ister; onaylarsanız çalıştırır. Tam özerk modda bile sorar ve
  "her zaman izin ver" sunmaz. Başında kimsenin olmadığı işlerde (rutinler, arka plan görevleri) yine çalıştırmaz.

Kalıplar `ada/tools/system.py` içindeki `HARD_BLOCK` listesindedir; `run_shell` ve terminal araçları için geçerlidir.
