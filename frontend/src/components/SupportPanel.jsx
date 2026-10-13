import { useEffect, useState } from "react";
import { Bug, Copy, ExternalLink, Heart, Lightbulb, MessageSquare } from "lucide-react";
import { api } from "../api";

// Geri bildirim ve destek: kullanıcının yazdığı metin GitHub'da önceden doldurulmuş bir kayıt olarak açılır
// (göndermeden önce orada görür ve düzenleyebilir); bağış bölümü 1-5 $ arası küçük destekler içindir.

const KINDS = [
  ["hata", Bug, "Hata", "Bir şey çalışmıyor ya da yanlış çalışıyor"],
  ["öneri", Lightbulb, "Öneri", "Yeni özellik ya da iyileştirme fikri"],
  ["diğer", MessageSquare, "Diğer", "Soru, yorum, teşekkür"],
];
const PLACEHOLDER = {
  hata: "Ne yaptınız, ne olmasını bekliyordunuz, ne oldu?",
  öneri: "ADA'da ne olsun isterdiniz? Hangi işinizi kolaylaştırırdı?",
  diğer: "Aklınızdakini yazın…",
};
const MAX = 4000;

export default function SupportPanel({ onClose }) {
  const [about, setAbout] = useState(null);
  const [kind, setKind] = useState("öneri");
  const [text, setText] = useState("");
  const [withInfo, setWithInfo] = useState(true);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api("/api/about").then(setAbout).catch(() => setAbout({}));
  }, []);

  if (!about) return null;

  const info = `ADA ${about.version} · ${about.system}`;
  const body = text.trim() + (withInfo ? `\n\n---\n${info}` : "");
  const title = `[${kind}] ` + text.trim().split("\n")[0].slice(0, 70);
  const issueUrl = about.feedback_url
    ? `${about.feedback_url}?labels=${encodeURIComponent(kind)}&title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`
    : "";
  const ready = text.trim().length >= 5;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(body);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* pano kullanılamıyor */
    }
  };

  const donateUrl = (amount) => (about.donate_url || "").replace("{amount}", amount);

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal support" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <b>Geri bildirim ve destek</b>
          <button className="close" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="support-body">
          <section>
            <h4>ADA'yı birlikte geliştirelim</h4>
            <p>Karşılaştığınız hatayı ya da aklınızdaki fikri yazın; doğrudan geliştiriciye ulaşır.</p>
            <div className="support-kinds">
              {KINDS.map(([id, Icon, label, hint]) => (
                <button key={id} className={kind === id ? "on" : ""} onClick={() => setKind(id)} title={hint}>
                  <Icon size={16} /> {label}
                </button>
              ))}
            </div>
            <textarea value={text} maxLength={MAX} placeholder={PLACEHOLDER[kind]} onChange={(e) => setText(e.target.value)} autoFocus />
            <label className="support-check">
              <input type="checkbox" checked={withInfo} onChange={(e) => setWithInfo(e.target.checked)} />
              <span>
                Sürüm ve sistem bilgisini ekle <small>({info})</small>
              </span>
            </label>
            <div className="support-actions">
              <span className="muted small">
                Gönder'e basınca tarayıcıda GitHub açılır; metni orada görüp onaylarsınız (ücretsiz GitHub hesabı gerekir). Görevleriniz ve dosyalarınız
                gönderilmez.
              </span>
              <button className="setup-btn" onClick={copy} disabled={!ready}>
                <Copy size={15} /> {copied ? "Kopyalandı" : "Kopyala"}
              </button>
              <a
                className={"setup-btn primary" + (ready && issueUrl ? "" : " disabled")}
                href={ready && issueUrl ? issueUrl : undefined}
                target="_blank"
                rel="noreferrer"
                aria-disabled={!ready}
              >
                <ExternalLink size={15} /> Gönder
              </a>
            </div>
          </section>

          {about.donate_url && (
            <section className="support-donate">
              <h4>
                <Heart size={16} /> Destek ol
              </h4>
              <p>ADA ücretsizdir. Geliştirmenin sürmesine küçük bir katkıyla destek olabilirsiniz; tamamen isteğe bağlıdır.</p>
              <div className="support-amounts">
                {(about.donate_amounts || []).map((a) => (
                  <a key={a} href={donateUrl(a)} target="_blank" rel="noreferrer">
                    {a} $
                  </a>
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
