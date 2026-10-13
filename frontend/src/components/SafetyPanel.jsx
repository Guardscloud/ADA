import { useEffect, useState } from "react";
import { api } from "../api";

export default function SafetyPanel({ onClose, usage }) {
  const [tab, setTab] = useState("audit");
  const [audit, setAudit] = useState([]);
  const [backups, setBackups] = useState([]);
  const [filter, setFilter] = useState("");

  const load = async () => {
    const [a, b] = await Promise.all([api("/api/audit?limit=300"), api("/api/backups")]);
    setAudit(a);
    setBackups(b);
  };

  useEffect(() => {
    load();
  }, []);

  const restore = async (b) => {
    if (!confirm(`${b.original}\n\nBu dosya ${b.time} tarihindeki haline döndürülsün mü? (Şimdiki hali de yedeklenir.)`)) return;
    try {
      const r = await api(`/api/backups/${b.id}/restore`, { method: "POST" });
      alert(r.message);
      load();
    } catch (e) {
      alert(e.message);
    }
  };

  const f = filter.toLowerCase();
  const rows = audit.filter((a) => !f || `${a.tool} ${a.args} ${a.result}`.toLowerCase().includes(f));

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div className="tabs">
            <button className={tab === "audit" ? "on" : ""} onClick={() => setTab("audit")}>
              İşlem günlüğü
            </button>
            <button className={tab === "backups" ? "on" : ""} onClick={() => setTab("backups")}>
              Yedekler / geri al ({backups.length})
            </button>
          </div>
          <button className="close" onClick={onClose}>
            ✕
          </button>
        </div>
        {usage && (
          <div className="usage">
            Bugün: {usage.tool_calls}/{usage.tool_limit || "∞"} araç çağrısı · {usage.emails}/{usage.email_limit || "∞"} e-posta gönderimi
          </div>
        )}
        {tab === "audit" ? (
          <>
            <input className="filter" placeholder="Ara (araç, dosya, komut…)" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <div className="audit">
              {rows.length === 0 && <div className="muted">Kayıt yok.</div>}
              {rows.map((a, i) => (
                <div key={i} className={"arow" + (a.ok ? "" : " bad")}>
                  <span className="atime">{a.time.slice(5, 16).replace("T", " ")}</span>
                  <span className="atool">{a.tool}</span>
                  {a.approved === true && <span className="tag ok">onaylandı</span>}
                  {a.approved === false && <span className="tag no">reddedildi</span>}
                  <span className="aargs" title={a.args}>
                    {a.args}
                  </span>
                  {!a.ok && (
                    <span className="ares" title={a.result}>
                      {a.result}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="audit">
            {backups.length === 0 && <div className="muted">Henüz yedek yok. ADA bir dosyayı değiştirmeden önce eski halini buraya kaydeder.</div>}
            {backups.map((b) => (
              <div key={b.id} className="arow">
                <span className="atime">{b.time.slice(5, 16).replace("T", " ")}</span>
                <span className="aargs" title={b.original}>
                  {b.original}
                </span>
                <button className="restore" onClick={() => restore(b)}>
                  Geri yükle
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
