// Sesli kullanım: konuşarak yazma ve yanıtları sesli okuma.
//
// ADA penceresi (Windows'ta WebView2) tarayıcının konuşma tanımasını kullanamaz: o özellik sesi Google'ın
// sunucusuna gönderir ve gömülü pencerede "network" hatası verir. Bu yüzden ses burada kaydedilir,
// 16 kHz mono WAV'a çevrilir ve ADA'nın sunucusunda yazıya çevrilir (bkz. ada/transcribe.py).

const MAX_SECONDS = 120;

export function recordingSupported() {
  return !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder && window.AudioContext);
}

/**
 * Mikrofon kaydını başlatır. Döner: { stop() → Promise<Blob (WAV)>, cancel() }.
 * Süre sınırı dolunca onLimit çağrılır (çağıran stop() ile bitirir).
 */
export async function startRecording(onLimit) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  const rec = new MediaRecorder(stream);
  const chunks = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  const stopped = new Promise((res) => (rec.onstop = res));
  rec.start();
  const timer = setTimeout(() => onLimit?.(), MAX_SECONDS * 1000);
  const release = () => {
    clearTimeout(timer);
    stream.getTracks().forEach((t) => t.stop());
  };
  return {
    async stop() {
      if (rec.state !== "inactive") rec.stop();
      await stopped;
      release();
      return toWav(new Blob(chunks, { type: rec.mimeType || "audio/webm" }));
    },
    cancel() {
      if (rec.state !== "inactive") rec.stop();
      release();
    },
  };
}

async function toWav(blob) {
  const ctx = new AudioContext();
  let decoded;
  try {
    decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
  } finally {
    ctx.close();
  }
  const rate = 16000;
  const offline = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * rate)), rate);
  const src = offline.createBufferSource();
  src.buffer = decoded;
  src.connect(offline.destination);
  src.start();
  const pcm = (await offline.startRendering()).getChannelData(0);
  const buf = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const str = (o, s) => [...s].forEach((c, i) => buf.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  buf.setUint32(4, 36 + pcm.length * 2, true);
  str(8, "WAVEfmt ");
  buf.setUint32(16, 16, true);
  buf.setUint16(20, 1, true); // PCM
  buf.setUint16(22, 1, true); // mono
  buf.setUint32(24, rate, true);
  buf.setUint32(28, rate * 2, true);
  buf.setUint16(32, 2, true);
  buf.setUint16(34, 16, true);
  str(36, "data");
  buf.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) {
    const s = Math.max(-1, Math.min(1, pcm[i]));
    buf.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}

/** Kaydı sunucuda yazıya çevirir. */
export async function transcribe(wav) {
  const fd = new FormData();
  fd.append("file", wav, "ses.wav");
  const r = await fetch("/api/voice/transcribe", { method: "POST", body: fd });
  if (r.status === 401) {
    location.href = "/login";
    throw new Error("Giriş gerekli");
  }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Ses yazıya çevrilemedi.");
  return (await r.json()).text || "";
}

/** Markdown'u sadeleştirip ilk birkaç cümleyi Türkçe sesle okur. */
export function speak(markdown) {
  if (!("speechSynthesis" in window) || !markdown) return;
  const plain = markdown
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[#*_`>|]/g, " ")
    .replace(/\[(.*?)\]\(.*?\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 400);
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(plain);
  u.lang = "tr-TR";
  const tr = window.speechSynthesis.getVoices().find((v) => v.lang?.startsWith("tr"));
  if (tr) u.voice = tr;
  window.speechSynthesis.speak(u);
}
