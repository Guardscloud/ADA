import { useEffect, useState } from "react";
import { api } from "../api";
import AIPanel from "./AIPanel";

// Masaüstü penceresi Windows'ta Edge WebView2 kullanır; izin adımları yalnızca macOS'ta vardır
const IS_WIN = navigator.userAgent.includes("Windows");
const PROVIDER_NAMES = {
  adaagi: "ADA-AGI (tüm motorlar, otomatik dağıtım)",
  gemini: "Yalnız Gemini",
  anthropic: "Yalnız Claude",
  selfhost: "Yalnız kendi sunucum",
  safe: "Güvenli mod — tehlikeli komutlar asla çalışmaz",
  approve: "Onayla her şey — tehlikeli komutlar onayınızla çalışır",
};

export default function SettingsPanel({ onClose }) {
  const [tab, setTab] = useState("ai");
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState({});
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loginItem, setLoginItem] = useState(null);
  const [perms, setPerms] = useState(null);
  const [allows, setAllows] = useState([]);
  const loadAllows = () => api("/api/always-allow").then(setAllows).catch(() => {});
  useEffect(() => {
    loadAllows();
  }, []);
  const removeAllow = async (key) => {
    await api("/api/always-allow/remove", { method: "POST", body: JSON.stringify({ key }) });
    loadAllows();
  };

  const requestPerms = async () => {
    setPerms("bekleniyor");
    try {
      setPerms(await api("/api/permissions/request", { method: "POST" }));
    } catch (e) {
      setPerms(null);
      alert(e.message);
    }
  };

  useEffect(() => {
    api("/api/settings").then(setData).catch((e) => alert(e.message));
    api("/api/login-item").then(setLoginItem).catch(() => {});
  }, []);

  const toggleLoginItem = async (enabled) => {
    try {
      setLoginItem(await api("/api/login-item", { method: "POST", body: JSON.stringify({ enabled }) }));
    } catch (e) {
      alert(e.message);
    }
  };

  const set = (key, value) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setSaved(false);
  };

  const save = async () => {
    // Gizli alanlarda boş bırakılan değer "değiştirme" demektir
    const changes = Object.fromEntries(Object.entries(draft).filter(([k, v]) => !(fieldOf(k)?.secret && v === "")));
    if (!Object.keys(changes).length) return onClose();
    setBusy(true);
    try {
      await api("/api/settings", { method: "POST", body: JSON.stringify({ changes }) });
      setSaved(true);
      setDraft({});
      setData(await api("/api/settings"));
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  };

  const restart = async () => {
    try {
      await api("/api/restart", { method: "POST" });
    } catch (e) {
      alert(e.message);
      return;
    }
    setBusy(true);
    // Sunucu yeniden açılana kadar bekle, sonra sayfayı yenile
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      try {
        const r = await fetch("/api/status");
        if (r.ok || r.status === 401) return location.reload();
      } catch {
        /* henüz açılmadı */
      }
    }
    alert("ADA yeniden açılamadı; uygulamayı elle başlatın.");
  };

  const fieldOf = (key) => data?.fields.find((f) => f.key === key);

  if (!data) return null;
  const provider = draft.ADA_PROVIDER ?? fieldOf("ADA_PROVIDER").value;
  // Bulut anahtarları Yapay zekâ sekmesindeki sağlayıcı kartlarından girilir
  const CARD_KEYS = ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "MISTRAL_API_KEY", "GROQ_API_KEY", "XAI_API_KEY", "OPENROUTER_API_KEY", "TOGETHER_API_KEY"];
  const hidden = {
    ...Object.fromEntries(CARD_KEYS.map((k) => [k, true])),
    GEMINI_API_KEY: provider !== "gemini",
    ADA_GEMINI_MODELS: provider !== "gemini",
    ADA_QWEN_API_KEY: provider !== "gemini",
    ADA_QWEN_MODEL: provider !== "gemini",
    ADA_QWEN_BASE_URL: provider !== "gemini",
    ADA_QWEN_FALLBACK: provider !== "gemini",
    ADA_QWEN_VISION: provider !== "gemini",
    ANTHROPIC_API_KEY: provider !== "anthropic",
    ADA_MODEL: provider !== "anthropic",
    ADA_EFFORT: provider !== "anthropic",
    ADA_LLM_BASE_URL: provider !== "selfhost",
    ADA_LLM_MODEL: provider !== "selfhost",
    ADA_LLM_API_KEY: provider !== "selfhost",
  };
  if (provider === "adaagi") {
    // ADA-AGI motorları kartlardan yönetilir; Gemini/Claude anahtarları da oradadır
    Object.assign(hidden, { GEMINI_API_KEY: true, ANTHROPIC_API_KEY: true });
  }

  const renderField = (f) => {
    const value = draft[f.key] ?? (f.secret ? "" : f.value);
    return (
      <label key={f.key} className="srow">
        <span className="slabel">{f.label}</span>
        {f.type === "select" ? (
          <select value={value} onChange={(e) => set(f.key, e.target.value)}>
            {f.options.map((o) => (
              <option key={o} value={o}>
                {PROVIDER_NAMES[o] || o}
              </option>
            ))}
          </select>
        ) : f.type === "bool" ? (
          <input type="checkbox" checked={value === "true"} onChange={(e) => set(f.key, e.target.checked ? "true" : "false")} />
        ) : (
          <input
            type={f.secret ? "password" : f.type === "number" ? "number" : "text"}
            value={value}
            placeholder={f.secret ? (f.set ? `kayıtlı (${f.value}) — değiştirmek için yazın` : "girilmedi") : ""}
            onChange={(e) => set(f.key, e.target.value)}
            autoComplete="off"
          />
        )}
      </label>
    );
  };

  const legacy = (
    <details className="ai-legacy">
      <summary>Gelişmiş: çalışma modu ve tek motor ayarları</summary>
      {data.fields.filter((f) => f.group === "ai" && !hidden[f.key]).map(renderField)}
    </details>
  );

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal settings" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <b>Ayarlar</b>
          <div className="tabs settings-tabs">
            <button className={tab === "ai" ? "on" : ""} onClick={() => setTab("ai")}>
              Yapay zekâ
            </button>
            <button className={tab === "general" ? "on" : ""} onClick={() => setTab("general")}>
              Genel
            </button>
          </div>
          <button className="close" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="settings-body">
          {tab === "ai" && <AIPanel legacy={legacy} onRestart={restart} />}
          {tab === "general" && (
            <>
          {loginItem?.supported && !IS_WIN && (
            <div className="srow perms">
              <span className="slabel">
                macOS izinleri (Mail, Takvim, Notlar, ekran…)
                <small>Arka plan görevleri izin penceresinde takılmasın diye, başındayken bir kez isteyin.</small>
              </span>
              <button onClick={requestPerms} disabled={perms === "bekleniyor"}>
                {perms === "bekleniyor" ? "Açılan pencereleri onaylayın…" : "İzinleri şimdi iste"}
              </button>
            </div>
          )}
          {Array.isArray(perms) && (
            <div className="perm-list">
              {perms.map((p) => (
                <div key={p.name} className={p.ok ? "ok-text" : "warn"} title={p.message}>
                  {p.ok ? "✓" : "✗"} {p.name}
                  {!p.ok && (
                    <button
                      className="perm-open"
                      onClick={() => api("/api/permissions/open", { method: "POST", body: JSON.stringify({ name: p.name }) })}
                    >
                      Ayarı aç
                    </button>
                  )}
                  {!p.ok && <small> — {p.message}</small>}
                </div>
              ))}
            </div>
          )}
          {loginItem?.supported && (
            <label className="srow">
              <span className="slabel">
                {IS_WIN ? "Windows açılınca ADA'yı başlat (arka planda)" : "Mac açılınca ADA'yı başlat (arka planda)"}
                <small>Rutinlerin her gün zamanında çalışması için açık kalmalı.</small>
              </span>
              <input type="checkbox" checked={!!loginItem.enabled} onChange={(e) => toggleLoginItem(e.target.checked)} />
            </label>
          )}
          {data.fields.filter((f) => f.group !== "ai" && !hidden[f.key]).map(renderField)}
          <div className="allow-box">
            <div className="slabel">"Her zaman izin ver" dediğiniz işlem türleri</div>
            {allows.length === 0 && <div className="muted">Yok — riskli işlemlerde her seferinde onay istenir.</div>}
            {allows.map((a) => (
              <div key={a.key} className="allow-row">
                <span>
                  <b>{a.tool}</b> — {a.reason}
                </span>
                <button onClick={() => removeAllow(a.key)}>Kaldır</button>
              </div>
            ))}
          </div>
          <div className="shint">
            Ayarlar: <code>{data.env_file}</code>
            <br />
            Çalışma alanı: <code>{data.workspace}</code>
          </div>
            </>
          )}
        </div>
        <div className="settings-foot">
          {saved && <span className="ok-text">Kaydedildi. Geçerli olması için yeniden başlatın.</span>}
          <button onClick={save} disabled={busy}>
            Kaydet
          </button>
          <button className="primary" onClick={restart} disabled={busy}>
            {busy ? "Bekleniyor…" : "Yeniden başlat"}
          </button>
        </div>
      </div>
    </div>
  );
}
