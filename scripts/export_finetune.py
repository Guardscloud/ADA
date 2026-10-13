"""ADA'nın başarılı görevlerinden ince ayar (fine-tuning) veri seti çıkarır.

Her konuşma için kullanıcı isteği, ADA'nın adım adım araç kullanımı (araç adı + girdi özeti + sonuç
özeti) ve son yanıtı tek bir örnek olur. Hatayla biten, durdurulan veya reddedilen görevler dahil edilmez.
Çıktı: OpenAI-benzeri sohbet JSONL biçimi (çoğu açık model ince ayar aracının kabul ettiği format).

    uv run python scripts/export_finetune.py                     # geliştirme veri klasörü
    ADA_HOME=~/Library/Application\\ Support/ADA uv run python scripts/export_finetune.py   # uygulama verisi

Kişisel veri içerir (e-postalar, belgeler): paylaşmadan önce gözden geçirin.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ada import config, store  # noqa: E402
from ada.prompts import SYSTEM_PROMPT  # noqa: E402

BAD_MARKERS = ("_Görev kullanıcı tarafından durduruldu", "süre sınırına ulaştı", "API hatası", "kotası doldu",
               "güvenlik politikası", "Beklenmeyen hata", "Adım sınırına")


def examples():
    for item in store.list_all():
        conv = store.load(item["id"])
        if conv is None:
            continue
        ui = conv.ui
        for i in range(len(ui) - 1):
            user, assistant = ui[i], ui[i + 1]
            if user.get("role") != "user" or assistant.get("role") != "assistant":
                continue
            reply = (assistant.get("text") or "").strip()
            if not reply or any(m in reply for m in BAD_MARKERS):
                continue
            steps = [s for s in assistant.get("steps", []) if "(hata)" not in s.get("label", "")]
            trace = "\n".join(f"- {s['label']}: {s.get('detail', '')}" for s in steps)
            yield {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user["text"]},
                    {"role": "assistant", "content": (f"[Adımlar]\n{trace}\n\n" if trace else "") + reply},
                ],
                "meta": {"conversation": conv.id, "provider": conv.provider, "files": assistant.get("files", [])},
            }


def main() -> None:
    out = config.DATA_DIR / "finetune.jsonl"
    n = 0
    with out.open("w", encoding="utf-8") as f:
        for ex in examples():
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
            n += 1
    print(f"{n} örnek → {out}")
    if n < 200:
        print(f"Not: anlamlı bir ince ayar için genellikle birkaç yüz-bin iyi örnek gerekir (şu an {n}).")


if __name__ == "__main__":
    main()
