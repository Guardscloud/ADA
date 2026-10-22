import { useEffect, useRef, useState } from "react";
import { ArrowUp, FileText, LoaderCircle, Mic, Paperclip, X } from "lucide-react";
import { api, upload } from "../api";
import { recordingSupported, startRecording, transcribe } from "../voice";

const isMac = /Mac/i.test(navigator.platform || navigator.userAgent);
const DICTATION_HINT = isMac
  ? "macOS dikte özelliğini de kullanabilirsiniz: metin kutusuna tıklayıp Fn (🌐) tuşuna iki kez basın."
  : "Windows'un sesle yazma özelliğini de kullanabilirsiniz: metin kutusuna tıklayıp Windows + H tuşlarına basın.";

// Sohbet kutusu komutları: model çağrılmadan arayüzde karşılanır
const COMMANDS = [
  { cmd: "/d", fill: "/d", label: "Program kur", desc: "Kurulum penceresini açar" },
  { cmd: "/d <program>", fill: "/d ", label: "Ara ve kur", desc: "ör. /d vlc" },
  { cmd: "/d update", fill: "/d update", label: "Her şeyi güncelle", desc: "Tüm programlar ve ADA" },
  { cmd: "update", fill: "/update", label: "Programları güncelle", desc: "Kurulu programları görüp seçerek güncelle" },
];

// Mesajın tamamı bu kelimelerden biriyse (dil fark etmeksizin) elle güncelleme penceresi açılır
const UPDATE_WORDS = new Set([
  "update", "updates", "upgrade", "/update", "/u",
  "güncelle", "guncelle", "güncelleme", "guncelleme", "güncellemeler",
  "aktualisieren", "aktualisierung", "aktualisierungen",
  "actualizar", "actualiza", "actualización", "actualizacion", "actualizaciones",
  "mettre à jour", "mettre a jour", "mise à jour", "mise a jour", "mises à jour", "maj",
  "aggiorna", "aggiornare", "aggiornamento", "aggiornamenti",
  "atualizar", "atualiza", "atualização", "atualizacao", "atualizações",
  "обновить", "обнови", "обновление", "обновления",
  "更新", "升级", "升級", "アップデート", "更新する", "アップグレード",
  "bijwerken", "aktualizuj", "aktualizacja", "تحديث", "업데이트", "अपडेट",
]);

export function isUpdateWord(text) {
  const t = (text || "").trim().replace(/[\s.!?。！？]+$/u, "").toLocaleLowerCase();
  return UPDATE_WORDS.has(t) || UPDATE_WORDS.has(t.toLowerCase());
}

/** "/d", "/d vlc", "/d update" → {action, query}; komut değilse null. */
export function parseSlash(text) {
  if (isUpdateWord(text)) return { action: "update", query: "", manual: true };
  const m = /^\s*\/d(?:\s+([\s\S]*))?$/i.exec(text || "");
  if (!m) return null;
  const rest = (m[1] || "").trim();
  if (/^(update|upgrade|güncelle|guncelle|u)$/i.test(rest)) return { action: "update", query: "" };
  return { action: "install", query: rest.slice(0, 80) };
}

export default function Composer({ onSend, onCommand, disabled, prefill, onPrefillUsed }) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState([]);
  const area = useRef(null);
  const [mic, setMic] = useState("idle"); // idle | rec | busy
  const recRef = useRef(null);

  const finishRecording = async () => {
    const rec = recRef.current;
    recRef.current = null;
    if (!rec) return;
    setMic("busy");
    try {
      const said = (await transcribe(await rec.stop())).trim();
      if (said) setText((prev) => (prev && !prev.endsWith(" ") ? prev + " " : prev) + said);
    } catch (e) {
      alert(e.message);
    } finally {
      setMic("idle");
      area.current?.focus();
    }
  };

  const toggleMic = async () => {
    if (mic === "busy") return;
    if (mic === "rec") return finishRecording();
    if (!recordingSupported()) {
      alert("Bu pencerede mikrofon kullanılamıyor. " + DICTATION_HINT);
      area.current?.focus();
      return;
    }
    try {
      const st = await api("/api/voice");
      if (!st.available) {
        area.current?.focus();
        if (st.dictation) {
          // Anahtar yok: Windows'un sesle yazma özelliği açılır, söylenenler doğrudan metin kutusuna yazılır
          try {
            await api("/api/voice/dictation", { method: "POST" });
            return;
          } catch {
            /* aşağıdaki açıklama gösterilir */
          }
        }
        alert("Konuşarak yazma için Ayarlar → Yapay zekâ bölümünden bir Gemini (ücretsiz), Groq veya OpenAI anahtarı girin. " + DICTATION_HINT);
        return;
      }
      recRef.current = await startRecording(finishRecording);
      setMic("rec");
    } catch (e) {
      recRef.current = null;
      setMic("idle");
      const denied = e?.name === "NotAllowedError" || e?.name === "SecurityError";
      const missing = e?.name === "NotFoundError";
      alert(denied ? "Mikrofon izni verilmedi. Windows Ayarlar → Gizlilik → Mikrofon bölümünden masaüstü uygulamalarına izin verin."
        : missing ? "Mikrofon bulunamadı." : e.message);
    }
  };

  useEffect(() => () => recRef.current?.cancel(), []);
  const fileInput = useRef(null);

  useEffect(() => {
    if (prefill) {
      setText(prefill);
      onPrefillUsed();
      area.current?.focus();
    }
  }, [prefill, onPrefillUsed]);

  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 220) + "px";
  }, [text]);

  const submit = async () => {
    const t = text.trim();
    if (!t) return;
    const slash = onCommand && parseSlash(t);
    if (slash) {
      onCommand(slash);
      setText("");
      return;
    }
    if (disabled) return;
    const ok = await onSend(t, attachments);
    if (ok !== false) {
      setText("");
      setAttachments([]);
    }
  };

  const pick = async (e) => {
    for (const f of e.target.files) {
      try {
        const item = await upload(f);
        setAttachments((prev) => [...prev, item]);
      } catch (err) {
        alert(err.message);
      }
    }
    e.target.value = "";
  };

  return (
    <footer>
      <div className="wrap">
        {attachments.length > 0 && (
          <div id="attachRow">
            {attachments.map((a, i) => (
              <span key={a.path} className="chip">
                <FileText size={13} /> {a.name}
                <button onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}>
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
        )}
        {onCommand && /^\/\S*$/.test(text) && !text.startsWith("/d ") && (
          <div className="slash-menu" role="listbox">
            {COMMANDS.filter((c) => c.fill.trim().startsWith(text.trim()) || text === "/").map((c) => (
              <button
                key={c.cmd}
                onMouseDown={(e) => {
                  e.preventDefault();
                  if (c.fill.endsWith(" ")) {
                    setText(c.fill);
                    area.current?.focus();
                  } else {
                    onCommand(parseSlash(c.fill));
                    setText("");
                  }
                }}
              >
                <code data-no-i18n>{c.cmd}</code>
                <b>{c.label}</b>
                <span>{c.desc}</span>
              </button>
            ))}
          </div>
        )}
        <div className="composer">
          <textarea
            ref={area}
            rows={1}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder="ADA'ya bir iş verin… (ör. 'Masaüstündeki faturaları tarayıp Excel'e dök')"
          />
          <div className="composer-bar">
            <button className="iconbtn" title="Dosya ekle" onClick={() => fileInput.current?.click()}>
              <Paperclip size={18} />
            </button>
            <input type="file" ref={fileInput} multiple hidden onChange={pick} />
            <button
              className={"iconbtn" + (mic === "rec" ? " rec" : mic === "busy" ? " busy" : "")}
              title={mic === "rec" ? "Bitirmek için tıklayın" : mic === "busy" ? "Yazıya çevriliyor…" : "Konuşarak yaz"}
              onClick={toggleMic}
              disabled={mic === "busy"}
            >
              {mic === "busy" ? <LoaderCircle size={18} className="spin" /> : <Mic size={18} />}
            </button>
            <span className="hint">Enter gönderir · Shift+Enter yeni satır · Fareyi sol üst köşeye götürmek ekran kontrolünü durdurur</span>
            <button id="sendBtn" onClick={submit} disabled={(disabled && !parseSlash(text)) || !text.trim()} title="Gönder">
              <ArrowUp size={18} strokeWidth={2.5} />
            </button>
          </div>
        </div>
      </div>
    </footer>
  );
}
