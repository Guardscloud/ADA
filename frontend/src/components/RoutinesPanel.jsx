import { useEffect, useRef, useState } from "react";
import { api, fileUrl, uploadTo } from "../api";

const DAYS = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]; // 0 = Pazartesi (sunucuyla aynı)
const RUN_STATUS = { running: "çalışıyor", ok: "tamamlandı", error: "hata", cancelled: "iptal edildi" };
const RUN_BADGE = { running: "running", ok: "ok", error: "error", cancelled: "off" };
const HINT =
  "Bir .md dosyası yükleyin; ADA içindeki işi her gün belirtilen saatte kendi başına yapar, raporunu saklar ve her çalışmada öğrendiklerini bir sonrakine taşır.";
const DISCARD = "Kaydedilmemiş değişiklikler kaybolacak. Devam edilsin mi?";

// Sunucudan şablon alınamazsa kullanılan örnek rutin dosyası
const FALLBACK_TEMPLATE = `---
ad: Günlük haber özeti
saat: 09:00
gunler: her gün
aktif: evet
bildirim: evet
---
# Günlük haber özeti

Her sabah Türkiye ve dünya gündemindeki önemli haberleri tara.
- Ekonomi, teknoloji ve sektörümü ilgilendiren gelişmelere öncelik ver.
- Her haberi 2-3 cümleyle özetle, kaynağını yaz.
- Sonucu kısa bir Word raporu olarak kaydet.
`;

/** "2026-10-03T09:00" → "bugün 09:00" · "yarın 09:00" · "dün 09:00" · "03.10 09:00" (başka yıl: "03.10.2025 09:00") */
export function fmtWhen(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(iso || "");
  if (!m) return iso || "";
  const [, y, mo, d, h, mi] = m;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((new Date(+y, +mo - 1, +d) - today) / 86400000);
  const hm = `${h}:${mi}`;
  if (diff === 0) return "bugün " + hm;
  if (diff === 1) return "yarın " + hm;
  if (diff === -1) return "dün " + hm;
  return `${d}.${mo}${+y === today.getFullYear() ? "" : "." + y} ${hm}`;
}

/** Bir sonraki çalışma zamanı; rutin kendiliğinden çalışmayacaksa boş */
export function nextRun(r) {
  return r.enabled && !r.invalid && r.next_run ? fmtWhen(r.next_run) : "";
}

function badgeOf(r) {
  if (r.invalid) return ["invalid", "geçersiz", r.invalid];
  if (r.running) return ["running", "çalışıyor", "Şu anda çalışıyor"];
  if (!r.enabled) return ["off", "kapalı", "Kendiliğinden çalışmaz (elle çalıştırılabilir)"];
  if (r.last_status === "error") return ["error", "hata", "Son çalışma hatayla bitti"];
  if (r.last_status === "ok") return ["ok", "✓", "Son çalışma başarılı"];
  if (r.last_status === "cancelled") return ["off", "iptal", "Son çalışma iptal edildi"];
  return null;
}

export function RoutineBadge({ r }) {
  const b = badgeOf(r);
  if (!b) return null;
  return (
    <span className={"badge " + b[0]} title={b[2]}>
      {b[1]}
    </span>
  );
}

function fmtDur(s) {
  s = Math.round(s);
  if (s < 60) return `${s} sn`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${m} dk ${s % 60} sn` : `${m} dk`;
  return m % 60 ? `${Math.floor(m / 60)} sa ${m % 60} dk` : `${Math.floor(m / 60)} sa`;
}

function daysHint(days) {
  const k = days.join(",");
  if (!days.length || days.length === 7) return "Her gün";
  if (k === "0,1,2,3,4") return "Hafta içi";
  if (k === "5,6") return "Hafta sonu";
  return "";
}

// Zamanlama düzenleyicisinin değerleri (sunucudaki rutinden)
const formOf = (d) => ({
  enabled: !!d.enabled,
  time: (d.spec?.times || []).join(", "),
  weekdays: [...(d.spec?.weekdays || [])].sort((a, b) => a - b),
  notify: !!d.notify,
});

const baseName = (p) => p.split("/").pop();

/** Uzun metni iki satırda keser; tıklayınca tamamı açılır */
function Clamp({ text, className = "" }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={"rt-clamp " + className + (open ? " open" : "")} title={open ? "" : "Tamamını görmek için tıklayın"} onClick={() => setOpen(!open)}>
      {text}
    </div>
  );
}

function RunRow({ run, onOpenConv }) {
  const files = (run.files || []).filter((f) => f !== run.report);
  return (
    <div className="rt-run">
      <div className="rt-run-top">
        <span className="rt-when" title={run.planned ? "Planlanan: " + fmtWhen(run.planned) : ""}>
          {fmtWhen(run.started || run.planned)}
        </span>
        <span className={"badge " + (RUN_BADGE[run.status] || "off")}>{RUN_STATUS[run.status] || run.status}</span>
        {run.late && (
          <span className="rt-late" title={`Planlanan: ${fmtWhen(run.planned)} — bilgisayar kapalı olduğu için sonradan çalıştı`}>
            gecikmeli
          </span>
        )}
        {run.retry_scheduled && (
          <span className="rt-late" title="İlk hatadan sonra ADA işi 15 dakika içinde bir kez yeniden dener">
            yeniden deneme planlandı
          </span>
        )}
        {run.duration_s != null && <span className="rt-dur">{fmtDur(run.duration_s)}</span>}
      </div>
      {run.summary && <Clamp text={run.summary} />}
      {run.error && <div className="warn rt-run-err">{run.error}</div>}
      {(run.report || files.length > 0 || run.conv_id) && (
        <div className="rt-links">
          {run.report && (
            <a className="rt-link report" href={fileUrl(run.report)} download title={run.report}>
              Rapor
            </a>
          )}
          {files.map((f) => {
            const name = baseName(f);
            return (
              <a key={f} className="rt-link" href={fileUrl(f)} download title={f}>
                <span className="ext">{name.includes(".") ? name.split(".").pop() : "dosya"}</span>
                <span className="nm">{name}</span>
              </a>
            );
          })}
          {run.conv_id && (
            <button className="linkbtn" onClick={() => onOpenConv(run.conv_id)}>
              Konuşma
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function RoutinesPanel({ focus, onClose, onOpenConv, onChanged, halted }) {
  const [list, setList] = useState(null); // null: yükleniyor
  const [sel, setSel] = useState(null);
  const [detail, setDetail] = useState(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState(null); // null: zamanlamaya dokunulmadı (değerler detail'den gelir)
  const [text, setText] = useState(null); // null: dosya düzenleyicisi kapalı
  const [template, setTemplate] = useState("");
  const [newName, setNewName] = useState("");
  const [newText, setNewText] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [drag, setDrag] = useState(false);
  const fileInput = useRef(null);
  const dragDepth = useRef(0);
  const msgTimer = useRef(null);
  // Arka plandaki yenilemeler güncel seçimi ve en son isteği bilsin
  const selRef = useRef(null);
  const creatingRef = useRef(false);
  const listSeq = useRef(0);
  const detailSeq = useRef(0);
  const listBox = useRef(null);
  const scrollTo = useRef(null); // seçilen rutin listede görünmüyorsa (yeni yüklenen vb.) kaydırılır
  selRef.current = sel;
  creatingRef.current = creating;

  const loadDetail = (id) => {
    const seq = ++detailSeq.current;
    return api("/api/routines/" + id)
      .then((d) => {
        if (seq === detailSeq.current && selRef.current === id) setDetail(d);
      })
      .catch(() => {});
  };

  const choose = (id) => {
    setCreating(false);
    creatingRef.current = false;
    setForm(null);
    setText(null);
    if (id !== selRef.current) setDetail(null);
    selRef.current = id;
    scrollTo.current = id;
    setSel(id);
    if (id) loadDetail(id);
  };

  const loadList = () => {
    const seq = ++listSeq.current;
    return api("/api/routines")
      .then((items) => {
        if (seq !== listSeq.current) return;
        setList(items);
        const cur = selRef.current;
        if (cur && !items.some((r) => r.id === cur)) choose(null); // başka yerden silinmiş
        if (!selRef.current && !creatingRef.current && items.length) choose(items[0].id);
      })
      .catch(() => setList((prev) => prev || []));
  };

  const startNew = () => {
    setCreating(true);
    creatingRef.current = true;
    setForm(null);
    setText(null);
    setNewName("");
    setNewText(template);
  };

  useEffect(() => {
    loadList();
    api("/api/routine-template")
      .then((t) => setTemplate(t.content || FALLBACK_TEMPLATE))
      .catch(() => setTemplate(FALLBACK_TEMPLATE));
    const timer = setInterval(() => {
      if (document.hidden) return;
      loadList();
      if (selRef.current) loadDetail(selRef.current);
    }, 5000);
    return () => {
      clearInterval(timer);
      clearTimeout(msgTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (focus === "new") startNew();
    else if (focus) choose(focus);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus]);

  // Şablon "yeni rutin" açıldıktan sonra gelirse boş düzenleyiciyi doldur
  useEffect(() => {
    if (template) setNewText((t) => t || template);
  }, [template]);

  // Yeni seçilen rutin liste yenilenince görünür olsun (bir kez; sonraki yenilemeler kaydırmaz)
  useEffect(() => {
    if (!scrollTo.current || !listBox.current) return;
    const el = listBox.current.querySelector(`[data-id="${CSS.escape(scrollTo.current)}"]`);
    if (el) {
      el.scrollIntoView({ block: "nearest" });
      scrollTo.current = null;
    }
  });

  const d = detail;
  const base = d && formOf(d);
  const shown = form || base;
  const dirty = !!(form && base && JSON.stringify(form) !== JSON.stringify(base));
  const textDirty = text !== null && text !== (d?.content ?? "");
  const unsaved = dirty || textDirty || (creating && newText.trim() !== "" && newText !== template);
  const discardOk = () => !unsaved || confirm(DISCARD);

  const close = () => {
    if (discardOk()) onClose();
  };

  const select = (id) => {
    if ((id === sel && !creating) || !discardOk()) return;
    choose(id);
  };

  const openConv = (id) => {
    if (discardOk()) onOpenConv(id);
  };

  const flash = (m) => {
    setMsg(m);
    clearTimeout(msgTimer.current);
    msgTimer.current = setTimeout(() => setMsg(""), 4000);
  };

  const changed = () => {
    loadList();
    onChanged?.();
  };

  const uploadFiles = async (files) => {
    if (!files.length || !discardOk()) return;
    let added = null;
    for (const f of files) {
      if (!/\.(md|markdown|txt)$/i.test(f.name)) {
        alert(`"${f.name}" bir Markdown (.md) dosyası değil.`);
        continue;
      }
      try {
        added = await uploadTo("/api/routines/upload", f);
      } catch (e) {
        alert(e.message);
      }
    }
    if (added) {
      choose(added.id);
      flash(`"${added.name}" eklendi`);
      changed();
    }
  };

  const pick = (e) => {
    const files = Array.from(e.target.files);
    e.target.value = "";
    uploadFiles(files);
  };

  // Dosya pencerenin herhangi bir yerine bırakılabilir
  const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes("Files");
  const dragProps = {
    onDragEnter: (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth.current += 1;
      setDrag(true);
    },
    onDragOver: (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    },
    onDragLeave: (e) => {
      if (!hasFiles(e)) return;
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (!dragDepth.current) setDrag(false);
    },
    onDrop: (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth.current = 0;
      setDrag(false);
      uploadFiles(Array.from(e.dataTransfer.files));
    },
  };

  const setField = (k, v) => setForm((f) => ({ ...(f || formOf(d)), [k]: v }));

  const toggleDay = (i) => {
    const days = shown.weekdays;
    setField("weekdays", days.includes(i) ? days.filter((x) => x !== i) : [...days, i].sort((a, b) => a - b));
  };

  const saveSchedule = async () => {
    const body = { enabled: shown.enabled, notify: shown.notify };
    if (d.spec?.type !== "interval") {
      body.time = shown.time.trim();
      body.weekdays = shown.weekdays;
      if (!body.time) return alert("Saat girin (ör. 09:00 veya 09:00, 18:00).");
    }
    setBusy(true);
    try {
      const r = await api("/api/routines/" + d.id, { method: "POST", body: JSON.stringify(body) });
      setDetail((cur) => (cur && cur.id === r.id ? { ...cur, ...r } : cur));
      setForm(null);
      flash("Kaydedildi");
      loadDetail(d.id);
      changed();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  };

  const toggleEdit = () => {
    if (text === null) setText(d.content ?? "");
    else if (!textDirty || confirm(DISCARD)) setText(null);
  };

  const saveContent = async () => {
    if (!text.trim()) return alert("Dosya boş olamaz.");
    setBusy(true);
    try {
      const r = await api("/api/routines/" + d.id, { method: "POST", body: JSON.stringify({ content: text }) });
      if (r.id !== d.id) choose(r.id);
      else {
        setDetail((cur) => (cur && cur.id === r.id ? { ...cur, ...r, content: text } : cur));
        setText(null);
        setForm(null); // dosyadaki saat/günler değişmiş olabilir
        loadDetail(d.id);
      }
      flash("Kaydedildi");
      changed();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  };

  const runNow = async () => {
    setBusy(true);
    try {
      await api(`/api/routines/${d.id}/run`, { method: "POST" });
      flash("Başlatıldı");
      loadDetail(d.id);
      changed();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!confirm(`"${d.name}" rutini ve dosyası silinsin mi? Raporlar kalır.`)) return;
    try {
      await api("/api/routines/" + d.id, { method: "DELETE" });
    } catch (e) {
      alert(e.message);
      return;
    }
    choose(null);
    flash("Silindi");
    changed();
  };

  const create = async () => {
    if (!newText.trim()) return alert("Rutin metni boş olamaz.");
    setBusy(true);
    try {
      const body = { content: newText };
      if (newName.trim()) body.name = newName.trim();
      const r = await api("/api/routines", { method: "POST", body: JSON.stringify(body) });
      choose(r.id);
      flash(`"${r.name}" oluşturuldu`);
      changed();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  };

  const cancelNew = () => {
    if (discardOk()) choose(sel || list?.[0]?.id || null);
  };

  const upload = () => fileInput.current?.click();
  const empty = list !== null && list.length === 0;
  const interval = d?.spec?.type === "interval";
  const next = d ? nextRun(d) : "";
  const runBlock = halted
    ? "ADA acil durdurma modunda"
    : d?.invalid
      ? "Dosya geçersiz; önce düzeltin"
      : d?.running
        ? "Zaten çalışıyor"
        : "Rutini şimdi bir kez çalıştır";

  const emptyView = (
    <div className="rt-empty">
      <div>
        <h3>Henüz rutin yok</h3>
        <p>Rutin, ADA'nın sizden komut beklemeden her gün belirlediğiniz saatte yaptığı bir iştir: haber özeti, gelen kutusu raporu, fiyat takibi, klasör düzenleme…</p>
        <p>
          İşi bir Markdown (.md) dosyasına düz Türkçe ile yazın ve yükleyin ya da dosyayı bu pencereye sürükleyip bırakın. Baştaki <code>---</code> satırları arasına ad,
          saat ve günleri yazabilirsiniz; yazmazsanız her gün 09:00'da çalışır.
        </p>
        <p className="rt-small">
          Dosyayı çalışma alanındaki <code>rutinler/</code> klasörüne kopyalamak da yeterlidir; ADA kendisi fark eder.
        </p>
        <div className="rt-empty-actions">
          <button className="rt-btn big primary" onClick={upload}>
            MD yükle
          </button>
          <button className="rt-btn big" onClick={startNew}>
            Yeni rutin
          </button>
        </div>
      </div>
      <div>
        <div className="rt-label">Örnek dosya</div>
        <pre className="rt-pre">{template || FALLBACK_TEMPLATE}</pre>
      </div>
    </div>
  );

  const newView = (
    <div className="rt-new">
      <div className="rt-title">
        <h3>Yeni rutin</h3>
      </div>
      <div className="rt-small">
        İşi düz Türkçe ile yazın. Baştaki <code>---</code> satırları arasındaki ayarlar adı, saati ve günleri belirler; yazılmazsa her gün 09:00'da çalışır.
      </div>
      <input className="rt-input" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Ad (boş bırakılırsa dosyadaki 'ad:' kullanılır)" />
      <textarea className="rt-text" value={newText} onChange={(e) => setNewText(e.target.value)} spellCheck={false} />
      <div className="rt-foot">
        <button className="rt-btn" onClick={cancelNew}>
          Vazgeç
        </button>
        <button className="rt-btn primary" onClick={create} disabled={busy}>
          Oluştur
        </button>
      </div>
    </div>
  );

  const detailView = d && (
    <>
      <div className="rt-title">
        <h3>{d.name}</h3>
        <RoutineBadge r={d} />
      </div>
      <div className="rt-path" title="Rutin dosyası">
        {d.file}
      </div>
      {d.invalid && <div className="warn rt-invalid">⚠ Dosya anlaşılamadı, rutin çalışmaz: {d.invalid}</div>}
      <div className="rt-facts">
        <span>{d.description || "—"}</span>
        {next && (
          <span>
            Sonraki: <b>{next}</b>
          </span>
        )}
        {d.last_run && <span>Son: {fmtWhen(d.last_run)}</span>}
        <span>{d.runs_count || 0} çalışma</span>
        {d.max_minutes ? <span>en fazla {d.max_minutes} dk</span> : null}
      </div>
      {d.last_summary && <Clamp className="rt-last" text={d.last_summary} />}

      <div className="rt-actions">
        <button className="rt-btn primary" onClick={runNow} disabled={busy || d.running || !!halted || !!d.invalid} title={runBlock}>
          Şimdi çalıştır
        </button>
        {d.running && d.conv_id && (
          <button className="rt-btn" onClick={() => openConv(d.conv_id)} title="Çalışmayı konuşma ekranında izle">
            İzle
          </button>
        )}
        <button className={"rt-btn" + (text !== null ? " on" : "")} onClick={toggleEdit}>
          Dosyayı düzenle
        </button>
        <button className="rt-btn danger" onClick={remove}>
          Sil
        </button>
      </div>

      {text !== null && (
        <div className="rt-editor">
          <textarea
            className="rt-text"
            rows={Math.min(28, Math.max(12, text.split("\n").length + 1))}
            value={text}
            onChange={(e) => setText(e.target.value)}
            spellCheck={false}
          />
          <div className="rt-foot">
            <button className="rt-btn" onClick={() => setText(null)}>
              Vazgeç
            </button>
            <button className="rt-btn primary" onClick={saveContent} disabled={busy}>
              Kaydet
            </button>
          </div>
        </div>
      )}

      <h4 className="rt-h">Zamanlama</h4>
      <div className="rt-sched">
        <label className="srow">
          <span className="slabel">Etkin</span>
          <input type="checkbox" checked={shown.enabled} onChange={(e) => setField("enabled", e.target.checked)} />
          <small>{shown.enabled ? "zamanı gelince kendiliğinden çalışır" : "yalnızca elle çalıştırılır"}</small>
        </label>
        {interval ? (
          <div className="srow">
            <span className="slabel">Sıklık</span>
            <span>Her {d.spec.minutes} dakikada bir</span>
            <small>(dosyadaki "aralik:" satırıyla değişir)</small>
          </div>
        ) : (
          <>
            <label className="srow">
              <span className="slabel">Saat</span>
              <input type="text" value={shown.time} placeholder="09:00, 18:00" onChange={(e) => setField("time", e.target.value)} />
            </label>
            <div className="srow">
              <span className="slabel">Günler</span>
              <div className="rt-days">
                {DAYS.map((name, i) => (
                  <button key={name} type="button" className={"rt-day" + (shown.weekdays.includes(i) ? " on" : "")} onClick={() => toggleDay(i)}>
                    {name}
                  </button>
                ))}
                <small>{daysHint(shown.weekdays)}</small>
              </div>
            </div>
          </>
        )}
        <label className="srow">
          <span className="slabel">Bildirim</span>
          <input type="checkbox" checked={shown.notify} onChange={(e) => setField("notify", e.target.checked)} />
          <small>bitince bildirim gönderir</small>
        </label>
        <div className="rt-foot">
          {dirty && <span className="rt-unsaved">Kaydedilmemiş değişiklik var</span>}
          <button className="rt-btn primary" onClick={saveSchedule} disabled={busy || !dirty}>
            Kaydet
          </button>
        </div>
      </div>

      <h4 className="rt-h">Öğrendikleri{d.notes?.length ? ` (${d.notes.length})` : ""}</h4>
      {d.notes?.length ? (
        <ul className="rt-notes">
          {d.notes.map((n, i) => (
            <li key={i}>
              {n.time && <span className="rt-when">{fmtWhen(n.time)}</span>}
              {n.text}
            </li>
          ))}
        </ul>
      ) : (
        <div className="muted rt-none">Henüz not yok — ADA her çalışmada öğrendiklerini buraya ekler.</div>
      )}

      <h4 className="rt-h">Geçmiş{d.runs?.length ? ` (${Math.max(d.runs_count || 0, d.runs.length)})` : ""}</h4>
      {d.runs?.length ? (
        d.runs.map((run) => <RunRow key={run.id} run={run} onOpenConv={openConv} />)
      ) : (
        <div className="muted rt-none">Henüz çalışmadı.{next ? ` İlk çalışma: ${next}.` : ""}</div>
      )}
    </>
  );

  return (
    <div className="modal-bg" onClick={close} {...dragProps}>
      <div className={"modal routines" + (drag ? " drag" : "")} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <b>Rutinler</b>
          {msg && <span className="ok-text rt-msg">{msg}</span>}
          <div className="rt-head-actions">
            <button className="rt-btn primary" onClick={upload} title=".md dosyası seçin (veya pencereye sürükleyip bırakın)">
              MD yükle
            </button>
            <button className="rt-btn" onClick={() => discardOk() && startNew()}>
              Yeni rutin
            </button>
          </div>
          <input type="file" ref={fileInput} accept=".md,.markdown,.txt" multiple hidden onChange={pick} />
          <button className="close" onClick={close}>
            ✕
          </button>
        </div>
        <div className="rt-hint">{HINT}</div>
        {halted && <div className="warn rt-halted">ADA acil durdurma modunda; rutinler çalışmaz.</div>}
        {list === null ? (
          <div className="muted">Yükleniyor…</div>
        ) : empty && !creating ? (
          emptyView
        ) : (
          <div className={"rt-grid" + (empty ? " single" : "")}>
            {!empty && (
              <div className="rt-list" ref={listBox}>
                {creating && (
                  <div className="rt-item on">
                    <div className="rt-item-top">
                      <span className="t">Yeni rutin</span>
                    </div>
                    <div className="rt-meta">
                      <span className="d">henüz kaydedilmedi</span>
                    </div>
                  </div>
                )}
                {list.map((r) => (
                  <div
                    key={r.id}
                    data-id={r.id}
                    className={"rt-item" + (r.id === sel && !creating ? " on" : "")}
                    onClick={() => select(r.id)}
                    title={r.invalid || r.file}
                  >
                    <div className="rt-item-top">
                      {r.running && <span className="dot" />}
                      <span className="t">{r.name}</span>
                      <RoutineBadge r={r} />
                    </div>
                    <div className={"rt-meta" + (r.invalid ? " warn" : "")}>
                      <span className="d">{r.invalid || r.description}</span>
                      {nextRun(r) && <span className="n">sonraki: {nextRun(r)}</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div className="rt-detail">
              {creating ? newView : detailView || <div className="muted">{sel ? "Yükleniyor…" : "Soldan bir rutin seçin."}</div>}
            </div>
          </div>
        )}
        {drag && <div className="rt-drop">Rutin dosyasını (.md) buraya bırakın</div>}
      </div>
    </div>
  );
}
