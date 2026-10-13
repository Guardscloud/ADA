import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Check, Hand, KeyRound, Monitor, Power, Shield, ShieldCheck, Zap } from "lucide-react";
import duckImg from "../assets/duck-192.png";
import { api } from "../api";

// Kurulumdan sonraki ilk açılışta gerekli soruları sorar: ad, yapay zekâ anahtarı, özerklik, arka plan.

const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
const IS_WIN = navigator.userAgent.includes("Windows");

const STEPS = ["Tanışma", "Yapay zekâ", "Özerklik", "Arka plan"];
const AUTONOMY = [
  ["smart", ShieldCheck, "Akıllı onay", "Önerilen. Yalnızca riskli işlemlerde (silme, e-posta gönderme, yönetici komutları) size sorar."],
  ["full", Zap, "Tam özerk", "Hiçbir şey sormaz; işi baştan sona kendi yapar. Yalnızca felaket komutları engellenir."],
  ["ask", Hand, "Her işlemde sor", "Dosya yazma, komut çalıştırma gibi yan etkisi olan her adımda onayınızı bekler."],
];

function Toggle({ icon: Icon, title, text, checked, onChange, disabled }) {
  return (
    <label className={"setup-toggle" + (disabled ? " disabled" : "")}>
      <span className="setup-ic">
        <Icon size={18} />
      </span>
      <span className="setup-toggle-text">
        <b>{title}</b>
        <small>{text}</small>
      </span>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <i className="switch" />
    </label>
  );
}

export default function SetupWizard({ onDone }) {
  const [step, setStep] = useState(0);
  const [form, setForm] = useState(null);
  const [ai, setAi] = useState(null);
  const [provider, setProvider] = useState("gemini");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api("/api/setup")
      .then((s) => setForm({ name: s.name, autonomy: s.autonomy, watch: s.watch, computer: s.computer, autostart: s.autostart.supported, autostartSupported: s.autostart.supported }))
      .catch((e) => setError(e.message));
    // Bu bilgisayardaki yerel modeller de taransın: anahtar girmeden çalışabilecek motor var mı?
    post("/api/ai/scan", { target: "local" })
      .then(setAi)
      .catch(() => api("/api/ai").then(setAi).catch(() => {}));
  }, []);

  if (!form) return <div className="setup-bg">{error && <div className="setup-card">{error}</div>}</div>;

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const clouds = (ai?.cloud || []).filter((c) => !c.passive);
  const cloud = clouds.find((c) => c.id === provider);
  const ready = (ai?.engines || []).filter((e) => e.enabled && e.available);
  const localReady = ready.filter((e) => e.local);

  const saveKey = async () => {
    setBusy("key");
    setError("");
    try {
      const r = await post("/api/ai/key", { provider, key: key.trim() });
      setAi(r);
      setKey("");
      const st = r.cloud.find((c) => c.id === provider)?.status;
      if (st?.state === "error") setError(`${st.detail || "Anahtar doğrulanamadı"}. Anahtarı kontrol edip yeniden deneyin.`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  };

  const finish = async () => {
    setBusy("finish");
    setError("");
    try {
      const r = await post("/api/setup", { name: form.name, autonomy: form.autonomy, autostart: form.autostart, watch: form.watch, computer: form.computer });
      if (!r.restart_required) return onDone();
      await post("/api/restart");
      // Ayarlar yeniden başlatınca geçerli olur: sunucu açılana kadar bekle, sonra sayfayı yenile
      for (let i = 0; i < 60; i++) {
        await new Promise((res) => setTimeout(res, 1000));
        try {
          const s = await fetch("/api/status");
          if (s.ok || s.status === 401) return location.reload();
        } catch {
          /* henüz açılmadı */
        }
      }
      setError("ADA yeniden açılamadı; uygulamayı elle başlatın.");
    } catch (e) {
      setError(e.message);
    }
    setBusy("");
  };

  const next = () => {
    setError("");
    setStep((s) => s + 1);
  };
  const last = step === STEPS.length - 1;

  return (
    <div className="setup-bg">
      <div className="setup-card">
        <div className="setup-steps">
          {STEPS.map((s, i) => (
            <div key={s} className={"setup-step" + (i === step ? " on" : i < step ? " done" : "")}>
              <i>{i < step ? <Check size={12} strokeWidth={3} /> : i + 1}</i>
              <span>{s}</span>
            </div>
          ))}
        </div>

        <div className="setup-body">
          {step === 0 && (
            <>
              <img src={duckImg} alt="" className="setup-logo" />
              <h1>ADA'ya hoş geldiniz</h1>
              <p>Kurulum tamamlandı. Başlamadan önce birkaç kısa soru soracağım; hepsini sonradan Ayarlar'dan değiştirebilirsiniz.</p>
              <label className="setup-field">
                <span>Size nasıl hitap edeyim?</span>
                <input
                  autoFocus
                  value={form.name}
                  maxLength={60}
                  placeholder="Adınız (isteğe bağlı)"
                  onChange={(e) => set("name", e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && next()}
                />
              </label>
            </>
          )}

          {step === 1 && (
            <>
              <h1>Hangi yapay zekâyı kullanalım?</h1>
              <p>
                ADA işleri bir yapay zekâ modeliyle yapar. Ücretsiz başlamak için Google Gemini anahtarı yeterli; isterseniz başka sağlayıcıları da
                sonradan ekleyebilirsiniz.
              </p>
              <div className="setup-key">
                <select value={provider} onChange={(e) => (setProvider(e.target.value), setError(""))}>
                  {clouds.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.label}
                      {c.note ? ` — ${c.note}` : ""}
                    </option>
                  ))}
                  {!clouds.length && <option value="gemini">Google Gemini</option>}
                </select>
                <div className="setup-key-row">
                  <KeyRound size={16} />
                  <input
                    type="password"
                    autoComplete="off"
                    value={key}
                    placeholder={cloud?.key_set ? `kayıtlı (${cloud.key_mask}) — değiştirmek için yazın` : "API anahtarını yapıştırın"}
                    onChange={(e) => setKey(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && key.trim() && saveKey()}
                  />
                  <button className="setup-btn" disabled={!key.trim() || !!busy} onClick={saveKey}>
                    {busy === "key" ? "Doğrulanıyor…" : "Kaydet"}
                  </button>
                </div>
                {cloud?.key_url && (
                  <a href={cloud.key_url} target="_blank" rel="noreferrer">
                    {cloud.label} anahtarı nasıl alınır? ↗
                  </a>
                )}
              </div>
              <div className={"setup-ready" + (ready.length ? " ok" : "")}>
                {ready.length ? (
                  <>
                    <Check size={16} strokeWidth={3} />
                    <span>
                      <b>{ready.length} model hazır.</b> {localReady.length > 0 && `Bu bilgisayarda ${localReady.length} yerel model bulundu. `}
                      {ready.slice(0, 3).map((e) => e.label).join(", ")}
                      {ready.length > 3 && "…"}
                    </span>
                  </>
                ) : (
                  <span>Henüz kullanılabilir model yok. Anahtar girmeden de devam edebilirsiniz; ADA görev istediğinizde anahtarı hatırlatır.</span>
                )}
              </div>
            </>
          )}

          {step === 2 && (
            <>
              <h1>ADA ne kadar bağımsız çalışsın?</h1>
              <p>ADA dosya yazar, komut çalıştırır, uygulamaları kullanır. Ne zaman size sorması gerektiğini seçin.</p>
              <div className="setup-options">
                {AUTONOMY.map(([id, Icon, title, text]) => (
                  <button key={id} className={form.autonomy === id ? "on" : ""} onClick={() => set("autonomy", id)}>
                    <span className="setup-ic">
                      <Icon size={18} />
                    </span>
                    <span>
                      <b>{title}</b>
                      <small>{text}</small>
                    </span>
                    <i className="radio" />
                  </button>
                ))}
              </div>
            </>
          )}

          {step === 3 && (
            <>
              <h1>Arka planda neler çalışsın?</h1>
              <p>Rutinlerin her gün zamanında yapılması ve bilgisayarın denetlenmesi için ADA'nın açık kalması gerekir.</p>
              <div className="setup-options">
                <Toggle
                  icon={Power}
                  title={IS_WIN ? "Windows açılınca ADA'yı başlat" : "Bilgisayar açılınca ADA'yı başlat"}
                  text={form.autostartSupported ? "Arka planda, küçültülmüş olarak açılır." : "Yalnızca kurulu masaüstü uygulamasında kullanılabilir."}
                  checked={form.autostart}
                  disabled={!form.autostartSupported}
                  onChange={(v) => set("autostart", v)}
                />
                <Toggle
                  icon={Shield}
                  title="Gözcü: bilgisayarı sürekli denetle"
                  text="Disk, bellek, işlemci, internet ve başarısız rutinleri izler; sorun olursa haber verir."
                  checked={form.watch}
                  onChange={(v) => set("watch", v)}
                />
                <Toggle
                  icon={Monitor}
                  title="Ekran kontrolüne izin ver"
                  text="ADA gerektiğinde ekranı görüp fare ve klavyeyi kullanabilir."
                  checked={form.computer}
                  onChange={(v) => set("computer", v)}
                />
              </div>
            </>
          )}
          {error && <div className="setup-error">{error}</div>}
        </div>

        <div className="setup-foot">
          {step > 0 && (
            <button className="setup-btn ghost" disabled={!!busy} onClick={() => (setError(""), setStep((s) => s - 1))}>
              <ArrowLeft size={15} /> Geri
            </button>
          )}
          <span className="setup-count">
            {step + 1} / {STEPS.length}
          </span>
          {last ? (
            <button className="setup-btn primary" disabled={!!busy} onClick={finish}>
              {busy === "finish" ? "Hazırlanıyor…" : "Bitir ve başla"} <Check size={15} strokeWidth={3} />
            </button>
          ) : (
            <button className="setup-btn primary" disabled={!!busy} onClick={next}>
              {step === 1 && !ready.length ? "Şimdilik geç" : "Devam"} <ArrowRight size={15} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
