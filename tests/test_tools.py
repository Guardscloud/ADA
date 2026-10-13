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


# ---------------------------------------------------------------- güvenlik modu: güvenli / onayla her şey

def test_windows_shutdown_and_disk_commands_are_dangerous():
    from ada.tools import system

    for cmd in ("Stop-Computer -Force", "Restart-Computer", "shutdown /s /t 0", "Format-Volume -DriveLetter D", "format D: /q",
                "Clear-Disk -Number 1 -RemoveData", "diskpart /s x.txt", "Remove-Item C:/ -Recurse -Force", "rd /s /q C:/",
                "Remove-Item D: -Recurse", "bcdedit /delete {current}"):
        assert system.danger(cmd), cmd
    for cmd in ("Get-ChildItem C:/Users", "Remove-Item C:/Temp/eski.txt", "Get-Process", "echo format the report",
                "Write-Output 'Restart the service later'", "npm run format", "Remove-Item ./build -Recurse"):
        assert not system.danger(cmd), cmd


def _shell(command):
    import asyncio

    from ada import agent

    return asyncio.run(agent._run_tool("run_shell", {"command": command, "timeout": 5}))


def test_safe_mode_never_runs_dangerous_commands(monkeypatch):
    from ada import approval, config

    monkeypatch.delenv("ADA_SAFETY_MODE", raising=False)
    asked = []

    async def fake_request(title, detail, key=None):
        asked.append(title)
        return True

    monkeypatch.setattr(approval, "request", fake_request)
    assert config.safety_mode() == "safe"
    r = _shell("Stop-Computer -WhatIf")
    assert r.is_error and "güvenli modda" in r.content and "Onayla her şey" in r.content and not asked


def test_approve_mode_asks_every_time_and_runs_only_if_approved(monkeypatch):
    from ada import approval, config, runtime
    from ada.tools import system

    monkeypatch.setenv("ADA_SAFETY_MODE", "approve")
    monkeypatch.setattr(config, "AUTONOMY", "full")  # tam özerk modda bile sorulur
    asked, answer = [], [False]

    async def fake_request(title, detail, key=None):
        asked.append((title, detail, key))
        return answer[0]

    monkeypatch.setattr(approval, "request", fake_request)
    # -WhatIf: PowerShell komutu çalıştırmaz, yalnızca ne yapacağını yazar (bilgisayar kapanmaz)
    r = _shell("Stop-Computer -WhatIf")
    assert r.is_error and "onaylamadı" in r.content
    assert asked[0][0].startswith("Tehlikeli komut") and asked[0][1] == "Stop-Computer -WhatIf" and asked[0][2] is None

    answer[0] = True
    r = _shell("Stop-Computer -WhatIf")
    assert len(asked) == 2 and "güvenli modda" not in r.content and "onaylamadı" not in r.content
    assert system.DANGER_APPROVED.get() is False  # onay yalnızca o çağrı için geçerlidir

    # Başında kimse yokken (rutin, arka plan işi) onay modunda da çalıştırılmaz
    token = runtime.UNATTENDED.set(True)
    try:
        r = _shell("Stop-Computer -WhatIf")
        assert r.is_error and "güvenli modda" in r.content and len(asked) == 2
    finally:
        runtime.UNATTENDED.reset(token)

    # Tehlikeli olmayan komutlar ve başka araçlar etkilenmez
    assert not _shell("echo tamam").is_error
    assert len(asked) == 2
