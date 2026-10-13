"""Veri analizi için yerel Python çalıştırma."""

from __future__ import annotations

import os
import subprocess
import sys

from .. import config
from . import ToolResult, rel, tool

MAX_OUTPUT = 20_000


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n[... çıktı {len(text):,} karakter, kırpıldı]"


if config.ENABLE_PYTHON:

    @tool(
        "run_python",
        "Çalışma alanında Python kodu çalıştırır; veri analizi, hesaplama, dönüştürme ve "
        "özel grafikler için. pandas, numpy, matplotlib, openpyxl, python-docx, python-pptx, "
        "pypdf yüklüdür. Çalışma dizini workspace köküdür (uploads/ ve outputs/ burada); "
        "bilgisayardaki başka dosyalara mutlak yolla erişilebilir. Sonuçları print() ile "
        "yazdırın; ürettiğiniz dosyaları outputs/ altına kaydedin. Her çağrı ayrı bir süreçtir.",
        {"code": {"type": "string", "description": "Çalıştırılacak Python kodu"}},
        ["code"],
    )
    def run_python(code: str) -> ToolResult:
        before = {p: p.stat().st_mtime for p in config.OUTPUTS_DIR.rglob("*") if p.is_file()}
        try:
            proc = subprocess.run(
                # Paketlenmiş uygulamada ayrı bir python yoktur: uygulama kendisi kodu çalıştırır (desktop.main)
                [sys.executable, "--ada-run-python", code] if config.FROZEN else [sys.executable, "-c", code],
                cwd=config.WORKSPACE,
                capture_output=True,
                text=True,
                timeout=config.PYTHON_TIMEOUT,
                env={**os.environ, "MPLBACKEND": "Agg"},
            )
        except subprocess.TimeoutExpired:
            return ToolResult(f"Zaman aşımı: kod {config.PYTHON_TIMEOUT} saniyede bitmedi.", is_error=True)

        changed = [
            rel(p)
            for p in config.OUTPUTS_DIR.rglob("*")
            if p.is_file() and before.get(p) != p.stat().st_mtime
        ]
        out = f"çıkış kodu: {proc.returncode}\n"
        if proc.stdout:
            out += f"--- stdout ---\n{_clip(proc.stdout)}\n"
        if proc.stderr:
            out += f"--- stderr ---\n{_clip(proc.stderr)}\n"
        if changed:
            out += "Oluşturulan/güncellenen dosyalar: " + ", ".join(changed)
        return ToolResult(out, files=changed, is_error=proc.returncode != 0)
