"""Beceriler: ADA'nın başarıyla yaptığı işlerin yeniden kullanılabilir tarifleri.

Her beceri workspace/.ada/skills/<slug>.json dosyasıdır. Beceri dizini (ad + ne zaman
kullanılır) her isteğin dinamik bağlamına eklenir; model uygun beceriyi `skill_use` ile açar.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime

from . import config

DIR = config.DATA_DIR / "skills"


def slugify(name: str) -> str:
    table = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    s = unicodedata.normalize("NFKD", name.translate(table)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "beceri"


def _path(slug: str):
    if not re.fullmatch(r"[a-z0-9\-]{1,60}", slug or ""):
        return None
    return DIR / f"{slug}.json"


def save(name: str, description: str, when_to_use: str, steps: str) -> dict:
    DIR.mkdir(parents=True, exist_ok=True)
    slug = slugify(name)
    p = _path(slug)
    existing = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    item = {
        "slug": slug,
        "name": name,
        "description": description,
        "when": when_to_use,
        "steps": steps,
        "created": existing.get("created") or datetime.now().isoformat(timespec="seconds"),
        "updated": datetime.now().isoformat(timespec="seconds"),
        "uses": existing.get("uses", 0),
        "version": existing.get("version", 0) + 1,
    }
    p.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    return item


def get(slug: str, count_use: bool = False) -> dict | None:
    p = _path(slug)
    if p is None or not p.exists():
        return None
    item = json.loads(p.read_text(encoding="utf-8"))
    if count_use:
        item["uses"] = item.get("uses", 0) + 1
        p.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    return item


def list_all() -> list[dict]:
    if not DIR.exists():
        return []
    items = [json.loads(p.read_text(encoding="utf-8")) for p in DIR.glob("*.json")]
    return sorted(items, key=lambda x: (-x.get("uses", 0), x["name"]))


def delete(slug: str) -> bool:
    p = _path(slug)
    if p is None or not p.exists():
        return False
    p.unlink()
    return True


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.casefold()) if len(w) > 2}


def search(query: str, limit: int = 3) -> list[dict]:
    q = _words(query)
    scored = []
    for s in list_all():
        head = _words(f"{s['name']} {s['description']} {s['when']}")
        body = _words(s["steps"])
        score = 3 * len(q & head) + len(q & body)
        if score:
            scored.append((score, s))
    return [s for _, s in sorted(scored, key=lambda x: -x[0])[:limit]]


def index_text(limit: int = 40) -> str:
    items = list_all()[:limit]
    if not items:
        return ""
    lines = [f"- {s['slug']}: {s['name']} — {s['when'][:140]}" for s in items]
    return "Kayıtlı becerilerin (uygun olanı skill_use ile aç ve adımlarını izle):\n" + "\n".join(lines)
