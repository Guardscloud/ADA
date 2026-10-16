import { useEffect, useState } from "react";
import { api } from "../api";

// Ayarlar → Yapay zekâ: ADA-AGI üst katmanı, yerel modeller, isteğe bağlı bulut anahtarları ve yönlendirme

const LOCAL_STATE = {
  running: ["çalışıyor", "ok"],
  installed: ["kurulu, kapalı", "waiting"],
  down: ["ulaşılamıyor", "failed"],
};
const CLOUD_STATE = {
  ok: ["bağlı", "ok"],
  error: ["hata", "failed"],
  offline: ["çevrimdışı", "waiting"],
};
// Claude Code: bu bilgisayarda kurulu `claude` komutu; anahtar yerine açık oturum (abonelik) kullanılır
const CLAUDE_CODE_STATE = {
  ok: ["bağlı", "ok"],
  nologin: ["oturum açılmamış", "waiting"],
  missing: ["kurulu değil", "off"],
};
// Bu bilgisayardaki kodlama ajanları: düşünen onlar, araçları çalıştıran ADA
const AGENTS = [
  {
    key: "claude_code",
    provider: "claudecode",
    name: "Claude Code",
    label: "Claude Code (abonelik)",
    note: "API anahtarı gerekmez; bu bilgisayardaki Claude Code oturumu kullanılır",
    install: "Kurmak için terminalde: npm install -g @anthropic-ai/claude-code — ardından claude yazıp /login ile oturum açın.",
  },
  {
    key: "opencode",
    provider: "opencode",
    name: "OpenCode",
    label: "OpenCode (CLI)",
    note: "Ücretsiz modeller ve OpenCode'da bağlı sağlayıcılar (Zen, Copilot, ChatGPT aboneliği…) anahtar girmeden kullanılır",
    warn:
      "Ücretsiz modeller yalnızca OpenCode kendi araçlarıyla (bash, dosya düzenleme) çalışırken kullanılabilir. Bu araçlar çalışma klasörünün dışına çıkamaz ve her işlem günlüğe yazılır, ama ADA'nın onay ve yedek adımından geçmez. Bu yüzden ücretsiz modeller kapalı gelir; kutucuğu işaretleyerek açabilirsiniz.",
    states: { nologin: "sağlayıcı bağlı değil" },
    install: "Kurmak için terminalde: npm install -g opencode-ai — ardından opencode auth login ile bir sağlayıcı bağlayın.",
  },
];

const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });

function minutes(s) {
  return s >= 3600 ? `${Math.round(s / 3600)} sa` : `${Math.max(1, Math.round(s / 60))} dk`;
}

function EngineRow({ e, onToggle, onTest, test }) {
  const st = e.stats || {};
  return (
    <div className={"ai-engine" + (e.enabled && !e.passive ? "" : " off")}>
      <input
        type="checkbox"
        checked={e.enabled && !e.passive}
        disabled={e.passive}
        onChange={(ev) => onToggle(e.id, ev.target.checked)}
        title={e.passive ? "Şimdilik pasif" : "Bu motoru kullan"}
      />
      <div className="ai-engine-main">
        <div className="ai-engine-name">
          <b>{e.label}</b>
          {e.passive && <span className="badge off">pasif · sonra eklenecek</span>}
          {!e.available && !e.passive && <span className="badge off">kullanılamıyor</span>}
          {e.cooldown && <span className="badge paused">dinleniyor {minutes(e.cooldown.seconds)}</span>}
        </div>
        <div className="ai-tags">
          <span title="Tahmini kalite">{"★".repeat(e.tier)}{"☆".repeat(5 - e.tier)}</span>
          {e.local ? <span>yerel</span> : <span>bulut</span>}
          {e.params_b > 0 && <span>{e.params_b}B</span>}
          {e.vision && <span>görsel</span>}
          {!e.tools && <span className="warn">araç çağıramaz</span>}
          {e.loaded && <span>bellekte</span>}
          {e.cost === 0 && !e.local && <span>ücretsiz/ucuz</span>}
          {e.specialty?.map((s) => <span key={s}>{s}</span>)}
          {(st.ok || st.fail) && (
            <span title={st.last_error || ""}>
              {st.ok || 0} başarılı · {st.fail || 0} hata
            </span>
          )}
        </div>
        {test && <div className={"ai-test " + (test.ok ? "ok-text" : "warn")}>{test.busy ? "Deneniyor…" : test.ok ? `✓ ${test.answer} (${test.seconds} sn)` : `✗ ${test.error}`}</div>}
      </div>
      <button className="ai-btn" onClick={() => onTest(e.id)} disabled={test?.busy || e.passive}>
        Dene
      </button>
    </div>
  );
}

export default function AIPanel({ legacy, onRestart }) {
  const [ai, setAi] = useState(null);
  const [busy, setBusy] = useState("");
  const [keys, setKeys] = useState({});
  const [tests, setTests] = useState({});
  const [open, setOpen] = useState({});
  const [custom, setCustom] = useState({ label: "", base_url: "", key: "" });
  const [modeSaved, setModeSaved] = useState(false);

  const run = async (label, fn) => {
    setBusy(label);
    try {
      setAi(await fn());
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy("");
    }
  };

  useEffect(() => {
    // Önce kayıtlı durum gösterilir, ardından bu bilgisayardaki modeller yeniden taranır
    api("/api/ai")
      .then((d) => {
        setAi(d);
        run("local", () => post("/api/ai/scan", { target: "local" }));
      })
      .catch((e) => alert(e.message));
  }, []);

  if (!ai) return <div className="muted">Yükleniyor…</div>;

  const toggle = (id, enabled) => run("", () => post("/api/ai/engine", { id, enabled }));
  const testEngine = async (id) => {
    setTests((t) => ({ ...t, [id]: { busy: true } }));
    try {
      const r = await post("/api/ai/test", { id });
      setTests((t) => ({ ...t, [id]: r }));
    } catch (e) {
      setTests((t) => ({ ...t, [id]: { ok: false, error: e.message } }));
    }
  };
  const saveKey = (provider, key) =>
    run(provider, async () => {
      const r = await post("/api/ai/key", { provider, key });
      setKeys((k) => ({ ...k, [provider]: "" }));
      setOpen((o) => ({ ...o, [provider]: true }));
      return r;
    });
  const enableMode = async (provider) => {
    try {
      await post("/api/ai/mode", { provider });
      setModeSaved(true);
    } catch (e) {
      alert(e.message);
    }
  };

  const localEngines = ai.engines.filter((e) => e.local);
  const enabledCount = ai.engines.filter((e) => e.enabled && e.available).length;
  const foundServers = ai.local.filter((s) => s.state && !["missing", "unknown"].includes(s.state));
  const missing = ai.local.filter((s) => s.state === "missing").map((s) => s.label);
  const choices = ai.engines.filter((e) => e.enabled && e.available);

  return (
    <div className="ai-panel">
      <div className={"ai-hero" + (ai.active ? " on" : "")}>
        <div>
          <b>ADA-AGI üst katmanı</b>
          <p>
            Her işi türüne göre en uygun modele verir: kod işi kod modeline, ekran işi görsel okuyan modele, derin düşünme en güçlü modele.
            Bir model kotaya takılır ya da kapanırsa iş kaldığı yerden sıradakiyle sürer. Geçmiş tek ortak biçimde tutulur; aileler birbirine
            karışmaz. Gizli işler yalnız yerel modelle yapılır.
          </p>
          {ai.active ? (
            <span className="ok-text">
              Etkin · {enabledCount} motor hazır{ai.scanned && ` · son tarama ${ai.scanned.replace("T", " ").slice(0, 16)}`}
            </span>
          ) : modeSaved ? (
            <span className="ok-text">
              Kaydedildi. <button className="ai-link" onClick={onRestart}>Şimdi yeniden başlat</button>
            </span>
          ) : (
            <span className="warn">Şu an yalnızca “{ai.provider}” kullanılıyor.</span>
          )}
        </div>
        {!ai.active && !modeSaved && (
          <button className="ai-btn primary" onClick={() => enableMode("adaagi")}>
            ADA-AGI'yi aç
          </button>
        )}
      </div>

      <section>
        <div className="ai-head">
          <h4>1 · Bu bilgisayardaki modeller</h4>
          <button className="ai-btn" disabled={!!busy} onClick={() => run("local", () => post("/api/ai/scan", { target: "local" }))}>
            {busy === "local" ? "Taranıyor…" : "Yeniden tara"}
          </button>
        </div>
        <p className="ai-note">Ollama, LM Studio, llama.cpp, Jan, vLLM ve benzerleri otomatik aranır. Yeni model kurduğunuzda ADA kendiliğinden fark eder.</p>
        {foundServers.map((s) => (
          <div key={s.id} className="ai-server">
            <b>{s.label}</b>
            <span className={"badge " + (LOCAL_STATE[s.state]?.[1] || "")}>{LOCAL_STATE[s.state]?.[0] || s.state}</span>
            <small>{s.detail}</small>
            {s.id === "ollama" && s.state === "installed" && (
              <button className="ai-btn" disabled={!!busy} onClick={() => run("ollama", () => post("/api/ai/ollama/start"))}>
                {busy === "ollama" ? "Başlatılıyor…" : "Başlat"}
              </button>
            )}
          </div>
        ))}
        {!foundServers.length && <div className="muted small">Bu bilgisayarda yerel model sunucusu bulunamadı. Ücretsiz seçenek: ollama.com</div>}
        {localEngines.map((e) => (
          <EngineRow key={e.id} e={e} onToggle={toggle} onTest={testEngine} test={tests[e.id]} />
        ))}
        {missing.length > 0 && <div className="ai-note">Bulunamayanlar: {missing.join(", ")}</div>}
      </section>

      <section>
        <div className="ai-head">
          <h4>2 · Bulut modelleri (isteğe bağlı API anahtarı)</h4>
          <button className="ai-btn" disabled={!!busy} onClick={() => run("all", () => post("/api/ai/scan", { target: "all" }))}>
            {busy === "all" ? "Yenileniyor…" : "Listeleri yenile"}
          </button>
        </div>
        <p className="ai-note">Anahtar girdiğiniz sağlayıcının modelleri listelenir ve en iyileri açılır. Yeniden başlatma gerekmez.</p>
        {AGENTS.map((a) => {
          const st = ai[a.key];
          const rows = ai.engines.filter((e) => e.provider === a.provider);
          if (!CLAUDE_CODE_STATE[st?.state]) return null;
          return (
            <div key={a.key} className={"ai-card" + (st.state === "ok" ? " set" : "")}>
              <div className="ai-card-head" onClick={() => setOpen((o) => ({ ...o, ["_" + a.key]: !o["_" + a.key] }))}>
                <b>{a.label}</b>
                <small>{a.note}</small>
                <span className={"badge " + CLAUDE_CODE_STATE[st.state][1]}>{a.states?.[st.state] || CLAUDE_CODE_STATE[st.state][0]}</span>
                {st.state === "ok" && (
                  <span className="ai-count">
                    {rows.filter((e) => e.enabled).length}/{rows.length} model açık
                  </span>
                )}
                <span className="ai-caret">{open["_" + a.key] ? "▾" : "▸"}</span>
              </div>
              {open["_" + a.key] && (
                <div className="ai-card-body">
                  {st.detail && <div className="ai-note">{st.detail}</div>}
                  {st.state === "missing" && <div className="ai-note">{a.install}</div>}
                  <div className="ai-note">
                    Düşünen {a.name}'dur; komutları, dosyaları ve ekranı yine ADA'nın araçları yönetir (onaylar ve işlem günlüğü aynen geçerlidir).
                  </div>
                  {a.warn && <div className="ai-note warn">{a.warn}</div>}
                  {rows.map((e) => (
                    <EngineRow key={e.id} e={e} onToggle={toggle} onTest={testEngine} test={tests[e.id]} />
                  ))}
                </div>
              )}
            </div>
          );
        })}
        {ai.cloud.map((c) => {
          const rows = ai.engines.filter((e) => e.provider === c.id);
          const st = CLOUD_STATE[c.status?.state];
          const expanded = open[c.id];
          if (c.passive) {
            return (
              <div key={c.id} className="ai-card passive">
                <div className="ai-card-head">
                  <b>{c.label}</b>
                  <span className="badge off">pasif · sonra eklenecek</span>
                </div>
              </div>
            );
          }
          return (
            <div key={c.id} className={"ai-card" + (c.key_set ? " set" : "")}>
              <div className="ai-card-head" onClick={() => setOpen((o) => ({ ...o, [c.id]: !o[c.id] }))}>
                <b>{c.label}</b>
                {c.note && <small>{c.note}</small>}
                {c.key_set && st && <span className={"badge " + st[1]} title={c.status.detail}>{st[0]}</span>}
                {c.key_set && <span className="ai-count">{rows.filter((e) => e.enabled).length}/{rows.length} model açık</span>}
                <span className="ai-caret">{expanded ? "▾" : "▸"}</span>
              </div>
              {expanded && (
                <div className="ai-card-body">
                  <div className="ai-key">
                    <input
                      type="password"
                      autoComplete="off"
                      value={keys[c.id] || ""}
                      placeholder={c.key_set ? `kayıtlı (${c.key_mask}) — değiştirmek için yazın` : "API anahtarını yapıştırın"}
                      onChange={(ev) => setKeys((k) => ({ ...k, [c.id]: ev.target.value }))}
                    />
                    <button className="ai-btn primary" disabled={!keys[c.id] || !!busy} onClick={() => saveKey(c.id, keys[c.id])}>
                      {busy === c.id ? "Doğrulanıyor…" : "Kaydet"}
                    </button>
                    {c.key_set && (
                      <button className="ai-btn danger" disabled={!!busy} onClick={() => confirm(`${c.label} anahtarı kaldırılsın mı?`) && saveKey(c.id, "")}>
                        Kaldır
                      </button>
                    )}
                    <a href={c.key_url} target="_blank" rel="noreferrer">
                      Anahtar al ↗
                    </a>
                  </div>
                  {c.status?.detail && <div className="ai-note">{c.status.detail}</div>}
                  {rows.map((e) => (
                    <EngineRow key={e.id} e={e} onToggle={toggle} onTest={testEngine} test={tests[e.id]} />
                  ))}
                </div>
              )}
            </div>
          );
        })}

        <div className="ai-card">
          <div className="ai-card-head" onClick={() => setOpen((o) => ({ ...o, _custom: !o._custom }))}>
            <b>Özel sunucu (OpenAI uyumlu)</b>
            <small>Başka bilgisayardaki vLLM/Ollama, kurumsal uç nokta…</small>
            <span className="ai-caret">{open._custom ? "▾" : "▸"}</span>
          </div>
          {open._custom && (
            <div className="ai-card-body">
              {ai.custom.map((c) => (
                <div key={c.id} className="ai-server">
                  <b>{c.label}</b>
                  <small>{c.base_url}</small>
                  {c.state && <span className={"badge " + (LOCAL_STATE[c.state]?.[1] || "")}>{LOCAL_STATE[c.state]?.[0] || c.state}</span>}
                  <button className="ai-btn danger" onClick={() => run("", () => post("/api/ai/custom/remove", { id: c.id }))}>
                    Kaldır
                  </button>
                </div>
              ))}
              {ai.engines
                .filter((e) => ai.custom.some((c) => c.id === e.provider))
                .map((e) => (
                  <EngineRow key={e.id} e={e} onToggle={toggle} onTest={testEngine} test={tests[e.id]} />
                ))}
              <div className="ai-key">
                <input placeholder="Ad" value={custom.label} onChange={(ev) => setCustom({ ...custom, label: ev.target.value })} />
                <input placeholder="http://192.168.1.20:8000/v1" value={custom.base_url} onChange={(ev) => setCustom({ ...custom, base_url: ev.target.value })} />
                <input type="password" placeholder="Anahtar (varsa)" value={custom.key} onChange={(ev) => setCustom({ ...custom, key: ev.target.value })} />
                <button
                  className="ai-btn primary"
                  disabled={!custom.base_url || !!busy}
                  onClick={() =>
                    run("custom", async () => {
                      const r = await post("/api/ai/custom", custom);
                      setCustom({ label: "", base_url: "", key: "" });
                      return r;
                    })
                  }
                >
                  Ekle
                </button>
              </div>
            </div>
          )}
        </div>
      </section>

      <section>
        <div className="ai-head">
          <h4>3 · İş dağıtımı (ADA-AGI)</h4>
        </div>
        <p className="ai-note">
          Varsayılan “Otomatik”: ADA kalite, hız, maliyet, uzmanlık ve geçmiş başarıya göre seçer. Bir türü belirli bir modele sabitleyebilirsiniz;
          o model yapamazsa yine sıradakine geçilir. Alt ajanlar: Araştırmacı → araştırma, Analist → kod, Yazar → ofis, Denetçi → derin, Operatör → ekran.
        </p>
        <div className="ai-routes">
          {Object.entries(ai.kinds).map(([kind, label]) => (
            <div key={kind} className="ai-route">
              <div className="ai-route-label">{label}</div>
              <div className="ai-chain">
                {(ai.preview[kind] || []).map((e, i) => (
                  <span key={e.id} className={i === 0 ? "first" : ""}>
                    {e.label}
                  </span>
                ))}
                {!(ai.preview[kind] || []).length && <span className="warn">uygun motor yok</span>}
              </div>
              <select value={ai.routes[kind] || ""} onChange={(ev) => run("", () => post("/api/ai/route", { kind, engine: ev.target.value }))}>
                <option value="">Otomatik</option>
                {choices
                  .filter((e) => kind !== "gizli" || e.local)
                  .map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.label}
                    </option>
                  ))}
              </select>
            </div>
          ))}
        </div>
      </section>

      {ai.decisions.length > 0 && (
        <section>
          <div className="ai-head">
            <h4>Son kararlar</h4>
          </div>
          <div className="ai-decisions">
            {ai.decisions.map((d) => (
              <div key={d.at + d.text} title={d.chain?.join(" → ")}>
                <span className="muted">{d.at.slice(11, 16)}</span> <b>{ai.kinds[d.kind] || d.kind}</b> → {d.engine}{" "}
                <span className={d.outcome?.startsWith("tamam") ? "ok-text" : d.outcome === "çalışıyor" ? "" : "warn"}>{d.outcome}</span>
                <small> “{d.text}”</small>
              </div>
            ))}
          </div>
        </section>
      )}

      {legacy}
    </div>
  );
}
