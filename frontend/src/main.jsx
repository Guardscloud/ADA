import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
import { initI18n } from "./i18n";
import { api } from "./api";

// Arayüzde yakalanmamış hatalar ADA'nın işlem günlüğüne yazılır (uygulama penceresinde konsol görünmez)
function report(kind, message, detail) {
  try {
    fetch("/api/client-error", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, message: String(message).slice(0, 500), detail: String(detail || "").slice(0, 1500) }),
    }).catch(() => {});
  } catch {
    /* yoksay */
  }
}
window.addEventListener("error", (e) => report("error", e.message, e.error?.stack || `${e.filename}:${e.lineno}`));
window.addEventListener("unhandledrejection", (e) => report("promise", e.reason?.message || e.reason, e.reason?.stack));

// Uygulama penceresinde dış linkler pencerenin kendisini siteye götürmesin (geri dönülemez, Tor'u da atlar):
// ADA linki dışarıda açar, Tor açıksa Tor üzerinden. Tarayıcıdan uzaktan erişimde linkler olağan açılır.
document.addEventListener(
  "click",
  (e) => {
    if (!("pywebview" in window) || e.defaultPrevented || e.button !== 0) return;
    const a = e.target.closest?.("a[href]");
    if (!a || a.hasAttribute("download")) return;
    let u;
    try {
      u = new URL(a.href, location.href);
    } catch {
      return;
    }
    if (!/^https?:$/.test(u.protocol) || u.origin === location.origin) return;
    e.preventDefault();
    api("/api/open-url", { method: "POST", body: JSON.stringify({ url: u.href }) }).catch((err) => alert(err.message));
  },
  true,
);

// Önce dil sözlüğü yüklenir ki arayüz ilk çizimde doğru dilde görünsün
initI18n().finally(() =>
  createRoot(document.getElementById("root")).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  ),
);
