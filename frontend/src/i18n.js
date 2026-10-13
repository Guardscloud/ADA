// Arayüz çevirisi. Bileşenler Türkçe yazılır; seçilen dil Türkçe değilse ekrandaki metinler sözlükten
// (public/assets/i18n/<dil>.json: {"Türkçe metin": "çeviri"}) çevrilir. Sözlükte olmayan metin Türkçe kalır.
//
// Çeviri DOM düzeyinde yapılır (metin düğümleri ve title/placeholder gibi özellikler), böylece bileşenlere
// dokunmak gerekmez. Yalnızca düğümün metni değiştirilir, yapı değiştirilmez: React'in ağacı bozulmaz.
// Sözlük anahtarları scripts/i18n_extract.py ile kaynaklardan çıkarılır; "{0}" içerenler değişkenli kalıplardır.

export const LANGS = {
  tr: "Türkçe", en: "English", de: "Deutsch", fr: "Français", es: "Español", pt: "Português",
  it: "Italiano", ru: "Русский", zh: "中文", ja: "日本語",
};

// Kullanıcı içeriği çevrilmez: sohbet metni, kod, terminal, düzenleme alanları, sürüm notları
const SKIP = ".bubble, .think > div, pre, code, textarea, .xterm, script, style, .update-notes, .checklist, [data-no-i18n]";
const ATTRS = ["placeholder", "title", "alt", "aria-label"];

let lang = "tr";
let setting = "auto";
let dict = null;
let patterns = [];
const cache = new Map();
const lastText = new WeakMap(); // düğüm → bizim yazdığımız metin (kendi değişikliğimizi yeniden çevirmemek için)
const lastAttr = new WeakMap();

export const currentLang = () => lang;
export const langSetting = () => setting;

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function compile(source) {
  patterns = [];
  for (const [key, value] of Object.entries(source)) {
    if (!key.includes("{0}")) continue;
    const literal = key.replace(/\{\d+\}/g, "");
    if ((literal.match(/\p{L}/gu) || []).length < 3) continue; // "{0}: {1}" gibi her şeye uyan kalıplar alınmaz
    const order = [];
    const re = new RegExp(
      "^" + key.split(/(\{\d+\})/).map((p) => {
        const m = /^\{(\d+)\}$/.exec(p);
        if (!m) return escapeRe(p);
        order.push(Number(m[1]));
        return "([\\s\\S]+?)";
      }).join("") + "$",
    );
    patterns.push({ re, value, order, weight: literal.length });
  }
  patterns.sort((a, b) => b.weight - a.weight); // en belirgin kalıp önce
}

/** Bir metni çevirir; sözlükte yoksa aynen döner. */
export function tr(text) {
  if (!dict || typeof text !== "string") return text;
  const core = text.trim();
  if (!core) return text;
  let out = cache.get(core);
  if (out === undefined) {
    out = dict[core] ?? null;
    if (out === null && core.length <= 300) {
      for (const p of patterns) {
        const m = p.re.exec(core);
        if (!m) continue;
        out = p.value.replace(/\{(\d+)\}/g, (_, n) => {
          const part = m[p.order.indexOf(Number(n)) + 1] ?? "";
          return dict[part.trim()] ?? part; // değerin kendisi de bir arayüz metni olabilir ("çalışıyor")
        });
        break;
      }
    }
    if (cache.size > 5000) cache.clear();
    cache.set(core, out);
  }
  if (out === null) return text;
  const start = text.indexOf(core);
  return text.slice(0, start) + out + text.slice(start + core.length);
}

function textNode(node) {
  const value = node.nodeValue;
  if (!value || lastText.get(node) === value) return;
  const parent = node.parentElement;
  if (!parent || parent.closest(SKIP)) return;
  const out = tr(value);
  if (out !== value) {
    lastText.set(node, out);
    node.nodeValue = out;
  }
}

function attribute(el, name) {
  const value = el.getAttribute(name);
  if (!value) return;
  const mine = lastAttr.get(el);
  // Yazı alanının kendi içeriği çevrilmez ama ipucu metni (placeholder) çevrilir
  if (mine?.[name] === value || el.closest(".xterm, [data-no-i18n]")) return;
  const out = tr(value);
  if (out !== value) {
    lastAttr.set(el, { ...mine, [name]: out });
    el.setAttribute(name, out);
  }
}

function walk(root) {
  if (root.nodeType === Node.TEXT_NODE) return textNode(root);
  if (root.nodeType !== Node.ELEMENT_NODE) return;
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  for (let n = root; n; n = w.nextNode()) {
    if (n.nodeType === Node.TEXT_NODE) textNode(n);
    else for (const a of ATTRS) if (n.hasAttribute(a)) attribute(n, a);
  }
}

function watch() {
  walk(document.body);
  new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.type === "characterData") textNode(m.target);
      else if (m.type === "attributes") attribute(m.target, m.attributeName);
      else m.addedNodes.forEach(walk);
    }
  }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });

  // Tarayıcı pencereleri (uyarı, onay, soru) de çevrilir
  for (const name of ["alert", "confirm", "prompt"]) {
    const original = window[name].bind(window);
    window[name] = (message, ...rest) => original(tr(String(message ?? "")), ...rest);
  }
}

/** Dili belirler ve sözlüğü yükler; arayüz çizilmeden önce çağrılır. */
export async function initI18n() {
  try {
    const status = await fetch("/api/status").then((r) => (r.ok ? r.json() : {}));
    setting = status.lang || "auto";
  } catch {
    setting = "auto";
  }
  // Otomatik: sistem dili desteklenmiyorsa Türkçe bilmeyen kullanıcı için İngilizce
  const system = (navigator.language || "tr").slice(0, 2).toLowerCase();
  lang = setting !== "auto" && LANGS[setting] ? setting : LANGS[system] ? system : "en";
  document.documentElement.lang = lang;
  if (lang === "tr") return;
  try {
    const r = await fetch(`/assets/i18n/${lang}.json`);
    if (!r.ok) return;
    dict = await r.json();
    compile(dict);
    watch();
  } catch {
    dict = null; // sözlük yüklenemezse arayüz Türkçe kalır
  }
}

/** Dili kaydeder ve arayüzü yeni dille yeniden yükler. */
export async function setLang(code) {
  await fetch("/api/lang", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lang: code }) });
  location.reload();
}
