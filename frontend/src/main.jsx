import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

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

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
