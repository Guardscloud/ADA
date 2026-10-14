import { useState } from "react";

const ROUTINE_SAMPLE = `---
ad: Günlük haber özeti
saat: 09:00
gunler: hafta içi
bildirim: evet
---
# Günlük haber özeti
Her sabah sektörümle ilgili önemli haberleri tara,
her birini 2-3 cümleyle özetle ve kaynaklarıyla Word raporu hazırla.`;

const SECTIONS = [
  {
    id: "baslarken",
    title: "Başlarken",
    body: (
      <>
        <p>ADA bir yapay zekâ çalışanıdır: ona bir iş verirsiniz, bilgisayarınızda araştırır, dosyaları okur, komut çalıştırır, uygulamaları kullanır ve bitmiş çıktıyı teslim eder.</p>
        <ol>
          <li>
            <b>⚙ Ayarlar</b>'dan yapay zekâ anahtarını girin (ücretsiz: Google Gemini).
          </li>
          <li>
            <b>⚙ Ayarlar → İzinleri şimdi iste</b> ile macOS izinlerini bir kez verin; Ekran Kaydı iznini Sistem Ayarları'ndan açın.
          </li>
          <li>Alttaki kutuya işi yazın ve Enter'a basın. Dosya eklemek için 📎.</li>
        </ol>
        <p>Çıktılar (Word, Excel, PDF, sunum…) yanıtın altında indirme bağlantısı olarak görünür; hepsine üst çubuktaki <i>Kontrol → Çıktılar</i> ile ulaşırsınız.</p>
      </>
    ),
  },
  {
    id: "ornekler",
    title: "Örnek işler",
    body: (
      <ul>
        <li>"Ekteki Excel'deki satışları bölgelere göre analiz et, grafikli PDF rapor hazırla."</li>
        <li>"Mail'deki okunmamış e-postaları özetle, acil olanlara yanıt taslağı hazırla."</li>
        <li>"Gelecek Salı 14:00'e ekip toplantısı ekle, gündemi Notlar'a yaz."</li>
        <li>"Masaüstümdeki PDF faturaları tarihe göre yeniden adlandır ve Excel listesi çıkar."</li>
        <li>"Türkiye'deki e-ticaret pazarını araştır, kaynaklı Word raporu yaz."</li>
        <li>"Bu PDF'in 3-5. sayfalarını ayrı PDF yap." · "Şu iki PDF'i birleştir."</li>
      </ul>
    ),
  },
  {
    id: "hedefler",
    title: "Hedefler, tetikleyiciler, zamanlama",
    body: (
      <>
        <p>
          <b>Hedef:</b> Tek seferde bitmeyecek işler için. "Rakip analizi projesini üstlen" deyin veya kenar çubuğunda <b>Hedefler +</b>. ADA plan kurar, arka planda oturumlar halinde çalışır; bitince bağımsız bir denetçi kontrol eder.
        </p>
        <p>
          <b>Tetikleyici:</b> "ali@firma.com'dan e-posta gelince özetle", "İndirilenler'e PDF düşünce Excel'e ekle". Her dakika kontrol edilir.
        </p>
        <p>
          <b>Zamanlama:</b> "Her hafta içi 08:30'da gelen kutumu özetle". Kenar çubuğunda görünür, ✕ ile iptal edilir.
        </p>
        <p>Bunların çalışması için ADA'nın açık olması gerekir: kırmızı kapat düğmesi ADA'yı Dock'a küçültür, arka planda çalışmaya devam eder. Tamamen kapatmak için Cmd+Q. Ayarlar'dan "Mac açılınca başlat" seçilebilir.</p>
      </>
    ),
  },
  {
    id: "rutinler",
    title: "Rutinler (her gün kendiliğinden yapılan işler)",
    body: (
      <>
        <p>
          <b>Rutin</b>, ADA'nın sizden komut beklemeden her gün (veya seçtiğiniz günlerde) belirlenen saatte yaptığı bir iştir. İşi bir Markdown (<b>.md</b>) dosyasına düz Türkçe ile yazarsınız; ADA o saatte dosyayı okur, işi baştan sona yapar ve raporunu saklar.
        </p>
        <ol>
          <li>
            Kenar çubuğunda <b>Rutinler +</b> → <b>MD yükle</b> ile dosyayı seçin ya da dosyayı açık Rutinler penceresine sürükleyip bırakın. <b>Yeni rutin</b> ile hazır örnekten başlayarak yazabilirsiniz.
          </li>
          <li>
            Dosyayı çalışma alanındaki <code>rutinler/</code> klasörüne kopyalamak da yeterlidir; ADA yeni dosyayı kendisi fark eder.
          </li>
          <li>
            Rutinler penceresinden saati ve günleri değiştirebilir, rutini kapatıp açabilir, <b>Şimdi çalıştır</b> ile hemen deneyebilir, geçmiş çalışmaların raporlarını indirebilirsiniz.
          </li>
        </ol>
        <p>Dosyanın başına isteğe bağlı ayarlar yazılabilir; hiçbiri yazılmazsa rutin her gün 09:00'da çalışır:</p>
        <pre>{ROUTINE_SAMPLE}</pre>
        <table className="help-table">
          <tbody>
            <tr>
              <td><code>ad</code></td>
              <td>Rutinin adı (yazılmazsa dosyadaki ilk başlık kullanılır)</td>
            </tr>
            <tr>
              <td><code>saat</code> / <code>saatler</code></td>
              <td>
                Çalışma saati: <code>09:00</code>; günde birden çok kez için <code>09:00, 18:00</code>
              </td>
            </tr>
            <tr>
              <td><code>gunler</code></td>
              <td>
                <code>her gün</code>, <code>hafta içi</code>, <code>hafta sonu</code> veya <code>Pzt, Çar, Cum</code> gibi gün listesi
              </td>
            </tr>
            <tr>
              <td><code>aralik</code></td>
              <td>
                Saat yerine belli aralıkla çalışma, dakika cinsinden: <code>aralik: 60</code>
              </td>
            </tr>
            <tr>
              <td><code>aktif</code></td>
              <td>
                <code>evet</code> / <code>hayır</code> — hayır ise kendiliğinden çalışmaz, elle çalıştırılabilir
              </td>
            </tr>
            <tr>
              <td><code>bildirim</code></td>
              <td>
                <code>evet</code> ise her çalışma bitince bildirim gönderir
              </td>
            </tr>
            <tr>
              <td><code>sure</code></td>
              <td>
                Tek çalışmanın en uzun süresi, dakika cinsinden: <code>sure: 30</code>
              </td>
            </tr>
          </tbody>
        </table>
        <p>
          <b>Öğrenme:</b> ADA her çalışmanın sonunda bir sonrakinde işine yarayacak notlar çıkarır (ör. "şu kaynak artık açılmıyor, diğerini kullan"). Notlar rutinin <i>Öğrendikleri</i> bölümünde görünür ve sonraki çalışmalara taşınır.
        </p>
        <p>
          <b>Raporlar:</b> Her çalışmanın raporu ve ürettiği dosyalar <code>outputs/rutinler/&lt;ad&gt;/</code> altına kaydedilir; Rutinler penceresindeki <i>Geçmiş</i> bölümünden indirilebilir.
        </p>
        <p>
          <b>Bilgisayar kapalıysa:</b> kaçırılan çalışma, ADA açıldığında bir kez yapılır ve geçmişte "gecikmeli" olarak işaretlenir. Rutinlerin zamanında çalışması için ADA'nın açık olması gerekir.
        </p>
      </>
    ),
  },
  {
    id: "gozcu",
    title: "Gözcü (sürekli denetim)",
    body: (
      <>
        <p>ADA açık olduğu sürece Gözcü her dakika bilgisayarı ölçer: disk doluluğu, bellek, uzun süre yüksek işlemci, pil, internet, yerel model sunucusu, kalıcı terminal komutları ve art arda başarısız rutinler. Bu ölçümler yapay zekâ kullanmaz, ücretsizdir.</p>
        <ul>
          <li>Yeni bir sorun çıkınca masaüstü bildirimi gelir; kenar çubuğundaki 🛡 düğmesi sarı/kırmızı olur.</li>
          <li>Ayarlar'da "kendisi incelesin" açıksa ADA sorunu arka planda araştırır ve yalnızca güvenli düzeltmeleri yapar (belgelerinizi silmez).</li>
          <li>
            <b>Kontrol listesi</b> (🛡 → sağ taraf): "şunlara sürekli göz kulak ol" maddelerinizi "- " ile yazın. ADA bunları düzenli aralıklarla (varsayılan 60 dk) yapay zekâ ile kontrol eder; her şey yolundaysa sessiz kalır.
          </li>
        </ul>
      </>
    ),
  },
  {
    id: "terminal",
    title: "Terminal (claude remote-control vb.)",
    body: (
      <>
        <p>⌨ Terminal, ADA penceresinde gerçek bir komut satırı açar (Windows'ta PowerShell). Panel kapansa da oturumlar arka planda çalışmaya devam eder; tekrar açınca kaldığı yerden görürsünüz.</p>
        <ul>
          <li>"Claude Remote Control" düğmesi <code>claude remote-control</code> komutunu yeni sekmede başlatır.</li>
          <li>
            <b>Kalıcı komutlar</b>: ADA açılınca kendiliğinden başlar, kapanırsa yeniden başlatılır. Bir komutun hep açık kalmasını istiyorsanız buraya ekleyin.
          </li>
          <li>ADA'ya sohbette "claude remote-control'ü başlat" diyebilirsiniz; açtığı oturumu Terminal'de görürsünüz.</li>
        </ul>
      </>
    ),
  },
  {
    id: "guvenlik",
    title: "Onaylar ve güvenlik",
    body: (
      <>
        <p>
          <b>Onay kartları:</b> sudo, silme, e-posta gönderme, takvime yazma, gizli klasörlere erişim gibi işlemlerde çıkar. <b>Onayla</b> (bir kez), <b>Her zaman izin ver</b> (bu tür bir daha sorulmaz; Ayarlar'dan geri alınır), <b>Reddet</b>.
        </p>
        <p>
          <b>Acil durdur</b> (kırmızı düğme veya Kontrol menüsü): tüm işler durur, "Devam et" diyene kadar hiçbir şey başlamaz. Ekran kontrolünü anında durdurmak için fareyi ekranın sol üst köşesine götürün.
        </p>
        <p>
          <b>Günlük / geri al:</b> ADA'nın yaptığı her işlem kayıtlıdır; değiştirdiği dosyaların eski halleri tek tıkla geri yüklenir.
        </p>
        <p>ADA belgelerde, e-postalarda veya web sayfalarında kendisine yönelik gizli talimatlar görürse uygulamaz ve sizi uyarır. Veri yoksa rakam uydurmaz, sorar.</p>
      </>
    ),
  },
  {
    id: "ses",
    title: "Sesli kullanım",
    body: (
      <>
        <p>
          <b>🎤</b> konuşarak yazma (desteklenmiyorsa macOS dikte: metin kutusunda Fn/🌐 tuşuna iki kez basın). <b>🔊</b> açıkken ADA işi bitirince özeti sesli okur.
        </p>
      </>
    ),
  },
  {
    id: "gizlilik",
    title: "Gizlilik",
    body: (
      <>
        <p>ADA'nın gördüğü içerik (belgeler, e-postalar, ekran görüntüleri) seçilen yapay zekâya gönderilir. Google Gemini'nin <b>ücretsiz</b> katmanında Google bu içeriği ürünlerini geliştirmek için kullanabilir — gizli belgeler için Claude, Gemini'nin ücretli katmanı veya yerel model (Ayarlar → Kendi sunucum) tercih edin.</p>
        <p>Yerel uygulamalarda ADA önce ekran görüntüsü yerine uygulamanın düğme/metin bilgisini kullanır; ekranınızdaki diğer pencereler gönderilmez.</p>
      </>
    ),
  },
  {
    id: "sorun",
    title: "Sorun giderme",
    body: (
      <ul>
        <li>
          <b>"Kota doldu" / "model yoğun":</b> ücretsiz Gemini sınırı; ADA otomatik olarak başka modele geçer. Hepsi doluysa ertesi gün sıfırlanır.
        </li>
        <li>
          <b>Mail/Takvim hatası (-1743 / -1712):</b> Ayarlar → İzinleri şimdi iste; veya Sistem Ayarları → Gizlilik ve Güvenlik → Otomasyon → ADA.
        </li>
        <li>
          <b>Ekran kontrolü çalışmıyor:</b> Sistem Ayarları → Gizlilik ve Güvenlik → Ekran Kaydı ve Erişilebilirlik → ADA.
        </li>
        <li>
          <b>Görev uzun sürdü ve durdu:</b> ADA uzun görevleri 20 dakikalık bölümler halinde kendiliğinden sürdürür; yalnızca ilerleme olmazsa durur. O zaman isteği daraltın.
        </li>
        <li>
          <b>Yanlış bir şey yaptı:</b> Günlük / geri al → Yedekler'den dosyayı eski haline getirin.
        </li>
      </ul>
    ),
  },
];

export default function HelpPanel({ onClose }) {
  const [open, setOpen] = useState("baslarken");
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal help" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <b>ADA kullanım kılavuzu</b>
          <button className="close" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="help-body">
          {SECTIONS.map((s) => (
            <section key={s.id} className={open === s.id ? "open" : ""}>
              <h3 onClick={() => setOpen(open === s.id ? "" : s.id)}>
                {open === s.id ? "▾" : "▸"} {s.title}
              </h3>
              {open === s.id && <div className="help-text">{s.body}</div>}
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
