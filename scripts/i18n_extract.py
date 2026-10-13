"""Çeviri kataloğunu üretir: arayüzde görünen Türkçe metinlerin listesi.

    uv run python scripts/i18n_extract.py          # frontend/public/assets/i18n/catalog.json yazar
    uv run python scripts/i18n_extract.py --check  # her dil sözlüğünde eksik/fazla anahtarları raporlar

Kaynaklar: arayüz bileşenleri (frontend/i18n-extract.mjs, Babel ile) ve sunucunun arayüze gönderdiği metinler
(hata iletileri, ayar adları, adım etiketleri…). Değişken içeren metinler "{0}", "{1}" yer tutuculu kalıptır.
Sözlükler: frontend/public/assets/i18n/<dil>.json  ({"Türkçe metin": "çeviri"}).
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "public" / "assets" / "i18n"
TURKISH = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
LETTER = re.compile(r"[^\W\d_]")
# Modele giden metinler (istemler, araç açıklamaları) arayüzde görünmez
SKIP_FILES = {"prompts.py", "injection.py", "maintenance.py", "notify.py", "permissions.py", "desktop.py", "__init__.py"}
# agent.py'den yalnızca adım etiketleri alınır
ONLY_NAMES = {"agent.py": {"TOOL_LABELS", "ROLE_NAMES"}}


def _joined(node: ast.JoinedStr) -> str | None:
    parts, n = [], 0
    for v in node.values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            parts.append(v.value)
        else:
            parts.append(f"{{{n}}}")
            n += 1
    return "".join(parts) if n else None


def _wanted(s: str, forced: bool) -> bool:
    s = s.strip()
    if not (2 <= len(s) <= 220) or not LETTER.search(s) or "\n" in s:
        return False
    plain = re.sub(r"\{\d+\}", "", s)
    if TURKISH.search(plain):
        return True
    return forced and bool(re.search(r"\s", plain.strip()))


def from_python() -> set[str]:
    found: set[str] = set()
    for path in sorted((ROOT / "ada").glob("*.py")):
        if path.name in SKIP_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstrings.add(id(first.value))
        skip: set[int] = set()   # re.compile(...) ve print(...) içindekiler
        forced: set[int] = set()  # raise X("...") / HTTPException(…, "...")
        scope: set[int] | None = None
        only = ONLY_NAMES.get(path.name)
        if only is not None:
            scope = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in only for t in node.targets):
                    scope.update(id(n) for n in ast.walk(node.value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", "")
                inner = {id(n) for a in list(node.args) + [k.value for k in node.keywords] for n in ast.walk(a)}
                if name in ("compile", "search", "match", "sub", "fullmatch", "findall", "print", "getenv", "get", "log", "startswith", "endswith"):
                    skip |= inner
                elif name in ("HTTPException", "ValueError", "RuntimeError", "ToolError", "UpdateError"):
                    forced |= inner
        for node in ast.walk(tree):
            if id(node) in skip or id(node) in docstrings or (scope is not None and id(node) not in scope):
                continue
            text = None
            if isinstance(node, ast.JoinedStr):
                text = _joined(node)
                skip |= {id(v) for v in node.values}
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            if text and _wanted(text, id(node) in forced):
                found.add(text.strip())
    return found


def from_frontend() -> set[str]:
    r = subprocess.run(["node", "i18n-extract.mjs"], cwd=ROOT / "frontend", capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit("Arayüz metinleri çıkarılamadı:\n" + r.stderr)
    return set(json.loads(r.stdout))


# Çıkarıcının yakalayamadığı arayüz metinleri (elle eklenir) ve çevrilmeyecek olanlar (dil seçici)
EXTRA = {"E-posta", "{0} motor", "Tehlikeli komut: bilgisayarı kapatabilir ya da verileri kalıcı olarak silebilir"}
IGNORE = {"Arayüz dili", "Auto (", "Language", "Language / Dil", "Türkçe", "Français", "ıİğĞüÜşŞöÖçÇâÂîÎûÛáàãäéèêëíìïóòõôúùñ"}


def catalog() -> list[str]:
    return sorted((from_frontend() | from_python() | EXTRA) - IGNORE, key=lambda s: (s.casefold(), s))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    keys = catalog()
    if "--check" in sys.argv:
        for path in sorted(OUT.glob("*.json")):
            if path.name == "catalog.json":
                continue
            d = json.loads(path.read_text(encoding="utf-8"))
            missing = [k for k in keys if k not in d]
            bad = [k for k, v in d.items() if sorted(re.findall(r"\{\d+\}", k)) != sorted(re.findall(r"\{\d+\}", v))]
            print(f"{path.stem}: {len(d)} çeviri, {len(missing)} eksik, {len(bad)} yer tutucusu uyumsuz")
        return
    if "--merge" in sys.argv:
        # parts/<dil>-N.json parçalarını (çevirmenlerin çıktısı) <dil>.json sözlüğüne birleştirir
        langs = sorted({p.name.rsplit("-", 1)[0] for p in (OUT / "parts").glob("*-[0-9].json") if not p.name.startswith("kaynak")})
        for lang in langs:
            path = OUT / f"{lang}.json"
            d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            for part in sorted((OUT / "parts").glob(f"{lang}-[0-9].json")):
                d.update(json.loads(part.read_text(encoding="utf-8")))
            ek = OUT / "parts" / "ek.json"  # elle eklenen metinler: {"metin": {"dil": "çeviri"}}
            if ek.exists():
                d.update({k: v[lang] for k, v in json.loads(ek.read_text(encoding="utf-8")).items() if lang in v})
            d = {k: d[k] for k in keys if str(d.get(k, "")).strip()}
            path.write_text(json.dumps(d, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
            print(f"{lang}: {len(d)}/{len(keys)}")
        return
    (OUT / "catalog.json").write_text(json.dumps(keys, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{len(keys)} metin → {OUT / 'catalog.json'}")


if __name__ == "__main__":
    main()
