import { useEffect, useState } from "react";
import { api } from "../api";

const t = (iso) => (iso ? iso.slice(5, 16).replace("T", " ") : "—");
const CHECK_STATUS = { ok: "sorun yok", issue: "dikkat gereken bir şey var", error: "hata", running: "çalışıyor" };

function Meter({ label, value, warn = 90, sub }) {
  const v = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div className="meter">
      <div className="mlabel">
        <span>{label}</span>
        <b>%{Math.round(v)}</b>
      </div>
      <div className="mbar">
        <div className={"mfill" + (v >= warn ? " hot" : "")} style={{ width: v + "%" }} />
      </div>
      {sub && <div className="msub">{sub}</div>}
    </div>
  );
}

export default function WatchPanel({ onClose, onOpenConv, onOpenSettings }) {
  const [st, setSt] = useState(null);
  const [text, setText] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = () => api("/api/watch").then(setSt).catch(() => {});

  useEffect(() => {
    load();
    api("/api/watch/checklist")
      .then((c) => setText(c.content))
      .catch(() => {});
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  const save = async () => {
    setBusy(true);
    try {
      await api("/api/watch/checklist", { method: "POST", body: JSON.stringify({ content: text }) });
      setDirty(false);
      load();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  };

  const checkNow = async () => {
    try {
      if (dirty) await save();
      const r = await api("/api/watch/check", { method: "POST" });
      load();
      if (confirm("Kontrol başladı. Konuşmasını açmak ister misiniz?")) onOpenConv(r.conv_id);
    } catch (e) {
      alert(e.message);
    }
  };

  const m = st?.metrics || {};
  const cl = st?.checklist || {};

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal watch" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <b>🛡 Gözcü</b>
          <span className="muted" style={{ marginLeft: 10, fontSize: 12 }}>
            {st ? (st.enabled ? `açık · her ${st.tick_seconds} sn ölçüm · son: ${t(st.last_tick)}` : "kapalı (Ayarlar'dan açın)") : "…"}
          </span>
          <button className="close" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="watch-body">
          <div className="watch-col">
            <h4>Şu an</h4>
            {m.time ? (
              <>
                <Meter label="İşlemci" value={m.cpu} warn={95} />
                <Meter label="Bellek" value={m.mem} warn={92} sub={`${m.mem_used_gb} / ${m.mem_total_gb} GB`} />
                {(m.disks || []).map((d) => (
                  <Meter key={d.mount} label={"Disk " + d.mount} value={d.percent} sub={`${d.free_gb} GB boş / ${d.total_gb} GB`} />
                ))}
                <div className="watch-facts">
                  <span>İnternet: {m.internet ? "✓ var" : "✕ yok"}</span>
                  {m.qwen !== null && m.qwen !== undefined && <span>Yerel model sunucusu: {m.qwen ? "✓ yanıt veriyor" : "✕ yanıt vermiyor"}</span>}
                  {m.battery && (
                    <span>
                      Pil: %{m.battery.percent} {m.battery.plugged ? "(şarjda)" : ""}
                    </span>
                  )}
                  <span>Açık kalma: {m.uptime_h} saat</span>
                </div>
              </>
            ) : (
              <div className="muted">İlk ölçüm bekleniyor…</div>
            )}

            <h4>Sorunlar</h4>
            {st?.problems?.length ? (
              st.problems.map((p) => (
                <div key={p.key} className={"problem " + p.level}>
                  <b>{p.title}</b>
                  <div>{p.detail}</div>
                  <div className="muted small">
                    {t(p.since)}'den beri
                    {st.fixing?.[p.key] && (
                      <>
                        {" · "}
                        <button className="linkbtn" onClick={() => onOpenConv(st.fixing[p.key])}>
                          ADA inceliyor →
                        </button>
                      </>
                    )}
                  </div>
                </div>
              ))
            ) : (
              <div className="ok-line">✓ Şu an sorun yok</div>
            )}
            <div className="muted small">
              Yeni sorunda bildirim gelir{st?.ai_fix ? "; ADA sorunu kendisi inceler ve güvenli düzeltmeleri yapar" : ""}.{" "}
              <button className="linkbtn" onClick={onOpenSettings}>
                Ayarlar
              </button>
            </div>

            <h4>Geçmiş</h4>
            <div className="watch-history">
              {(st?.history || []).length === 0 && <div className="muted">Kayıt yok.</div>}
              {(st?.history || []).map((h, i) => (
                <div key={i} className={"hrow " + (h.event === "çözüldü" ? "solved" : h.level || "")}>
                  <span className="atime">{t(h.time)}</span>
                  <span>
                    {h.event === "çözüldü" ? "✓ " : "⚠ "}
                    {h.title}
                    {h.conv_id && (
                      <button className="linkbtn" onClick={() => onOpenConv(h.conv_id)}>
                        {" "}
                        aç →
                      </button>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="watch-col">
            <h4>Kontrol listesi (gozcu.md)</h4>
            <div className="muted small">
              ADA bu maddeleri {cl.interval_minutes > 0 ? `her ${cl.interval_minutes} dakikada` : "(aralık 0: yalnızca elle)"} yapay zekâ ile kontrol eder. Her
              madde "- " ile başlayan bir satırdır. Sorun yoksa sessiz kalır.
            </div>
            <textarea
              className="checklist"
              value={text}
              spellCheck={false}
              onChange={(e) => {
                setText(e.target.value);
                setDirty(true);
              }}
            />
            <div className="watch-actions">
              <button onClick={save} disabled={!dirty || busy}>
                {dirty ? "Kaydet" : "Kaydedildi"}
              </button>
              <button onClick={checkNow} disabled={cl.running}>
                {cl.running ? "Kontrol ediliyor…" : "Şimdi kontrol et"}
              </button>
            </div>
            <div className="muted small">
              Etkin madde: {cl.items ?? 0} · Son kontrol: {t(cl.last_run)}
              {cl.last_status && ` (${CHECK_STATUS[cl.last_status] || cl.last_status})`}
              {cl.next_run && ` · Sonraki: ${t(cl.next_run)}`}
            </div>
            {cl.last_summary && cl.last_status !== "ok" && (
              <div className="problem warn">
                {cl.last_summary}
                {cl.last_conv && (
                  <div>
                    <button className="linkbtn" onClick={() => onOpenConv(cl.last_conv)}>
                      Konuşmayı aç →
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
