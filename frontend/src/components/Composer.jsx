import { useEffect, useRef, useState } from "react";
import { ArrowUp, FileText, Mic, Paperclip, X } from "lucide-react";
import { upload } from "../api";
import { listen, recognitionSupported } from "../voice";

export default function Composer({ onSend, disabled, prefill, onPrefillUsed }) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState([]);
  const area = useRef(null);
  const [listening, setListening] = useState(false);
  const stopRef = useRef(null);

  const toggleMic = () => {
    if (listening) {
      stopRef.current?.();
      return;
    }
    if (!recognitionSupported()) {
      alert("Bu pencerede konuşarak yazma desteklenmiyor. macOS dikte özelliğini kullanın: metin kutusuna tıklayıp Fn (🌐) tuşuna iki kez basın.");
      area.current?.focus();
      return;
    }
    const base = text ? text + " " : "";
    setListening(true);
    stopRef.current = listen(
      (t) => setText(base + t),
      () => setListening(false),
    );
  };
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
    if (!t || disabled) return;
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
            <button className={"iconbtn" + (listening ? " rec" : "")} title="Konuşarak yaz" onClick={toggleMic}>
              <Mic size={18} />
            </button>
            <span className="hint">Enter gönderir · Shift+Enter yeni satır · Fareyi sol üst köşeye götürmek ekran kontrolünü durdurur</span>
            <button id="sendBtn" onClick={submit} disabled={disabled || !text.trim()} title="Gönder">
              <ArrowUp size={18} strokeWidth={2.5} />
            </button>
          </div>
        </div>
      </div>
    </footer>
  );
}
