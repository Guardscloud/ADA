import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { api } from "../api";

const PRESETS = [
  { title: "Claude Remote Control", command: "claude remote-control" },
  { title: "Claude Code", command: "claude" },
];

const THEME = {
  background: "#151514",
  foreground: "#e8e6e1",
  cursor: "#8fb0ea",
  selectionBackground: "#3b5f9e88",
};

/** Tek bir oturuma bağlı xterm görünümü: sunucudaki son çıktıyı yeniden oynatır, yazılanı gönderir. */
function TermView({ session, onExit }) {
  const box = useRef(null);

  useEffect(() => {
    const term = new Terminal({
      fontFamily: 'Cascadia Mono, Consolas, "Courier New", monospace',
      fontSize: 13,
      cursorBlink: true,
      scrollback: 5000,
      theme: THEME,
      allowProposedApi: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(box.current);
    try {
      fit.fit();
    } catch {
      /* görünmezken ölçülemez */
    }

    // Ctrl+C: seçim varsa kopyala (yoksa programa ^C gider); Ctrl+V: yapıştır
    term.attachCustomKeyEventHandler((e) => {
      if (e.type !== "keydown") return true;
      if (e.ctrlKey && !e.altKey && (e.key === "c" || e.key === "C") && term.hasSelection()) {
        navigator.clipboard?.writeText(term.getSelection()).catch(() => {});
        term.clearSelection();
        return false;
      }
      if (e.ctrlKey && !e.altKey && (e.key === "v" || e.key === "V")) return false; // tarayıcı yapıştırma olayı → onData
      return true;
    });

    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/terminal/sessions/${session.id}/ws`);
    const send = (msg) => ws.readyState === 1 && ws.send(JSON.stringify(msg));
    const sizeUp = () => {
      try {
        fit.fit();
      } catch {
        return;
      }
      send({ t: "resize", cols: term.cols, rows: term.rows });
    };
    ws.onopen = sizeUp;
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.t === "out") term.write(msg.d);
      else if (msg.t === "exit") onExit?.(session.id, msg.code);
    };
    ws.onclose = (ev) => {
      if (ev.code === 4404) term.write("\r\n\x1b[90m[oturum bulunamadı]\x1b[0m\r\n");
      else if (ev.code === 4403) term.write("\r\n\x1b[31m[erişim reddedildi]\x1b[0m\r\n");
    };
    const dataSub = term.onData((d) => send({ t: "in", d }));
    const ro = new ResizeObserver(() => sizeUp());
    ro.observe(box.current);
    term.focus();

    return () => {
      ro.disconnect();
      dataSub.dispose();
      ws.close();
      term.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.id]);

  return <div className="term-box" ref={box} />;
}

export default function TerminalPanel({ onClose }) {
  const [state, setState] = useState({ sessions: [], saved: [], shell: "" });
  const [activeId, setActiveId] = useState(null);
  const [cmd, setCmd] = useState("");
  const [showSaved, setShowSaved] = useState(false);
  const [draft, setDraft] = useState({ title: "", command: "", cwd: "" });

  const load = async () => {
    const s = await api("/api/terminal").catch(() => null);
    if (!s) return null;
    setState(s);
    return s;
  };

  useEffect(() => {
    load().then((s) => {
      if (!s) return;
      const alive = s.sessions.filter((x) => x.alive);
      const pick = alive[alive.length - 1] || s.sessions[s.sessions.length - 1];
      if (pick) setActiveId(pick.id);
      else open("");
    });
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const open = async (command, title = "") => {
    try {
      const s = await api("/api/terminal/sessions", { method: "POST", body: JSON.stringify({ command, title }) });
      await load();
      setActiveId(s.id);
    } catch (e) {
      alert(e.message);
    }
  };

  const close = async (id) => {
    const s = state.sessions.find((x) => x.id === id);
    if (s?.alive && !confirm(`"${s.title}" kapatılsın mı? İçinde çalışan program da durur.`)) return;
    await api("/api/terminal/sessions/" + id, { method: "DELETE" }).catch(() => {});
    const rest = state.sessions.filter((x) => x.id !== id);
    if (activeId === id) setActiveId(rest.length ? rest[rest.length - 1].id : null);
    load();
  };

  const run = (e) => {
    e.preventDefault();
    if (!cmd.trim()) return;
    open(cmd.trim());
    setCmd("");
  };

  const addSaved = async (item) => {
    try {
      await api("/api/terminal/saved", { method: "POST", body: JSON.stringify({ autostart: true, ...item }) });
      setDraft({ title: "", command: "", cwd: "" });
      setShowSaved(true);
      load();
    } catch (e) {
      alert(e.message);
    }
  };

  const savedAction = async (k, action) => {
    try {
      if (action === "delete") {
        if (!confirm(`"${k.title}" kalıcı komutlardan çıkarılsın mı? (Çalışan oturumu açık kalır.)`)) return;
        await api("/api/terminal/saved/" + k.id, { method: "DELETE" });
      } else if (action === "start") {
        const s = await api(`/api/terminal/saved/${k.id}/start`, { method: "POST" });
        setActiveId(s.id);
      } else if (action === "toggle") {
        await api("/api/terminal/saved/" + k.id, { method: "POST", body: JSON.stringify({ autostart: !k.autostart }) });
      }
    } catch (e) {
      alert(e.message);
    }
    load();
  };

  const active = state.sessions.find((s) => s.id === activeId);

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal terminal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <b>Terminal</b>
          <span className="muted term-shell">{state.shell}</span>
          <button className="close" onClick={onClose} title="Paneli kapat (oturumlar çalışmaya devam eder)">
            ✕
          </button>
        </div>

        <div className="term-tabs">
          {state.sessions.map((s) => (
            <div key={s.id} className={"term-tab" + (s.id === activeId ? " on" : "")} onClick={() => setActiveId(s.id)} title={s.command || "kabuk"}>
              <span className={"tdot" + (s.alive ? " live" : "")} />
              <span className="tt">{s.title}</span>
              {s.source === "ada" && <span className="tag">ADA</span>}
              {s.source === "kalıcı" && <span className="tag">kalıcı</span>}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  close(s.id);
                }}
              >
                ✕
              </button>
            </div>
          ))}
          <button className="term-new" onClick={() => open("")} title="Yeni kabuk">
            +
          </button>
        </div>

        <form className="term-run" onSubmit={run}>
          <input value={cmd} onChange={(e) => setCmd(e.target.value)} placeholder="Yeni sekmede komut çalıştır… (ör. claude remote-control)" />
          <button type="submit">Çalıştır</button>
          {PRESETS.map((p) => (
            <button type="button" key={p.command} className="ghost" onClick={() => open(p.command, p.title)} title={p.command}>
              {p.title}
            </button>
          ))}
          <button type="button" className={"ghost" + (showSaved ? " on" : "")} onClick={() => setShowSaved((v) => !v)}>
            Kalıcı komutlar ({state.saved.length})
          </button>
        </form>

        {showSaved && (
          <div className="term-saved">
            <div className="muted small">
              Kalıcı komutlar ADA açılınca kendiliğinden başlar ve kapanırlarsa yeniden başlatılır (saatte en fazla 5 kez). Gözcü durumlarını izler.
            </div>
            {state.saved.map((k) => (
              <div key={k.id} className="saved-row">
                <span className={"tdot" + (k.alive ? " live" : "")} />
                <span className="st" title={k.command}>
                  <b>{k.title}</b> <code>{k.command}</code>
                  {k.gave_up && <span className="warn"> · {k.gave_up}</span>}
                </span>
                <label title="ADA açılınca başlat, kapanırsa yeniden başlat">
                  <input type="checkbox" checked={k.autostart} onChange={() => savedAction(k, "toggle")} /> otomatik
                </label>
                {k.session_id && (
                  <button onClick={() => setActiveId(k.session_id)} title="Oturumu göster">
                    Göster
                  </button>
                )}
                {!k.alive && <button onClick={() => savedAction(k, "start")}>Başlat</button>}
                <button onClick={() => savedAction(k, "delete")} title="Kaldır">
                  ✕
                </button>
              </div>
            ))}
            <form
              className="saved-add"
              onSubmit={(e) => {
                e.preventDefault();
                if (draft.command.trim()) addSaved(draft);
              }}
            >
              <input placeholder="Ad" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
              <input placeholder="Komut (ör. claude remote-control)" value={draft.command} onChange={(e) => setDraft({ ...draft, command: e.target.value })} />
              <input placeholder="Klasör (isteğe bağlı)" value={draft.cwd} onChange={(e) => setDraft({ ...draft, cwd: e.target.value })} />
              <button type="submit">Ekle</button>
            </form>
            {active?.command && !active.saved_id && (
              <button className="linkbtn" onClick={() => addSaved({ title: active.title, command: active.command, cwd: active.cwd })}>
                Bu sekmedeki komutu ({active.command}) kalıcı yap
              </button>
            )}
          </div>
        )}

        {active ? <TermView key={active.id} session={active} onExit={() => load()} /> : <div className="term-empty muted">Açık oturum yok — + ile yeni kabuk açın.</div>}
        <div className="muted small term-hint">
          Panel kapansa da oturumlar arka planda çalışır. Ctrl+C: seçili metni kopyalar (seçim yoksa programı durdurur) · Ctrl+V: yapıştırır.
        </div>
      </div>
    </div>
  );
}
