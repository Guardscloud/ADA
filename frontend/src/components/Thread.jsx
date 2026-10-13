import { useEffect, useRef } from "react";
import { BarChart3, Clock, Download, FileSearch, FolderTree, Mail, Repeat, Sparkles, Square, Users } from "lucide-react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import duckImg from "../assets/duck-96.png";
import duckBig from "../assets/duck-192.png";
import { fileUrl } from "../api";
import { EXAMPLES } from "../examples";

const md = (text) => ({ __html: DOMPurify.sanitize(marked.parse(text || "")) });

const EXAMPLE_ICONS = {
  "Araştırma + rapor": FileSearch,
  "Bilgisayarda düzen": FolderTree,
  "Veri analizi": BarChart3,
  "E-posta": Mail,
  "Çoklu ajan": Users,
  Otomasyon: Clock,
  Rutin: Repeat,
};

function FileCard({ path }) {
  const name = path.split("/").pop();
  const ext = name.includes(".") ? name.split(".").pop() : "dosya";
  const url = fileUrl(path);
  if (/^(png|jpe?g|gif|webp)$/i.test(ext)) {
    return (
      <a className="file image" href={url} target="_blank" rel="noreferrer">
        <img src={url} alt={name} />
        <span>{name}</span>
      </a>
    );
  }
  return (
    <a className="file" href={url} download>
      <span className={"ext ext-" + ext.toLowerCase()}>{ext}</span>
      <span className="nm">{name}</span>
      <Download size={15} className="dl" />
    </a>
  );
}

function ApprovalCard({ item, onApprove }) {
  return (
    <div className={"approval" + (item.resolved ? " resolved" : "")}>
      <b>
        Onay gerekli{item.agent ? ` · ${item.agent}` : ""}: {item.title}
      </b>
      <pre>{item.detail}</pre>
      {!item.resolved && (
        <div className="approval-actions">
          <button className="ok" onClick={() => onApprove(item.id, true)}>
            Onayla
          </button>
          {item.can_always && (
            <button className="always" onClick={() => onApprove(item.id, true, true)} title="Bu tür işlemler bir daha sorulmaz (Ayarlar'dan geri alınabilir)">
              Her zaman izin ver
            </button>
          )}
          <button className="no" onClick={() => onApprove(item.id, false)}>
            Reddet
          </button>
        </div>
      )}
    </div>
  );
}

function AssistantMessage({ entry, isLast, running, onStop, onApprove }) {
  const live = isLast && running;
  return (
    <div className="msg assistant">
      <div className="who">
        <img src={duckImg} alt="" className={live ? "busy" : ""} />
        <b>ADA</b>
        {live && <span className="who-state">çalışıyor</span>}
      </div>
      <div className="msg-body">
        {entry.thinking && (
          <details className="think">
            <summary>Düşünce süreci</summary>
            <div>{entry.thinking}</div>
          </details>
        )}
        {entry.steps.length > 0 && (
          <details className="steps" open={live || entry.steps.length <= 6}>
            <summary>
              {entry.steps.length} adım
              {entry.steps.some((s) => s.error) && <span className="step-errs"> · {entry.steps.filter((s) => s.error).length} hata</span>}
            </summary>
            <div className="step-list">
              {entry.steps.map((s, i) => (
                <div key={i} className={"step" + (s.error ? " err" : "") + (live && i === entry.steps.length - 1 ? " now" : "")}>
                  {s.agent && <span className="agent">{s.agent}</span>}
                  <span className="l">{s.label}</span>
                  <span className="d">{s.detail}</span>
                </div>
              ))}
            </div>
          </details>
        )}
        {entry.screenshot && (
          <div className="screen">
            <img src={"data:image/png;base64," + entry.screenshot} alt="ADA'nın gördüğü ekran" />
            <small>ADA'nın gördüğü ekran</small>
          </div>
        )}
        {(entry.approvals || []).map((a) => (
          <ApprovalCard key={a.id} item={a} onApprove={onApprove} />
        ))}
        {entry.text && <div className="bubble" dangerouslySetInnerHTML={md(entry.text)} />}
        {entry.files.length > 0 && (
          <div className="files">
            {entry.files.map((f) => (
              <FileCard key={f} path={f} />
            ))}
          </div>
        )}
        {live && (
          <div className="working">
            <span className="typing">
              <i />
              <i />
              <i />
            </span>
            <span>Çalışıyor…</span>
            <button onClick={onStop}>
              <Square size={11} fill="currentColor" /> Durdur
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function Onboarding({ status, onOpenSettings }) {
  const gemini = status.provider === "gemini";
  return (
    <div className="onboard">
      <img src={duckBig} alt="" className="hero-logo" />
      <h1>ADA'ya hoş geldiniz</h1>
      <p>Başlamak için bir yapay zekâ anahtarı gerekiyor. Ücretsiz seçenek Google Gemini:</p>
      <ol>
        <li>
          <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">aistudio.google.com/apikey</a> adresine Google hesabınızla girin.
        </li>
        <li>
          <b>Create API key</b> → <b>Copy key</b>.
        </li>
        <li>
          Aşağıdaki düğmeyle Ayarlar'ı açın, {gemini ? "Gemini API anahtarı" : "Yapay zekâ: gemini ve Gemini API anahtarı"} alanına yapıştırın,{" "}
          <b>Kaydet</b> → <b>Yeniden başlat</b>.
        </li>
      </ol>
      <button className="primary" onClick={onOpenSettings}>
        Ayarları aç
      </button>
      <p className="muted">
        Claude kullanmak isterseniz console.anthropic.com'dan API anahtarı alıp kredi yükleyin (Claude aboneliği API'yi kapsamaz).
      </p>
    </div>
  );
}

function greeting() {
  const h = new Date().getHours();
  if (h < 6) return "İyi geceler";
  if (h < 12) return "Günaydın";
  if (h < 18) return "İyi günler";
  return "İyi akşamlar";
}

function EmptyState({ onExample, status, onOpenSettings }) {
  if (status && !status.api_key) return <Onboarding status={status} onOpenSettings={onOpenSettings} />;
  return (
    <div className="empty">
      <img src={duckBig} alt="" className="hero-logo" />
      <h1>{greeting()}. Bugün ne yapalım?</h1>
      <p>Rapor, analiz, sunum, e-posta, araştırma ya da bilgisayarda bir iş. Görevi yazın, ADA baştan sona yapsın.</p>
      <div className="examples">
        {EXAMPLES.map(([title, prompt]) => {
          const Icon = EXAMPLE_ICONS[title] || Sparkles;
          return (
            <button key={title} onClick={() => onExample(prompt)}>
              <span className="ex-icon">
                <Icon size={17} />
              </span>
              <b>{title}</b>
              <span className="ex-text">{prompt}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function Thread({ entries, running, onStop, onApprove, onExample, status, onOpenSettings }) {
  const box = useRef(null);

  // Kullanıcı en alta yakınsa yeni içerikle birlikte kaydır
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 260;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [entries]);

  return (
    <div id="chat" ref={box}>
      <div className="wrap">
        {entries.length === 0 && !running ? (
          <EmptyState onExample={onExample} status={status} onOpenSettings={onOpenSettings} />
        ) : (
          entries.map((e, i) =>
            e.role === "user" ? (
              <div key={i} className="msg user">
                <div className="bubble">{e.text}</div>
              </div>
            ) : (
              <AssistantMessage key={i} entry={e} isLast={i === entries.length - 1} running={running} onStop={onStop} onApprove={onApprove} />
            ),
          )
        )}
      </div>
    </div>
  );
}
