// Arayüz kaynaklarındaki Türkçe metinleri çıkarır (çeviri kataloğu için). scripts/i18n_extract.py çağırır.
// Çıktı (stdout): JSON dizi. Değişken içeren metinler "{0}", "{1}" yer tutuculu kalıp olarak yazılır.
import fs from "node:fs";
import path from "node:path";
import { parse } from "@babel/parser";
import _traverse from "@babel/traverse";

const traverse = _traverse.default || _traverse;
const SRC = path.join(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1")), "src");
const out = new Set();

// Metin olmayan özellikler (sınıf adı, adres, tür…)
const SKIP_ATTRS = new Set(["className", "id", "type", "key", "href", "src", "rel", "target", "name", "role", "autoComplete", "method", "htmlFor", "style", "value", "d", "viewBox"]);
const LETTER = /\p{L}/u;
const TURKISH = /[çğıöşüÇĞİÖŞÜ]/;

function jsxText(raw) {
  // JSX boşluk kuralı: satırlar kırpılır, boş olmayanlar tek boşlukla birleşir
  const lines = raw.split(/\r?\n/);
  const parts = lines.map((l, i) => {
    let s = l.replace(/\t/g, " ");
    if (i !== 0) s = s.replace(/^ +/, "");
    if (i !== lines.length - 1) s = s.replace(/ +$/, "");
    return s;
  }).filter((s) => s);
  return parts.join(" ");
}

function add(text) {
  const t = String(text).trim();
  if (t.length < 2 || t.length > 400 || !LETTER.test(t)) return;
  out.add(t);
}

function looksLikeText(s) {
  if (!LETTER.test(s)) return false;
  if (TURKISH.test(s)) return true;
  if (/^[a-z0-9_.:/#?&=%-]+$/.test(s)) return false; // kimlik, yol, sınıf adı
  if (/^(https?:|\/|\.|#)/.test(s)) return false;
  return /\s/.test(s.trim()) || /^[A-ZİÇĞÖŞÜ][a-zçğıöşü]+/.test(s);
}

// "a" + x + "b" ya da `a ${x} b` → "a {0} b"
function pattern(node) {
  const parts = [];
  let n = 0;
  let hasText = false;
  const walk = (e) => {
    if (e.type === "StringLiteral") {
      parts.push(e.value);
      if (LETTER.test(e.value)) hasText = true;
    } else if (e.type === "TemplateLiteral") {
      e.quasis.forEach((q, i) => {
        parts.push(q.value.cooked ?? "");
        if (LETTER.test(q.value.cooked ?? "")) hasText = true;
        if (i < e.expressions.length) parts.push(`{${n++}}`);
      });
    } else if (e.type === "BinaryExpression" && e.operator === "+") {
      walk(e.left);
      walk(e.right);
    } else {
      parts.push(`{${n++}}`);
    }
  };
  walk(node);
  return hasText && n > 0 ? parts.join("") : null;
}

function files(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) =>
    d.isDirectory() ? files(path.join(dir, d.name)) : /\.(jsx|js)$/.test(d.name) ? [path.join(dir, d.name)] : []);
}

for (const file of files(SRC)) {
  if (/i18n\.js$/.test(file)) continue;
  const ast = parse(fs.readFileSync(file, "utf8"), { sourceType: "module", plugins: ["jsx"] });
  traverse(ast, {
    JSXText(p) {
      add(jsxText(p.node.value));
    },
    StringLiteral(p) {
      const parent = p.parent;
      if (parent.type === "ImportDeclaration" || parent.type === "ExportNamedDeclaration") return;
      if (parent.type === "JSXAttribute" && SKIP_ATTRS.has(parent.name.name)) return;
      if (parent.type === "ObjectProperty" && parent.key === p.node && !parent.computed) return;
      if (parent.type === "MemberExpression") return;
      if (parent.type === "BinaryExpression" && ["===", "!==", "==", "!="].includes(parent.operator)) return;
      if (parent.type === "CallExpression" && ["getItem", "setItem", "api", "post", "fetch", "addEventListener", "removeEventListener", "includes", "startsWith", "endsWith", "split", "join", "replace", "querySelector"].includes(parent.callee.property?.name || parent.callee.name)) return;
      if (looksLikeText(p.node.value)) add(p.node.value);
    },
    TemplateLiteral(p) {
      if (p.parent.type === "TaggedTemplateExpression") return;
      if (p.parent.type === "CallExpression" && ["api", "post", "fetch"].includes(p.parent.callee.name)) return;
      if (p.parent.type === "BinaryExpression" && p.parent.operator === "+") return; // üst düğümde ele alınır
      if (!p.node.expressions.length) {
        const s = p.node.quasis[0].value.cooked ?? "";
        if (looksLikeText(s)) add(s);
        return;
      }
      const pat = pattern(p.node);
      if (pat && looksLikeText(pat.replace(/\{\d+\}/g, ""))) add(pat);
    },
    BinaryExpression(p) {
      if (p.node.operator !== "+" || (p.parent.type === "BinaryExpression" && p.parent.operator === "+")) return;
      const pat = pattern(p.node);
      if (pat && looksLikeText(pat.replace(/\{\d+\}/g, ""))) add(pat);
    },
  });
}

process.stdout.write(JSON.stringify([...out]));
