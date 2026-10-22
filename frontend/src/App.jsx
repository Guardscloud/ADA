import { useCallback, useEffect, useRef, useState } from "react";
import { api, followStream, notify } from "./api";
import { speak } from "./voice";
import { getItem, setItem } from "./storage";
import { tr } from "./i18n";
import Sidebar from "./components/Sidebar";
import Thread from "./components/Thread";
import Composer from "./components/Composer";
import SafetyPanel from "./components/SafetyPanel";
import SettingsPanel from "./components/SettingsPanel";
import HelpPanel from "./components/HelpPanel";
import RoutinesPanel from "./components/RoutinesPanel";
import TerminalPanel from "./components/TerminalPanel";
import WatchPanel from "./components/WatchPanel";
import ControlMenu from "./components/ControlMenu";
import SetupWizard from "./components/SetupWizard";
import UpdatePanel from "./components/UpdatePanel";
import SupportPanel from "./components/SupportPanel";
import InstallerPanel from "./components/InstallerPanel";

const blankAssistant = () => ({ role: "assistant", text: "", steps: [], files: [], approvals: [], thinking: "", screenshot: null });

export default function App() {
  const [status, setStatus] = useState(null);
  const [convs, setConvs] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [goals, setGoals] = useState([]);
  const [triggers, setTriggers] = useState([]);
  const [skills, setSkills] = useState([]);
  const [routines, setRoutines] = useState([]);
  const [currentId, setCurrentId] = useState(null);
  const [entries, setEntries] = useState([]);
  const [running, setRunning] = useState(false);
  const [prefill, setPrefill] = useState("");
  const [showSafety, setShowSafety] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [showRoutines, setShowRoutines] = useState(false);
  const [routineFocus, setRoutineFocus] = useState(null); // rutin kimliği, "new" veya null
  const [showTerminal, setShowTerminal] = useState(false);
  const [showWatch, setShowWatch] = useState(false);
  const [showUpdate, setShowUpdate] = useState(false);
  const [showSupport, setShowSupport] = useState(false);
  const [installer, setInstaller] = useState(null); // /d komutu: {mode, query, autoStart}
  const [watch, setWatch] = useState(null);
  const [terminals, setTerminals] = useState(null);
  const [voiceOn, setVoiceOn] = useState(() => {
    return getItem("ada.voice") === "1";
  });
  const voiceRef = useRef(voiceOn);
  useEffect(() => {
    voiceRef.current = voiceOn;
    setItem("ada.voice", voiceOn ? "1" : "0");
  }, [voiceOn]);
  const currentRef = useRef(null); // akış olaylarının hangi konuşmaya ait olduğunu doğrulamak için

  const refreshLists = useCallback(async () => {
    const safe = (p) => api(p).catch(() => []);
    const [c, s, g, t, k, r] = await Promise.all([
      api("/api/conversations"),
      safe("/api/schedules"),
      safe("/api/goals"),
      safe("/api/triggers"),
      safe("/api/skills"),
      safe("/api/routines"),
    ]);
    setConvs(c);
    api("/api/status").then(setStatus).catch(() => {});
    api("/api/watch").then(setWatch).catch(() => {});
    api("/api/terminal").then(setTerminals).catch(() => {});
    setSchedules(s);
    setGoals(g);
    setTriggers(t);
    setSkills(k);
    setRoutines(r);
  }, []);

  const runningRef = useRef(false);
  useEffect(() => {
    runningRef.current = running;
  }, [running]);

  // Hedefler ve tetikleyiciler arka planda çalışır: listeleri düzenli yenile; açık konuşmada
  // arka planda yeni bir tur başladıysa (ör. hedef oturumu) onu izlemeye başla
  useEffect(() => {
    const id = setInterval(async () => {
      if (document.hidden) return;
      await refreshLists();
      const cur = currentRef.current;
      if (cur && !runningRef.current) {
        const data = await api("/api/conversations/" + cur).catch(() => null);
        if (data?.running && currentRef.current === cur) openConvRef.current(cur);
      }
    }, 8000);
    return () => clearInterval(id);
  }, [refreshLists]);

  const select = (id) => {
    currentRef.current = id;
    setCurrentId(id);
    setItem("ada.conv", id || null);
  };

  // Son asistan girdisini fonksiyonel olarak günceller (akış sırasında)
  const updateLast = (fn) =>
    setEntries((prev) => {
      const last = prev[prev.length - 1];
      if (!last || last.role !== "assistant") return prev;
      return [...prev.slice(0, -1), fn(last)];
    });

  const applyEvent = (ev) =>
    updateLast((last) => {
      switch (ev.type) {
        case "text":
          return { ...last, text: last.text + ev.text };
        case "text_set":
          // Başarısız model denemesinin yarım metni geri alındı (model değişimi / Qwen devri)
          return { ...last, text: ev.text };
        case "thinking":
          return { ...last, thinking: (last.thinking || "") + ev.text };
        case "step":
          return { ...last, steps: [...last.steps, { label: ev.label, detail: ev.detail, agent: ev.agent }] };
        case "step_error":
          return { ...last, steps: [...last.steps, { label: ev.label + " (hata)", detail: ev.detail, agent: ev.agent, error: true }] };
        case "files":
          return { ...last, files: [...last.files, ...ev.files.filter((f) => !last.files.includes(f))] };
        case "screenshot":
          return { ...last, screenshot: ev.data };
        case "approval":
          notify("ADA onay bekliyor: " + ev.title);
          return { ...last, approvals: [...(last.approvals || []), { ...ev, resolved: false }] };
        case "approval_resolved":
          return { ...last, approvals: (last.approvals || []).map((a) => (a.id === ev.id ? { ...a, resolved: true } : a)) };
        case "error":
          return { ...last, text: last.text + `\n\n_${ev.message}_` };
        default:
          return last;
      }
    });

  const follow = async (id) => {
    setRunning(true);
    try {
      await followStream(id, applyEvent, () => currentRef.current === id);
    } finally {
      if (currentRef.current === id) setRunning(false);
      refreshLists();
      if (voiceRef.current) {
        api("/api/conversations/" + id)
          .then((d) => speak(d.ui[d.ui.length - 1]?.text || ""))
          .catch(() => {});
      }
      if (document.hidden) notify("ADA görevi tamamladı");
    }
  };

  const openConv = async (id) => {
    select(id);
    const data = await api("/api/conversations/" + id);
    const ui = data.ui.map((m) => ({ ...blankAssistant(), ...m, steps: m.steps || [], files: m.files || [] }));
    if (data.running && ui.length && ui[ui.length - 1].role === "assistant") ui.pop();
    if (data.running) ui.push(blankAssistant());
    setEntries(ui);
    setRunning(data.running);
    refreshLists();
    if (data.running) follow(id);
  };

  const openConvRef = useRef(openConv);
  openConvRef.current = openConv;

  useEffect(() => {
    (async () => {
      setStatus(await api("/api/status"));
      const saved = getItem("ada.conv");
      if (saved) {
        try {
          await openConv(saved);
          return;
        } catch {
          select(null);
        }
      }
      refreshLists();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const newConv = () => {
    select(null);
    setEntries([]);
    setRunning(false);
    refreshLists();
  };

  const deleteConv = async (id) => {
    if (!confirm("Bu görev geçmişi silinsin mi?")) return;
    try {
      await api("/api/conversations/" + id, { method: "DELETE" });
    } catch (e) {
      alert(e.message);
      return;
    }
    if (id === currentId) newConv();
    else refreshLists();
  };

  const send = async (text, attachments) => {
    let id = currentId;
    if (!id) {
      id = (await api("/api/conversations", { method: "POST" })).id;
      select(id);
    }
    try {
      await api(`/api/conversations/${id}/messages`, {
        method: "POST",
        body: JSON.stringify({ text, attachments: attachments.map((a) => a.path) }),
      });
    } catch (e) {
      alert(e.message);
      return false;
    }
    const shown = text + (attachments.length ? "\n📎 " + attachments.map((a) => a.name).join(", ") : "");
    setEntries((prev) => [...prev, { role: "user", text: shown }, blankAssistant()]);
    refreshLists();
    follow(id);
    return true;
  };

  const stop = () => api(`/api/conversations/${currentId}/stop`, { method: "POST" }).catch((e) => alert(e.message));

  const approve = async (approvalId, approved, always = false) => {
    try {
      await api("/api/approvals/" + approvalId, { method: "POST", body: JSON.stringify({ approved, always }) });
    } catch (e) {
      alert(e.message);
    }
    updateLast((last) => ({ ...last, approvals: last.approvals.map((a) => (a.id === approvalId ? { ...a, resolved: true } : a)) }));
  };

  const emergencyStop = async () => {
    if (!confirm("ACİL DURDURMA: çalışan tüm görevler iptal edilecek, hedefler duraklatılacak, tetikleyiciler ve zamanlanmış görevler duracak. Emin misiniz?")) return;
    const r = await api("/api/emergency-stop", { method: "POST" });
    alert(`ADA durduruldu. ${r.cancelled} görev iptal edildi, ${r.paused_goals} hedef duraklatıldı.`);
    refreshLists();
  };

  const resumeAll = async () => {
    await api("/api/resume", { method: "POST" });
    refreshLists();
  };

  const deleteSchedule = async (id) => {
    await api("/api/schedules/" + id, { method: "DELETE" });
    refreshLists();
  };

  const goalAction = async (id, action) => {
    try {
      if (action === "delete") {
        if (!confirm("Hedef silinsin mi? (Konuşma geçmişi kalır)")) return;
        await api("/api/goals/" + id, { method: "DELETE" });
      } else {
        await api("/api/goals/" + id, { method: "POST", body: JSON.stringify({ action }) });
      }
    } catch (e) {
      alert(e.message);
    }
    refreshLists();
  };

  const newGoal = async () => {
    const title = prompt("Hedefin kısa adı:");
    if (!title?.trim()) return;
    const objective = prompt("Ayrıntılı amaç (ne teslim edilmeli, kısıtlar, kalite ölçütü):");
    if (!objective?.trim()) return;
    try {
      const g = await api("/api/goals", { method: "POST", body: JSON.stringify({ title, objective }) });
      await refreshLists();
      openConv(g.conv_id);
    } catch (e) {
      alert(e.message);
    }
  };

  const deleteTrigger = async (id) => {
    if (!confirm("Tetikleyici silinsin mi?")) return;
    await api("/api/triggers/" + id, { method: "DELETE" });
    refreshLists();
  };

  const deleteSkill = async (slug) => {
    if (!confirm("Beceri silinsin mi?")) return;
    await api("/api/skills/" + slug, { method: "DELETE" });
    refreshLists();
  };

  const openRoutines = (focus) => {
    setRoutineFocus(focus);
    setShowRoutines(true);
  };

  return (
    <>
      <Sidebar
        status={status}
        convs={convs}
        schedules={schedules}
        goals={goals}
        triggers={triggers}
        skills={skills}
        routines={routines}
        currentId={currentId}
        onNew={newConv}
        onOpen={openConv}
        onDelete={deleteConv}
        onDeleteSchedule={deleteSchedule}
        onGoalAction={goalAction}
        onNewGoal={newGoal}
        onDeleteTrigger={deleteTrigger}
        onDeleteSkill={deleteSkill}
        onOpenRoutine={openRoutines}
        watch={watch}
        terminals={terminals}
        onShowTerminal={() => setShowTerminal(true)}
        onShowWatch={() => setShowWatch(true)}
        onEmergencyStop={emergencyStop}
        onShowSafety={() => setShowSafety(true)}
        onShowSettings={() => setShowSettings(true)}
        onShowHelp={() => setShowHelp(true)}
        voiceOn={voiceOn}
        onToggleVoice={() => setVoiceOn((v) => !v)}
      />
      <main>
        <header className="topbar">
          <div className="tb-title">{currentId ? convs.find((c) => c.id === currentId)?.title || "Görev" : "Yeni görev"}</div>
          {running && (
            <span className="pill live">
              <i /> çalışıyor
            </span>
          )}
          {watch?.enabled && watch.metrics?.time && (
            <button
              className={"pill" + (watch.problems?.some((p) => p.level === "crit") ? " crit" : watch.problems?.length ? " warn" : " ok")}
              onClick={() => setShowWatch(true)}
              title="Gözcü"
            >
              <i />
              {watch.problems?.length ? `${watch.problems.length} sorun` : "sistem sağlıklı"} · CPU %{Math.round(watch.metrics.cpu)} · RAM %{Math.round(watch.metrics.mem)}
            </button>
          )}
          <ControlMenu
            status={status}
            busy={convs.filter((c) => c.running).length}
            onEmergencyStop={emergencyStop}
            onResume={resumeAll}
            onShowSettings={() => setShowSettings(true)}
            onShowUpdate={() => setShowUpdate(true)}
            onShowSupport={() => setShowSupport(true)}
          />
        </header>
        {status?.halted && (
          <div className="halted">
            <span>
              <b>ADA durduruldu</b> ({status.halted.time?.slice(0, 16).replace("T", " ")}, {status.halted.source}). Görevler, hedefler ve tetikleyiciler çalışmıyor.
            </span>
            <button onClick={resumeAll}>Devam et</button>
          </div>
        )}
        <Thread
          convId={currentId}
          entries={entries}
          running={running}
          onStop={stop}
          onApprove={approve}
          onExample={(p) => setPrefill(tr(p))}
          status={status}
          onOpenSettings={() => setShowSettings(true)}
        />
        <Composer
          onSend={send}
          onCommand={(c) => setInstaller({ mode: c.action, query: c.query, autoStart: c.action === "update" && !c.manual, key: Date.now() })}
          disabled={running} prefill={prefill} onPrefillUsed={() => setPrefill("")} />
      </main>
      {status?.setup_needed && <SetupWizard onDone={refreshLists} />}
      {showSafety && <SafetyPanel onClose={() => setShowSafety(false)} usage={status?.usage} />}
      {showSettings && <SettingsPanel onClose={() => setShowSettings(false)} />}
      {showHelp && <HelpPanel onClose={() => setShowHelp(false)} />}
      {showSupport && <SupportPanel onClose={() => setShowSupport(false)} />}
      {installer && (
        <InstallerPanel
          key={installer.key}
          mode={installer.mode}
          query={installer.query}
          autoStart={installer.autoStart}
          onClose={() => {
            setInstaller(null);
            refreshLists();
          }}
        />
      )}
      {showUpdate && (
        <UpdatePanel
          onClose={() => {
            setShowUpdate(false);
            refreshLists();
          }}
          onOpenSettings={() => {
            setShowUpdate(false);
            setShowSettings(true);
          }}
        />
      )}
      {showTerminal && (
        <TerminalPanel
          onClose={() => {
            setShowTerminal(false);
            refreshLists();
          }}
        />
      )}
      {showWatch && (
        <WatchPanel
          onClose={() => setShowWatch(false)}
          onOpenConv={(id) => {
            setShowWatch(false);
            openConv(id).catch((e) => alert(e.message));
          }}
          onOpenSettings={() => {
            setShowWatch(false);
            setShowSettings(true);
          }}
        />
      )}
      {showRoutines && (
        <RoutinesPanel
          focus={routineFocus}
          onClose={() => setShowRoutines(false)}
          onOpenConv={(id) => {
            setShowRoutines(false);
            openConv(id).catch((e) => alert(e.message));
          }}
          onChanged={refreshLists}
          halted={status?.halted}
        />
      )}
    </>
  );
}
