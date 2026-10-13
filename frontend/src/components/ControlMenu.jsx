import { useEffect, useRef, useState } from "react";
import { ArrowUpCircle, ChevronDown, FolderOpen, HardDrive, MessageSquareHeart, OctagonX, Play, Power, Repeat, Settings, SlidersHorizontal } from "lucide-react";
import { api } from "../api";
import LanguageSelect from "./LanguageSelect";

// Üst çubuktaki Kontrol menüsü: pencerenin eski tip menü çubuğunun (Kontrol → …) yerini alır.

const AUTONOMY = { full: "Tam özerk", smart: "Akıllı onay", ask: "Her işlemde sorar" };
const FOLDERS = [
  ["outputs", "Çıktılar", "Raporlar, belgeler", FolderOpen],
  ["routines", "Rutinler", "Günlük iş dosyaları", Repeat],
  ["home", "Veri", "Ayarlar ve geçmiş", HardDrive],
];

export default function ControlMenu({ status, busy, onEmergencyStop, onResume, onShowSettings, onShowUpdate, onShowSupport }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const box = useRef(null);

  useEffect(() => {
    if (!open) return;
    const outside = (e) => !box.current?.contains(e.target) && setOpen(false);
    const esc = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const halted = !!status?.halted;
  const state = halted ? "durduruldu" : busy ? `${busy} görev çalışıyor` : "hazır";

  const openFolder = async (which) => {
    try {
      await api("/api/control/open-folder", { method: "POST", body: JSON.stringify({ which }) });
      setOpen(false);
    } catch (e) {
      setNote(e.message);
    }
  };

  const quit = async () => {
    if (!confirm("ADA tamamen kapatılsın mı? Çalışan görevler, rutinler ve Gözcü durur.")) return;
    try {
      await api("/api/control/quit", { method: "POST" });
    } catch (e) {
      setNote(e.message);
    }
  };

  const act = (fn) => () => {
    setOpen(false);
    fn();
  };

  return (
    <div className="ctl" ref={box}>
      <button className={"ctl-btn" + (open ? " on" : "") + (halted ? " halted" : "")} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <SlidersHorizontal size={15} />
        <span>Kontrol</span>
        {status?.update && <i className="ctl-badge" title={`Sürüm ${status.update} hazır`} />}
        <ChevronDown size={14} className="chev" />
      </button>
      {open && (
        <div className="ctl-pop" role="menu">
          <div className="ctl-status">
            <i className={"ctl-dot" + (halted ? " off" : busy ? " busy" : "")} />
            <div>
              <b>ADA {state}</b>
              {status && (
                <span>
                  {status.model} · {AUTONOMY[status.autonomy] || status.autonomy}
                </span>
              )}
            </div>
          </div>

          {halted ? (
            <button className="ctl-main go" onClick={act(onResume)}>
              <Play size={18} />
              <span>
                <b>Devam et</b>
                <small>Görevler, hedefler ve rutinler yeniden çalışır</small>
              </span>
            </button>
          ) : (
            <button className="ctl-main stop" onClick={act(onEmergencyStop)}>
              <OctagonX size={18} />
              <span>
                <b>Acil durdur</b>
                <small>Tüm görevleri iptal eder, arka plan işlerini durdurur</small>
              </span>
            </button>
          )}

          <div className="ctl-label">Klasörler</div>
          <div className="ctl-tiles">
            {FOLDERS.map(([id, label, sub, Icon]) => (
              <button key={id} onClick={() => openFolder(id)} title={sub}>
                <Icon size={18} />
                <b>{label}</b>
                <small>{sub}</small>
              </button>
            ))}
          </div>
          {note && <div className="ctl-note">{note}</div>}

          <button className={"ctl-update" + (status?.update ? " new" : "")} onClick={act(onShowUpdate)}>
            <ArrowUpCircle size={16} />
            <span>{status?.update ? `Sürüm ${status.update} hazır — güncelle` : "Güncellemeleri denetle"}</span>
            {status?.version && <small>v{status.version}</small>}
          </button>

          <button className="ctl-update" onClick={act(onShowSupport)}>
            <MessageSquareHeart size={16} />
            <span>Geri bildirim ve destek</span>
          </button>

          <LanguageSelect className="ctl-lang" />

          <div className="ctl-foot">
            <button onClick={act(onShowSettings)}>
              <Settings size={15} /> Ayarlar
            </button>
            {status?.desktop && (
              <button className="quit" onClick={quit} title="Arka plan işleri de durur">
                <Power size={15} /> ADA'dan çık
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
