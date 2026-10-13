// FastAPI arka ucuna ince bir istemci katmanı.
import { tr } from "./i18n";

export async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (r.status === 401) {
    location.href = "/login";
    throw new Error("Giriş gerekli");
  }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

export function fileUrl(path) {
  return "/api/files/" + path.split("/").map(encodeURIComponent).join("/");
}

export async function upload(file) {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch("/api/upload", { method: "POST", body: fd });
  if (!r.ok) throw new Error("Yüklenemedi: " + file.name);
  return r.json();
}

/** Dosyayı verilen uca `file` alanıyla gönderir (ör. /api/routines/upload); hata mesajı sunucudan gelir. */
export async function uploadTo(path, file) {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(path, { method: "POST", body: fd });
  if (r.status === 401) {
    location.href = "/login";
    throw new Error("Giriş gerekli");
  }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Yüklenemedi: " + file.name);
  return r.json();
}

/**
 * Sunucudaki olay akışını (SSE) okur. Bağlantı koparsa kaldığı `offset`'ten devam eder;
 * `done` olayı gelince veya `isCurrent()` false dönünce biter.
 */
export async function followStream(id, onEvent, isCurrent) {
  let offset = 0;
  while (true) {
    const r = await fetch(`/api/conversations/${id}/stream?offset=${offset}`);
    if (r.status === 401) {
      location.href = "/login";
      return;
    }
    if (!r.ok) return;
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let finished = false;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        if (!chunk.startsWith("data: ")) continue;
        offset++;
        const ev = JSON.parse(chunk.slice(6));
        if (ev.type === "done") finished = true;
        else if (isCurrent()) onEvent(ev);
      }
    }
    if (finished || !isCurrent()) return;
    await new Promise((res) => setTimeout(res, 1000));
  }
}

export function notify(message) {
  if (!("Notification" in window)) return;
  if (Notification.permission === "granted") new Notification("ADA", { body: tr(message) });
  else if (Notification.permission !== "denied") Notification.requestPermission();
}
