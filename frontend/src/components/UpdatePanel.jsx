import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Download, RefreshCw } from "lucide-react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { api } from "../api";

// Uygulama içi güncelleme: sürümü gösterir, yenisini denetler, indirip kurar (ADA kapanıp yeniden açılır).

const post = (path) => api(path, { method: "POST" });
const PHASE = { downloading: "İndiriliyor", verifying: "İmza doğrulanıyor", installing: "Kuruluyor — ADA birazdan yeniden açılacak" };
const when = (iso) => (iso ? iso.slice(0, 16).replace("T", " ") : "henüz denetlenmedi");

export default function UpdatePanel({ onClose, onOpenSettings }) {
  const [st, setSt] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const gone = useRef(0); // kurulum sırasında sunucunun kaç kez yanıt vermediği

  const working = st && ["downloading", "verifying", "installing"].includes(st.phase);

  useEffect(() => {
    post("/api/update/check")
      .then(setSt)
      .catch(() => api("/api/update").then(setSt).catch((e) => setError(e.message)));
  }, []);

  // Kurulum sürerken durumu izle; ADA kapanıp yeni sürümle açılınca sayfayı yenile
  useEffect(() => {
    if (!working) return;
    const id = setInterval(async () => {
      try {
        const s = await fetch("/api/update").then((r) => r.json());
        if (gone.current > 0) return location.reload();
        setSt(s);
      } catch {
        gone.current += 1;
      }
    }, 1000);
    return () => clearInterval(id);
  }, [working]);

  const check = async () => {
    setBusy(true);
    setError("");
    try {
      setSt(await post("/api/update/check"));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const install = async () => {
    setError("");
    try {
      setSt(await post("/api/update/install"));
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <div className="modal-bg" onClick={working ? undefined : onClose}>
      <div className="modal update" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <b>Güncelleme</b>
          {!working && (
            <button className="close" onClick={onClose}>
              ✕
            </button>
          )}
        </div>
        {!st ? (
          <div className="muted">{error || "Denetleniyor…"}</div>
        ) : (
          <div className="update-body">
            <div className="update-versions">
              <div>
                <small>Kurulu sürüm</small>
                <b>{st.current}</b>
              </div>
              {st.available && (
                <div className="new">
                  <small>Yeni sürüm</small>
                  <b>{st.latest}</b>
                </div>
              )}
            </div>

            {working ? (
              <div className="update-progress">
                <div className="update-bar">
                  <i style={{ width: (st.phase === "downloading" ? st.progress : 100) + "%" }} />
                </div>
                <span>
                  {PHASE[st.phase]}
                  {st.phase === "downloading" && ` · %${st.progress}`}
                </span>
              </div>
            ) : !st.source ? (
              <div className="update-note">
                Güncelleme adresi ayarlı değil.{" "}
                <button className="linkbtn" onClick={onOpenSettings}>
                  Ayarlar → Genel → Güncelleme adresi
                </button>
              </div>
            ) : st.available ? (
              <>
                {st.notes && <div className="update-notes" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(st.notes)) }} />}
                {!st.can_install && <div className="update-note">Güncelleme yalnızca kurulu Windows uygulamasında yapılabilir.</div>}
              </>
            ) : (
              !st.error && (
                <div className="update-ok">
                  <CheckCircle2 size={18} /> ADA güncel.
                </div>
              )
            )}

            {(error || st.error) && <div className="setup-error">{error || st.error}</div>}

            {!working && (
              <div className="update-foot">
                <span className="muted small">Son denetim: {when(st.checked)}</span>
                <button className="setup-btn" onClick={check} disabled={busy || !st.source}>
                  <RefreshCw size={15} className={busy ? "spin" : ""} /> Denetle
                </button>
                {st.available && st.can_install && (
                  <button className="setup-btn primary" onClick={install}>
                    <Download size={15} /> Şimdi güncelle
                  </button>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
