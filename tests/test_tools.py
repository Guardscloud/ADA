"""Yerel araçlar: ofis belgeleri, dosya sistemi, güvenlik filtreleri, beceriler, zamanlayıcı."""

from __future__ import annotations

from datetime import datetime

import pytest

from ada import config, scheduler, skills
from ada.tools import REGISTRY, ToolError, safe_path, validate_input


def run(tool_name, **kw):
    return REGISTRY[tool_name].handler(**kw)


def test_word_excel_pptx_chart_roundtrip():
    run("create_word_document", filename="r", title="Rapor", markdown="# Özet\n- **kalın** madde\n| A | B |\n|---|---|\n| 1 | 2 |")
    run("create_chart", filename="g", chart_type="bar", title="T", labels=["a", "b"], series=[{"name": "s", "values": [1, 2]}])
    run("create_presentation", filename="s", title="Sunum", slides=[{"title": "Bir", "bullets": ["x"], "image": "outputs/g.png"}])
    run("create_excel", filename="b", sheets=[{"name": "S", "rows": [["K", "Ay1"], ["M", 1], ["Toplam", "=SUM(B2:B2)"]]}])
    assert "Özet" in run("read_document", path="outputs/r.docx")
    assert "Bir" in run("read_document", path="outputs/s.pptx")
    xl = run("read_document", path="outputs/b.xlsx")
    assert "S!B3: =SUM(B2:B2)" in xl  # formüller NaN yerine listelenir


def test_excel_review_flags_shifted_formulas():
    bad = run("create_excel", filename="k", sheets=[{"name": "S", "rows": [["K", "Ay1", "Ay2"], ["M", 1, 2], ["Toplam", "=SUM(C2:C2)", "=SUM(C2:C2)"]]}])
    assert "S!B3" in bad.content and "kayma" in bad.content
    assert "S!C3" not in bad.content.split("FORMÜL KONTROLÜ")[1]  # doğru formül uyarı almaz


def test_workspace_paths_are_confined():
    for bad in ("../../etc/passwd", ".ada/memory.json"):
        with pytest.raises(ToolError):
            safe_path(bad)


def test_shell_hard_block():
    for cmd in ("rm -rf /", "rm -rf ~", "rm -rf ~/", "sudo rm -rf / --no-preserve-root", "rm -r /Users/someone", "shutdown -h now"):
        with pytest.raises(ToolError):
            run("run_shell", command=cmd, timeout=5)
    assert "çıkış kodu: 0" in run("run_shell", command="echo tamam", timeout=10).content


def test_shell_risk_detection():
    risk = REGISTRY["run_shell"].risk
    assert risk({"command": "sudo ls"})
    assert risk({"command": "rm -rf ./build"})
    assert risk({"command": "ls -la"}) is None


def test_validate_input():
    t = REGISTRY["read_document"]
    assert validate_input(t, {}) == "Zorunlu alan eksik: path"
    assert "string" in validate_input(t, {"path": 3})
    assert "Bilinmeyen alan" in validate_input(t, {"path": "x", "foo": 1})
    assert validate_input(t, {"path": "x"}) is None


def test_skills_save_search_index():
    run("skill_save", name="Aylık Satış Raporu", description="Excel'den rapor", when_to_use="aylık satış raporu", steps="1. oku")
    assert "aylik-satis-raporu" in run("skill_search", query="satış raporu hazırla")
    assert "sürüm 1" in run("skill_use", slug="aylik-satis-raporu")
    assert skills.get("aylik-satis-raporu")["uses"] == 1
    assert "aylik-satis-raporu" in skills.index_text()


def test_scheduler_next_run():
    now = datetime(2026, 9, 30, 10, 0)  # Çarşamba
    assert scheduler.compute_next({"type": "daily", "time": "08:30"}, now) == datetime(2026, 10, 1, 8, 30)
    assert scheduler.compute_next({"type": "daily", "time": "08:30", "weekdays": [0]}, now) == datetime(2026, 10, 5, 8, 30)
    assert scheduler.compute_next({"type": "once", "at": "2026-09-30T09:00"}, now) is None


def test_memory_digest_in_context():
    from ada import agent

    run("memory_save", key="sirket", content="ACME A.Ş.")
    assert "ACME" in agent._context_block()["text"]


def test_workspace_is_temporary():
    assert "ada-test-" in str(config.WORKSPACE)


def test_pdf_create_merge_extract():
    run("create_chart", filename="g", chart_type="line", title="T", labels=["a", "b"], series=[{"name": "s", "values": [1, 2]}])
    md = "# Başlık ğüşıöç\n**Kalın** ve *italik* metin.\n- madde 1\n- madde 2\n1. bir\n2. iki\n| Ad | Değer |\n|---|---|\n| İstanbul | 15,6 |\n![Grafik](outputs/g.png)\n---\n## İkinci sayfa\nSon."
    run("create_pdf", filename="rapor", title="Aylık Rapor", markdown=md)
    txt = run("read_document", path="outputs/rapor.pdf")
    assert "ğüşıöç" in txt and "İstanbul" in txt and "Sayfa 2" in txt
    run("pdf_merge", paths=["outputs/rapor.pdf", "outputs/rapor.pdf"], filename="birlesik")
    from pypdf import PdfReader

    assert len(PdfReader(str(config.OUTPUTS_DIR / "birlesik.pdf")).pages) == 4
    run("pdf_extract_pages", path="outputs/birlesik.pdf", pages="2-3", filename="parca")
    assert len(PdfReader(str(config.OUTPUTS_DIR / "parca.pdf")).pages) == 2
    with pytest.raises(ToolError):
        run("pdf_extract_pages", path="outputs/birlesik.pdf", pages="9", filename="x")


def test_pdf_lists_do_not_merge():
    run("create_pdf", filename="liste", markdown="1. bir\n2. iki\n- madde")
    txt = run("read_document", path="outputs/liste.pdf")
    assert "3." not in txt and "madde" in txt


def test_sensitive_paths_and_disk_scans_need_approval():
    from ada.tools.system import _shell_risk, sensitive_path

    assert _shell_risk({"command": "find / -name '*.xlsx'"})
    assert _shell_risk({"command": "cat ~/.ssh/id_rsa"})
    assert _shell_risk({"command": "mdfind -name satis"}) is None
    assert sensitive_path("~/Library/Mail")
    assert sensitive_path("uploads/x.pdf") is None  # kendi çalışma alanı serbest
    assert REGISTRY["read_document"].risk({"path": "~/.aws/credentials"})


def test_date_context_names_last_month():
    from ada import agent

    text = agent._date_block()["text"]
    assert "Geçen ay:" in text and "Bu çeyrek:" in text
