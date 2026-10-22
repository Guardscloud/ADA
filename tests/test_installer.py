"""Kurulum merkezi (/d komutu): winget çıktısı ayrıştırma, komut çözümü, hata açıklamaları, iş akışı.

Gerçek winget hiç çalıştırılmaz: `_winget` sahte bir işlevle değiştirilir.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from ada import installer

SEARCH_OUT = """\r   - \r   \\ \r
Name                               Id                        Version         Match
------------------------------------------------------------------------------------------
7-Zip                              7zip.7zip                 26.05           Moniker: 7zip
NanaZip                            M2Team.NanaZip            7.0.1843.0      Tag: 7zip
7zr                                7zip.7zr                  26.05
"""

UPGRADE_OUT = """Name                                                   Id                                     Version                 Available
------------------------------------------------------------------------------------------------------------------------------------
GitHub CLI                                             GitHub.cli                             2.102.0                 2.103.0
Microsoft .NET Runtime - 8.0.31 (x64)                  Microsoft.DotNet.Runtime.8             8.0.31                  8.0.32
2 upgrades available.

The following packages have an upgrade available, but require explicit targeting for upgrade:
Name   Id           Version Available
--------------------------------------
Pinned Foo.Pinned   1.0     2.0
"""


def test_parse_search_table():
    rows = installer.parse_table(SEARCH_OUT)
    assert [r["id"] for r in rows] == ["7zip.7zip", "M2Team.NanaZip", "7zip.7zr"]
    assert rows[0]["name"] == "7-Zip" and rows[0]["version"] == "26.05" and rows[0]["match"] == "Moniker: 7zip"


def test_parse_upgrade_table_skips_summary_and_reads_second_table():
    rows = installer.parse_table(UPGRADE_OUT)
    ids = [r["id"] for r in rows]
    assert ids == ["GitHub.cli", "Microsoft.DotNet.Runtime.8", "Foo.Pinned"]
    assert rows[1]["name"] == "Microsoft .NET Runtime - 8.0.31 (x64)" and rows[1]["available"] == "8.0.32"


def test_clean_drops_spinner_and_progress():
    lines = installer._clean("  -\r  \\\r  ██████▒▒▒  10 MB / 20 MB\nSuccessfully installed\n")
    assert lines == ["Successfully installed"]


@pytest.mark.parametrize("text,expected", [
    ("/d", {"action": "install", "query": ""}),
    ("  /D  ", {"action": "install", "query": ""}),
    ("/d vlc player", {"action": "install", "query": "vlc player"}),
    ("/d update", {"action": "update", "query": ""}),
    ("/d Güncelle", {"action": "update", "query": ""}),
    ("/download x", None),
    ("merhaba /d", None),
])
def test_parse_command(text, expected):
    assert installer.parse_command(text) == expected


def test_explain_codes():
    assert installer.explain(0, "Successfully installed", "install") == ("ok", "")
    assert installer.explain(0, "done", "upgrade") == ("updated", "")
    assert installer.explain(0x8A15002B, "", "install")[0] == "current"
    assert installer.explain(0x8A150109, "", "install")[0] == "reboot"
    assert installer.explain(0, "No available upgrade found.", "upgrade")[0] == "current"
    state, msg = installer.explain(0x8A150107, "", "install")
    assert state == "fail" and "İnternet" in msg
    state, msg = installer.explain(0x8A150011, "Installer failed with exit code: 1223", "install")
    assert state == "fail" and "Evet" in msg
    state, msg = installer.explain(0x8A15FFFF, "garip hata", "install")
    assert state == "fail" and "0x8A15FFFF" in msg


def test_valid_id():
    assert installer.valid_id("Notepad++.Notepad++") and installer.valid_id("Adobe.Acrobat.Reader.64-bit")
    assert not installer.valid_id("a; rm -rf /") and not installer.valid_id("") and not installer.valid_id("x y")


@pytest.fixture
def fake_winget(monkeypatch):
    calls: list[list[str]] = []
    codes: dict[str, int] = {}

    async def fake(args, timeout=0, on_line=None):
        calls.append(args)
        await asyncio.sleep(0)
        if args[0] == "upgrade" and "--id" not in args:
            return 0, UPGRADE_OUT
        if args[0] == "list":
            return 0, SEARCH_OUT
        pid = args[args.index("--id") + 1]
        if on_line:
            on_line(f"Installing {pid}")
        return codes.get(pid, 0), "Successfully installed"

    monkeypatch.setattr(installer, "_winget", fake)
    monkeypatch.setattr(installer, "winget_path", lambda: "winget.exe")
    monkeypatch.setattr(installer, "JOB", None)
    monkeypatch.setattr(installer, "_installed_cache", (0.0, set()))
    return calls, codes


async def _wait(job_task):
    await asyncio.wait_for(job_task, 5)


async def test_install_job_runs_each_package(fake_winget):
    calls, codes = fake_winget
    codes["Bad.App"] = 0x8A150107
    job = installer.start_install([{"id": "7zip.7zip", "name": "7-Zip"}, {"id": "Bad.App"}, {"id": "7zip.7zip"}])
    assert job["total"] == 2  # aynı paket iki kez kurulmaz
    await _wait(installer.JOB.task)
    pub = installer.current()
    assert pub["phase"] == "done" and not pub["running"]
    assert [it["state"] for it in pub["items"]] == ["ok", "fail"]
    assert "İnternet" in pub["items"][1]["message"]
    assert any("Başarısız olanları" in g for g in pub["guide"])
    install_calls = [c for c in calls if c[0] == "install"]
    assert install_calls[0][:4] == ["install", "--id", "7zip.7zip", "--exact"]
    assert "--silent" in install_calls[0] and "--accept-package-agreements" in install_calls[0]


async def test_install_rejects_bad_ids_and_parallel_jobs(fake_winget):
    with pytest.raises(installer.InstallerError):
        installer.start_install([{"id": "x & del C:"}])
    with pytest.raises(installer.InstallerError):
        installer.start_install([])
    installer.start_install([{"id": "7zip.7zip"}])
    with pytest.raises(installer.InstallerError, match="sürüyor"):
        installer.start_update()
    await _wait(installer.JOB.task)


async def test_update_job_upgrades_all_then_self_update(fake_winget):
    calls, _ = fake_winget
    seen = {}

    async def self_update():
        seen["called"] = True
        return {"state": "current", "message": "ADA güncel (sürüm 9)."}

    installer.start_update(self_update)
    await _wait(installer.JOB.task)
    pub = installer.current()
    assert pub["phase"] == "done" and seen["called"]
    # sabitlenmiş (ikinci tablodaki) paket kendiliğinden güncellenmez
    assert [it["id"] for it in pub["items"]] == ["GitHub.cli", "Microsoft.DotNet.Runtime.8"]
    assert all(it["state"] == "updated" for it in pub["items"])
    assert pub["items"][0]["message"] == "2.102.0 → 2.103.0"
    assert "ADA güncel (sürüm 9)." in pub["guide"]
    assert [c[2] for c in calls if c[0] == "upgrade" and "--id" in c] == ["GitHub.cli", "Microsoft.DotNet.Runtime.8"]


async def test_cancel_marks_remaining_skipped(monkeypatch, fake_winget):
    async def slow(args, timeout=0, on_line=None):
        await asyncio.sleep(10)
        return 0, ""

    monkeypatch.setattr(installer, "_winget", slow)
    installer.start_install([{"id": "A.One"}, {"id": "B.Two"}])
    await asyncio.sleep(0.05)
    assert installer.cancel()
    await asyncio.wait_for(asyncio.gather(installer.JOB.task, return_exceptions=True), 5)
    pub = installer.current()
    assert pub["phase"] == "cancelled" and {it["state"] for it in pub["items"]} == {"skipped"}


def test_halted_blocks_jobs(fake_winget, monkeypatch):
    from ada import safety

    monkeypatch.setattr(safety, "is_halted", lambda: True)
    with pytest.raises(installer.InstallerError, match="durdurulmuş"):
        installer.start_install([{"id": "7zip.7zip"}])


def test_info_without_winget_gives_guide(monkeypatch):
    monkeypatch.setattr(installer, "winget_path", lambda: None)
    i = installer.info()
    assert i["available"] is False and i["guide"]


def test_server_endpoints(fake_winget, monkeypatch):
    from ada import server

    client = TestClient(server.app)
    st = client.get("/api/installer").json()
    assert st["info"]["available"] is True and st["job"] is None
    cat = client.get("/api/installer/catalog").json()
    assert any(c["id"] == "7zip.7zip" and c["installed"] for c in cat)
    assert any(c["id"] == "Git.Git" and not c["installed"] for c in cat)
    evil = {"Origin": "http://evil.example"}
    assert client.post("/api/installer/install", json={"items": [{"id": "7zip.7zip"}]}, headers=evil).status_code == 403
    assert client.post("/api/installer/update", headers=evil).status_code == 403
    assert client.post("/api/installer/install", json={"items": [{"id": "bad id"}]}).status_code == 409
    r = client.post("/api/installer/install", json={"items": [{"id": "7zip.7zip", "name": "7-Zip"}]})
    assert r.status_code == 200 and r.json()["kind"] == "install"


# ---------------------------------------------------------------- "update" kelimesi: elle güncelleme

LIST_OUT = """Name                      Id                          Version     Available   Source
-----------------------------------------------------------------------------------------
GitHub CLI                GitHub.cli                  2.102.0     2.103.0     winget
7-Zip                     7zip.7zip                   26.05                   winget
Some Old Tool             ARP\\Machine\\X64\\OldTool      1.0
Visual Studio Code        Microsoft.VisualStudioCode  1.105.0                 winget
"""


@pytest.mark.parametrize("text", ["update", "Update", " güncelle ", "GÜNCELLE!", "actualizar", "mettre à jour",
                                  "aktualisieren", "aggiorna", "atualizar", "обновить", "更新", "アップデート", "/update"])
def test_update_word_any_language_opens_manual_panel(text):
    assert installer.parse_command(text) == {"action": "update", "query": "", "manual": True}


@pytest.mark.parametrize("text", ["update the excel file", "güncelle şu raporu", "vlc", "updater"])
def test_sentences_are_not_update_command(text):
    assert installer.parse_command(text) is None


def test_d_update_stays_automatic():
    assert installer.parse_command("/d update") == {"action": "update", "query": ""}


async def test_installed_lists_programs_with_updates_first(fake_winget, monkeypatch):
    async def fake(args, timeout=0, on_line=None):
        return 0, LIST_OUT

    monkeypatch.setattr(installer, "_winget", fake)
    items = await installer.installed()
    assert [i["id"] for i in items][0] == "GitHub.cli" and items[0]["available"] == "2.103.0" and items[0]["managed"]
    by_id = {i["id"]: i for i in items}
    assert by_id["7zip.7zip"]["managed"] and not by_id["7zip.7zip"]["available"]
    old = next(i for i in items if i["name"] == "Some Old Tool")
    assert not old["managed"] and items[-1] is old  # winget dışı en sonda


async def test_upgrade_selected_only_and_optional_ada(fake_winget):
    calls, _ = fake_winget
    seen = {}

    async def self_update():
        seen["called"] = True
        return {"state": "current", "message": "ADA güncel."}

    job = installer.start_upgrade([{"id": "GitHub.cli", "name": "GitHub CLI", "version": "2.102.0", "available": "2.103.0"}], None)
    assert job["kind"] == "update" and job["total"] == 1
    await _wait(installer.JOB.task)
    pub = installer.current()
    assert pub["phase"] == "done" and pub["items"][0]["state"] == "updated"
    assert pub["items"][0]["message"] == "2.102.0 → 2.103.0"
    assert [c[2] for c in calls if c[0] == "upgrade"] == ["GitHub.cli"]  # yalnızca seçilen, liste taranmaz
    assert "called" not in seen
    installer.start_upgrade([], self_update)  # yalnızca ADA
    await _wait(installer.JOB.task)
    assert seen["called"]
    with pytest.raises(installer.InstallerError):
        installer.start_upgrade([], None)
    with pytest.raises(installer.InstallerError):
        installer.start_upgrade([{"id": "a; rm -rf"}], None)


def test_server_installed_and_upgrade_endpoints(fake_winget, monkeypatch):
    async def fake(args, timeout=0, on_line=None):
        return 0, LIST_OUT if args[0] == "list" else "Successfully installed"

    monkeypatch.setattr(installer, "_winget", fake)
    from ada import server

    client = TestClient(server.app)
    r = client.get("/api/installer/installed")
    assert r.status_code == 200 and r.json()[0]["id"] == "GitHub.cli"
    evil = {"Origin": "http://evil.example"}
    assert client.post("/api/installer/upgrade", json={"items": [{"id": "GitHub.cli"}]}, headers=evil).status_code == 403
    assert client.post("/api/installer/upgrade", json={"items": []}).status_code == 409
    r = client.post("/api/installer/upgrade", json={"items": [{"id": "GitHub.cli", "name": "GitHub CLI"}]})
    assert r.status_code == 200 and r.json()["total"] == 1
