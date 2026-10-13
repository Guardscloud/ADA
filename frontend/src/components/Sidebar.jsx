import { useState } from "react";
import {
  BookOpen, ChevronDown, Clock, Cpu, History, OctagonX, Plus, Puzzle, Repeat, Settings, Shield, ShieldAlert,
  SquareTerminal, Target, Trash2, Volume2, VolumeX, Zap, Pause, Play, X,
} from "lucide-react";
import duckImg from "../assets/duck-96.png";
import { RoutineBadge, nextRun } from "./RoutinesPanel";
import { getItem, setItem } from "../storage";

const AUTONOMY = { full: "Tam özerk", smart: "Akıllı onay", ask: "Her işlemde sorar" };
const PROVIDER = { adaagi: "ADA-AGI", gemini: "Gemini", selfhost: "Kendi sunucum", anthropic: "Claude" };
const GOAL_STATUS = {
  active: "çalışıyor",
  waiting: "bekliyor",
  verifying: "denetleniyor",
  paused: "duraklatıldı",
  done: "tamamlandı",
  failed: "başarısız",
};

function progress(g) {
  if (!g.plan?.length) return "plan yok";
  const done = g.plan.filter((s) => s.status === "done" || s.status === "skipped").length;
  return `${done}/${g.plan.length}`;
}

/** Açılıp kapanabilen bölüm; durum tarayıcıda hatırlanır. */
function Section({ id, icon: Icon, title, count, action, children }) {
  const [open, setOpen] = useState(() => getItem("ada.side." + id) !== "0");
  const toggle = () => {
    setOpen((v) => {
      setItem("ada.side." + id, v ? "0" : "1");
      return !v;
    });
  };
  return (
    <div className={"side-section" + (open ? " open" : "")}>
      <h4>
        <button className="sec-toggle" onClick={toggle}>
          <ChevronDown size={14} className="chev" />
          <Icon size={14} />
          <span>{title}</span>
          {count > 0 && <span className="count">{count}</span>}
        </button>
        {action}
      </h4>
      {open && <div className="sec-body">{children}</div>}
    </div>
  );
}

function Empty({ children }) {
  return <div className="side-empty">{children}</div>;
}

export default function Sidebar({
  status, convs, schedules, goals, triggers, skills, routines, currentId,
  onNew, onOpen, onDelete, onDeleteSchedule, onGoalAction, onNewGoal, onDeleteTrigger, onDeleteSkill, onOpenRoutine,
  watch, terminals, onShowTerminal, onShowWatch,
  onEmergencyStop, onShowSafety, onShowSettings, onShowHelp, voiceOn, onToggleVoice,
}) {
  const goalConvs = new Set(goals.map((g) => g.conv_id));
  const problems = watch?.problems || [];
  const crit = problems.some((p) => p.level === "crit");
  const liveTerms = (terminals?.sessions || []).filter((s) => s.alive).length;
  const tasks = convs.filter((c) => !goalConvs.has(c.id));
  const busy = convs.filter((c) => c.running).length;

  return (
    <aside>
      <div className="brand">
        <img src={duckImg} alt="" />
        <div className="brand-text">
          <b>ADA</b>
          <span>
            <i className={"live-dot" + (status?.halted ? " off" : "")} />
            {status?.halted ? "durduruldu" : busy ? `${busy} görev çalışıyor` : "hazır"}
          </span>
        </div>
        <button className="icon-btn" onClick={onToggleVoice} title={voiceOn ? "Sesli yanıt açık" : "Sesli yanıt kapalı"}>
          {voiceOn ? <Volume2 size={17} /> : <VolumeX size={17} />}
        </button>
      </div>

      <button id="newBtn" onClick={onNew}>
        <Plus size={17} strokeWidth={2.5} /> Yeni görev
      </button>

      <nav className="quick">
        <button onClick={onShowTerminal} title="Komut satırı (ör. claude remote-control)">
          <SquareTerminal size={18} />
          <span>Terminal</span>
          {liveTerms > 0 && <em>{liveTerms}</em>}
        </button>
        <button
          onClick={onShowWatch}
          className={crit ? "crit" : problems.length ? "warn" : ""}
          title={problems.length ? problems.map((p) => p.title).join("\n") : "Bilgisayarı sürekli denetler"}
        >
          {problems.length ? <ShieldAlert size={18} /> : <Shield size={18} />}
          <span>Gözcü</span>
          {problems.length > 0 && <em>{problems.length}</em>}
        </button>
        <button onClick={onShowSafety} title="İşlem günlüğü ve dosya yedekleri">
          <History size={18} />
          <span>Günlük</span>
        </button>
      </nav>

      <div className="side-scroll">
        <div className="side-label">Görevler</div>
        <div id="convList">
          {tasks.length === 0 && <Empty>Henüz görev yok.</Empty>}
          {tasks.map((c) => (
            <div key={c.id} className={"conv" + (c.id === currentId ? " active" : "")} onClick={() => onOpen(c.id)} title={c.title}>
              {c.running ? <span className="dot" /> : <span className="conv-dot" />}
              <span className="t">{c.title}</span>
              <button
                className="del"
                title="Sil"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(c.id);
                }}
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>

        <Section
          id="routines"
          icon={Repeat}
          title="Rutinler"
          count={routines.length}
          action={
            <button className="mini" onClick={() => onOpenRoutine("new")} title="Yeni rutin">
              <Plus size={14} />
            </button>
          }
        >
          {routines.length === 0 && (
            <Empty>
              Henüz rutin yok —{" "}
              <button className="linkbtn" onClick={() => onOpenRoutine(null)}>
                .md dosyası yükleyin
              </button>
            </Empty>
          )}
          {routines.slice(0, 6).map((r) => (
            <div
              key={r.id}
              className="routine"
              onClick={() => onOpenRoutine(r.id)}
              title={r.invalid ? "⚠ " + r.invalid : [r.description, r.last_summary].filter(Boolean).join("\n")}
            >
              <div className="routine-top">
                {r.running && <span className="dot" />}
                <span className="t">{r.name}</span>
                <RoutineBadge r={r} />
              </div>
              <div className={"rt-meta" + (r.invalid ? " warn" : "")}>
                <span className="d">{r.invalid || r.description}</span>
                {nextRun(r) && <span className="n">{nextRun(r)}</span>}
              </div>
            </div>
          ))}
          {routines.length > 6 && (
            <button className="linkbtn" onClick={() => onOpenRoutine(null)}>
              Tümü ({routines.length}) →
            </button>
          )}
        </Section>

        <Section
          id="goals"
          icon={Target}
          title="Hedefler"
          count={goals.length}
          action={
            <button className="mini" onClick={onNewGoal} title="Yeni hedef">
              <Plus size={14} />
            </button>
          }
        >
          {goals.length === 0 && <Empty>Büyük işler için hedef oluşturun.</Empty>}
          {goals.map((g) => (
            <div key={g.id} className={"goal" + (g.conv_id === currentId ? " active" : "")} onClick={() => onOpen(g.conv_id)} title={g.objective}>
              <div className="goal-top">
                {g.running && <span className="dot" />}
                <span className="t">{g.title}</span>
                <span className="goal-actions" onClick={(e) => e.stopPropagation()}>
                  {["active", "waiting"].includes(g.status) && (
                    <button onClick={() => onGoalAction(g.id, "pause")} title="Duraklat">
                      <Pause size={13} />
                    </button>
                  )}
                  {["paused", "failed"].includes(g.status) && (
                    <button onClick={() => onGoalAction(g.id, "resume")} title="Devam ettir">
                      <Play size={13} />
                    </button>
                  )}
                  <button onClick={() => onGoalAction(g.id, "delete")} title="Sil">
                    <X size={13} />
                  </button>
                </span>
              </div>
              <div className="goal-meta">
                <span className={"badge " + g.status}>{GOAL_STATUS[g.status] || g.status}</span>
                <span className="goal-bar">
                  <i style={{ width: g.plan?.length ? `${(100 * g.plan.filter((s) => ["done", "skipped"].includes(s.status)).length) / g.plan.length}%` : 0 }} />
                </span>
                <span>{progress(g)}</span>
              </div>
              {g.status === "waiting" && g.wait_reason && <div className="goal-wait">{g.wait_reason}</div>}
            </div>
          ))}
        </Section>

        <Section id="triggers" icon={Zap} title="Tetikleyiciler" count={triggers.length}>
          {triggers.length === 0 && <Empty>Yok — sohbette "X olunca Y yap" deyin.</Empty>}
          {triggers.map((t) => (
            <div key={t.id} className="sched" title={t.prompt}>
              <span className="t">
                {t.name} · <span className="sub">{t.description}</span>
              </span>
              <button title="Sil" onClick={() => onDeleteTrigger(t.id)}>
                <X size={13} />
              </button>
            </div>
          ))}
        </Section>

        <Section id="schedules" icon={Clock} title="Zamanlanmış" count={schedules.length}>
          {schedules.length === 0 && <Empty>Zamanlanmış görev yok.</Empty>}
          {schedules.map((s) => (
            <div key={s.id} className="sched" title={s.prompt}>
              <span className="t">
                {s.name} · <span className="sub">{s.description}</span>
              </span>
              <button title="İptal" onClick={() => onDeleteSchedule(s.id)}>
                <X size={13} />
              </button>
            </div>
          ))}
        </Section>

        <Section id="skills" icon={Puzzle} title="Beceriler" count={skills.length}>
          {skills.length === 0 && <Empty>ADA öğrendikçe burada birikir.</Empty>}
          {skills.slice(0, 8).map((s) => (
            <div key={s.slug} className="sched" title={`${s.description}\nNe zaman: ${s.when}\nKullanım: ${s.uses}`}>
              <span className="t">
                {s.name} {s.uses ? <span className="sub">· {s.uses}×</span> : ""}
              </span>
              <button title="Sil" onClick={() => onDeleteSkill(s.slug)}>
                <X size={13} />
              </button>
            </div>
          ))}
        </Section>
      </div>

      <div className="side-foot">
        {status ? (
          <div className="sys-card" title={`Ekran: ${status.computer ? "açık" : "kapalı"} · Kabuk: ${status.shell ? "açık" : "kapalı"}`}>
            <Cpu size={15} />
            <div>
              <b>
                {PROVIDER[status.provider] || status.provider} · {status.model}
              </b>
              <span>
                {AUTONOMY[status.autonomy] || status.autonomy}
                {status.fallback_model && " · Qwen yedeği"}
              </span>
              {!status.api_key && <span className="warn">⚠ {status.key_name} ayarlı değil</span>}
            </div>
          </div>
        ) : (
          <div className="sys-card">…</div>
        )}
        <div className="foot-row">
          <button className="stop" onClick={onEmergencyStop} title="Tüm görevleri iptal et, hedefleri ve tetikleyicileri durdur">
            <OctagonX size={16} /> Acil durdur
          </button>
          <button className="icon-btn" onClick={onShowSettings} title="Ayarlar">
            <Settings size={17} />
          </button>
          <button className="icon-btn" onClick={onShowHelp} title="Kullanım kılavuzu">
            <BookOpen size={17} />
          </button>
        </div>
      </div>
    </aside>
  );
}
