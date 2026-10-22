import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Circle, Download, LoaderCircle, PackageSearch, RefreshCw, Search, Square, Store, XCircle } from "lucide-react";
import { api } from "../api";

// Kurulum merkezi (/d komutu): hazır katalogdan ya da winget aramasından program seçip sessizce kurar;
// "/d update" ile güncellemesi olan tüm programları ve en sonda ADA'nın kendisini günceller.

const post = (path, body) => api(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });

const ICON = {
  waiting: <Circle size={15} className="ins-i wait" />,
  running: <LoaderCircle size={15} className="ins-i spin run" />,
  ok: <CheckCircle2 size={15} className="ins-i ok" />,
  updated: <CheckCircle2 size={15} className="ins-i ok" />,
  current: <CheckCircle2 size={15} className="ins-i cur" />,
  reboot: <AlertTriangle size={15} className="ins-i warn" />,
  fail: <XCircle size={15} className="ins-i fail" />,
  skipped: <Circle size={15} className="ins-i wait" />,
};

const PHASE = {
  starting: "Başlatılıyor…",
  listing: "Güncellemesi olan programlar aranıyor…",
  working: "Çalışıyor",
  self_update: "ADA'nın yeni sürümü denetleniyor…",
  done: "Bitti",
  cancelled: "Durduruldu",
  error: "Hata",
};

function JobView({ job, onCancel, onBack }) {
  const logRef = useRef(null);
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [job.log.length]);
  const pct = job.total ? Math.round((job.done / job.total) * 100) : job.running ? 5 : 100;
  const s = job.summary || {};
  return (
    <div className="ins-job">
      <div className="update-progress">
        <div className="update-bar">
          <i style={{ width: (job.running ? Math.max(pct, 4) : 100) + "%" }} />
        </div>
        <span>
          {PHASE[job.phase] || job.phase}
          {job.current && job.running && ` · ${job.current}`}
          {job.total > 0 && ` · ${job.done}/${job.total}`}
        </span>
      </div>

      {job.items.length > 0 && (
        <ul className="ins-items">
          {job.items.map((it) => (
            <li key={it.id} className={"ins-row st-" + it.state}>
              {ICON[it.state] || ICON.waiting}
              <span className="ins-name">{it.name}</span>
              <span className="ins-state">{job.labels?.[it.state] || (it.state === "waiting" ? "Sırada" : it.state === "running" ? "Kuruluyor…" : it.state)}</span>
              {it.message && <span className="ins-msg">{it.message}</span>}
            </li>
          ))}
        </ul>
      )}
      {job.kind === "update" && !job.running && job.items.length === 0 && job.phase === "done" && (
        <div className="update-ok">
          <CheckCircle2 size={18} /> Tüm programlar güncel.
        </div>
      )}

      {job.error && <div className="setup-error">{job.error}</div>}

      {!job.running && (
        <div className="ins-summary">
          {(s.ok || 0) + (s.updated || 0) > 0 && <span className="badge">{(s.ok || 0) + (s.updated || 0)} başarılı</span>}
          {s.current > 0 && <span className="badge off">{s.current} zaten güncel</span>}
          {s.reboot > 0 && <span className="badge warn">{s.reboot} yeniden başlatma bekliyor</span>}
          {s.fail > 0 && <span className="badge fail">{s.fail} başarısız</span>}
        </div>
      )}

      {job.guide?.length > 0 && (
        <div className="ins-guide">
          <b>Bundan sonra</b>
          <ul>
            {job.guide.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </div>
      )}

      <details className="ins-log">
        <summary>Ayrıntılı kayıt</summary>
        <pre ref={logRef}>{job.log.join("\n") || "…"}</pre>
      </details>

      <div className="update-foot">
        <span className="muted small">{job.running ? "Windows izin sorarsa 'Evet'e basın." : `Bitiş: ${(job.finished || "").replace("T", " ").slice(0, 16)}`}</span>
        {job.running ? (
          <button className="setup-btn" onClick={onCancel}>
            <Square size={14} /> Durdur
          </button>
        ) : (
          <button className="setup-btn primary" onClick={onBack}>
            Tamam
          </button>
        )}
      </div>
    </div>
  );
}

export default function InstallerPanel({ mode = "install", query = "", autoStart = false, onClose }) {
  const [tab, setTab] = useState(mode);
  const [info, setInfo] = useState(null);
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [catalog, setCatalog] = useState(null);
  const [q, setQ] = useState(query);
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [picked, setPicked] = useState({}); // id → {id, name, note}
  const [ups, setUps] = useState(null);
  const [adaUp, setAdaUp] = useState(null);
  const started = useRef(false);
  const gone = useRef(0);

  const running = job?.running;

  // İlk açılış: durum + varsa süren iş
  useEffect(() => {
    api("/api/installer")
      .then((d) => {
        setInfo(d.info);
        if (d.job?.running) setJob(d.job);
      })
      .catch((e) => setError(e.message));
  }, []);

  // /d update → kendiliğinden başlat
  useEffect(() => {
    if (!info || started.current) return;
    if (autoStart && mode === "update" && info.available && !job?.running) {
      started.current = true;
      startUpdate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info]);

  // Katalog ve arama
  useEffect(() => {
    if (!info?.available || tab !== "install" || catalog) return;
    api("/api/installer/catalog").then(setCatalog).catch((e) => setError(e.message));
  }, [info, tab, catalog]);

  useEffect(() => {
    if (info?.available && query && query.length >= 2) doSearch(query);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info]);

  // Güncelleme sekmesi: liste + ADA sürümü
  useEffect(() => {
    if (!info?.available || tab !== "update" || ups || running) return;
    api("/api/installer/upgrades").then(setUps).catch((e) => setError(e.message));
    api("/api/update").then(setAdaUp).catch(() => {});
  }, [info, tab, ups, running]);

  // Süren işi izle; ADA kendini güncellerken kapanırsa yeniden açılınca sayfayı yenile
  useEffect(() => {
    if (!running && job?.self_update?.state !== "installing") return;
    const id = setInterval(async () => {
      try {
        const r = await fetch("/api/installer/job");
        if (!r.ok) throw new Error();
        const j = await r.json();
        if (gone.current > 0) return location.reload();
        if (j && j.id) setJob(j);
      } catch {
        gone.current += 1;
      }
    }, 1000);
    return () => clearInterval(id);
  }, [running, job?.self_update?.state]);

  const doSearch = async (text) => {
    const t = (text ?? q).trim();
    if (t.length < 2) return setResults(null);
    setSearching(true);
    setError("");
    try {
      setResults(await api("/api/installer/search?q=" + encodeURIComponent(t)));
    } catch (e) {
      setError(e.message);
    } finally {
      setSearching(false);
    }
  };

  const toggle = (p) =>
    setPicked((prev) => {
      const next = { ...prev };
      if (next[p.id]) delete next[p.id];
      else next[p.id] = { id: p.id, name: p.name, note: p.note || "" };
      return next;
    });

  const startInstall = async () => {
    setError("");
    try {
      setJob(await post("/api/installer/install", { items: Object.values(picked) }));
      setPicked({});
    } catch (e) {
      setError(e.message);
    }
  };

  async function startUpdate() {
    setError("");
    setTab("update");
    try {
      setJob(await post("/api/installer/update"));
    } catch (e) {
      setError(e.message);
    }
  }

  const cancel = () => post("/api/installer/cancel").catch((e) => setError(e.message));
  const back = () => {
    setJob(null);
    setCatalog(null);
    setUps(null);
  };

  const groups = useMemo(() => {
    const g = {};
    for (const c of catalog || []) (g[c.cat] ||= []).push(c);
    return g;
  }, [catalog]);
  const pickedList = Object.values(picked);

  return (
    <div className="modal-bg" onClick={running ? undefined : onClose}>
      <div className="modal installer" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <b>Kurulum merkezi</b>
          <span className="muted small ins-cmd">/d · /d &lt;program&gt; · /d update</span>
          {!running && (
            <button className="close" onClick={onClose}>
              ✕
            </button>
          )}
        </div>

        {!info ? (
          <div className="muted">{error || "Hazırlanıyor…"}</div>
        ) : !info.available ? (
          <div className="ins-setup">
            <PackageSearch size={30} />
            <ol>
              {info.guide.map((g, i) => (
                <li key={i}>{g}</li>
              ))}
            </ol>
            {info.store_url && (
              <button className="setup-btn primary" onClick={() => post("/api/installer/open-store").catch((e) => setError(e.message))}>
                <Store size={15} /> Microsoft Store'u aç
              </button>
            )}
            {error && <div className="setup-error">{error}</div>}
          </div>
        ) : job ? (
          <JobView job={job} onCancel={cancel} onBack={back} />
        ) : (
          <>
            <div className="ins-tabs">
              <button className={tab === "install" ? "on" : ""} onClick={() => setTab("install")}>
                <Download size={15} /> Program kur
              </button>
              <button className={tab === "update" ? "on" : ""} onClick={() => setTab("update")}>
                <RefreshCw size={15} /> Her şeyi güncelle
              </button>
            </div>

            {tab === "install" ? (
              <div className="ins-body">
                <form
                  className="ins-search"
                  onSubmit={(e) => {
                    e.preventDefault();
                    doSearch();
                  }}
                >
                  <Search size={16} />
                  <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Program ara (ör. vlc, zoom, python)…" autoFocus />
                  <button className="setup-btn" disabled={searching || q.trim().length < 2}>
                    {searching ? <LoaderCircle size={15} className="spin" /> : "Ara"}
                  </button>
                </form>

                <div className="ins-scroll">
                  {results && (
                    <section>
                      <h4>Arama sonuçları {results.length === 0 && <span className="muted small">· bulunamadı, başka bir ad deneyin</span>}</h4>
                      <div className="ins-grid">
                        {results.map((r) => (
                          <label key={r.id} className={"ins-card" + (picked[r.id] ? " on" : "")}>
                            <input type="checkbox" checked={!!picked[r.id]} onChange={() => toggle(r)} />
                            <span>
                              <b>{r.name}</b>
                              <small data-no-i18n>
                                {r.id} · {r.version}
                              </small>
                            </span>
                          </label>
                        ))}
                      </div>
                    </section>
                  )}
                  {!catalog ? (
                    <div className="muted">Kurulu programlar denetleniyor…</div>
                  ) : (
                    Object.entries(groups).map(([cat, items]) => (
                      <section key={cat}>
                        <h4>{cat}</h4>
                        <div className="ins-grid">
                          {items.map((c) => (
                            <label key={c.id} className={"ins-card" + (picked[c.id] ? " on" : "") + (c.installed ? " have" : "")} title={c.note || c.id}>
                              <input type="checkbox" checked={!!picked[c.id]} onChange={() => toggle(c)} />
                              <span>
                                <b>{c.name}</b>
                                <small>{c.installed ? "Kurulu · seçerseniz güncellenir" : c.note ? "Kurulumdan sonra yönlendirme var" : "Hazır"}</small>
                              </span>
                            </label>
                          ))}
                        </div>
                      </section>
                    ))
                  )}
                </div>

                {pickedList.some((p) => p.note) && (
                  <div className="ins-guide small-guide">
                    <b>Bilmeniz gerekenler</b>
                    <ul>
                      {pickedList
                        .filter((p) => p.note)
                        .map((p) => (
                          <li key={p.id}>
                            {p.name}: {p.note}
                          </li>
                        ))}
                    </ul>
                  </div>
                )}
                {error && <div className="setup-error">{error}</div>}
                <div className="update-foot">
                  <span className="muted small">
                    {pickedList.length ? `${pickedList.length} program seçildi · sessiz kurulur, sırayla` : "Kurmak istediklerinizi işaretleyin"}
                  </span>
                  <button className="setup-btn primary" disabled={!pickedList.length} onClick={startInstall}>
                    <Download size={15} /> Kur
                  </button>
                </div>
              </div>
            ) : (
              <div className="ins-body">
                <div className="ins-scroll">
                  {adaUp && (
                    <div className={"ins-ada" + (adaUp.available ? " new" : "")}>
                      <b>ADA</b> {adaUp.current}
                      {adaUp.available ? ` → ${adaUp.latest} (en son kurulur, ADA yeniden açılır)` : " · güncel"}
                    </div>
                  )}
                  {!ups ? (
                    <div className="muted">
                      <LoaderCircle size={14} className="spin" /> Güncellemeler aranıyor…
                    </div>
                  ) : ups.length === 0 ? (
                    <div className="update-ok">
                      <CheckCircle2 size={18} /> Tüm programlar güncel.
                    </div>
                  ) : (
                    <ul className="ins-items">
                      {ups.map((u) => (
                        <li key={u.id} className="ins-row">
                          <RefreshCw size={14} className="ins-i wait" />
                          <span className="ins-name">{u.name}</span>
                          <span className="ins-msg" data-no-i18n>
                            {u.version} → {u.available}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                {error && <div className="setup-error">{error}</div>}
                <div className="update-foot">
                  <span className="muted small">Açık programlar güncellenemeyebilir; mümkünse kapatın.</span>
                  <button className="setup-btn primary" onClick={startUpdate} disabled={!ups || (ups.length === 0 && !adaUp?.available)}>
                    <RefreshCw size={15} /> Hepsini güncelle
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
