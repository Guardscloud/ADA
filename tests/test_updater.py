"""Uygulama içi güncelleme: sürüm karşılaştırma, kaynak denetimi, dosya doğrulama ve API uçları."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import ada
from ada import server, updater


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setenv("ADA_UPDATE_URL", "https://ornek.test/ada/latest.json")
    updater._set(latest="", url="", sha256="", notes="", checked="", error="", phase="idle", progress=0, notified="")
    yield
    updater._set(latest="", url="", sha256="", notes="", checked="", error="", phase="idle", progress=0, notified="")


def test_version_matches_pyproject():
    text = (Path(ada.__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1) == ada.__version__


def test_version_comparison():
    assert updater.is_newer("0.2.0", "0.1.0") and updater.is_newer("v0.1.1", "0.1.0") and updater.is_newer("1.0", "0.9.9")
    assert updater.is_newer("0.10.0", "0.9.0")  # sayısal, metin sırası değil
    assert not updater.is_newer("0.1.0", "0.1.0") and not updater.is_newer("0.1", "0.1.0") and not updater.is_newer("0.0.9", "0.1.0")
    assert not updater.is_newer("", "0.1.0")


async def test_check_reads_manifest_and_resolves_relative_url(monkeypatch):
    async def fake_get(self, url, **kw):
        import httpx

        assert url == "https://ornek.test/ada/latest.json"
        return httpx.Response(200, json={"version": "9.0.0", "url": "ADA-Kurulum.exe", "sha256": "AB" * 32, "notes": "- yenilik"},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    s = await updater.check()
    assert s["available"] and s["latest"] == "9.0.0" and s["notes"] == "- yenilik" and not s["error"]
    assert updater._state["url"] == "https://ornek.test/ada/ADA-Kurulum.exe" and updater._state["sha256"] == "ab" * 32
    assert s["can_install"] is False  # geliştirme modunda kurulmaz


async def test_check_rejects_plain_http_and_bad_sources(monkeypatch):
    monkeypatch.setenv("ADA_UPDATE_URL", "http://ornek.test/latest.json")
    s = await updater.check()
    assert "https" in s["error"] and not s["available"]
    monkeypatch.setenv("ADA_UPDATE_URL", "github:yanlis")
    assert "sahip/depo" in (await updater.check())["error"]
    monkeypatch.setenv("ADA_UPDATE_URL", "")
    assert "ayarlı değil" in (await updater.check())["error"]


async def test_download_url_must_be_https_too(monkeypatch):
    async def fake_get(self, url, **kw):
        import httpx

        return httpx.Response(200, json={"version": "9.0.0", "url": "http://kotu.test/x.exe"}, request=httpx.Request("GET", url))

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    s = await updater.check()
    assert "https" in s["error"] and not s["available"]


def test_verify_checks_sha256(tmp_path):
    f = tmp_path / "kurulum.exe"
    f.write_bytes(b"icerik")
    good = hashlib.sha256(b"icerik").hexdigest()
    updater.verify(f, good.upper(), own_exe=f)  # imzasız kopya: yalnızca özet denetlenir
    with pytest.raises(updater.UpdateError, match="SHA-256"):
        updater.verify(f, "0" * 64, own_exe=f)


def test_verify_requires_same_signer(tmp_path, monkeypatch):
    f = tmp_path / "kurulum.exe"
    f.write_bytes(b"x")
    sigs = {"own": ("UnknownError", "AAAA"), str(f): ("UnknownError", "AAAA")}
    monkeypatch.setattr(updater, "signer", lambda p: sigs[str(p)])
    updater.verify(f, "", own_exe="own")
    sigs[str(f)] = ("UnknownError", "BBBB")
    with pytest.raises(updater.UpdateError, match="başka bir sertifika"):
        updater.verify(f, "", own_exe="own")
    sigs[str(f)] = ("HashMismatch", "AAAA")
    with pytest.raises(updater.UpdateError, match="imzası geçersiz"):
        updater.verify(f, "", own_exe="own")
    sigs[str(f)] = ("NotSigned", "")
    with pytest.raises(updater.UpdateError, match="imzası geçersiz"):
        updater.verify(f, "", own_exe="own")


def test_failed_install_is_reported_after_restart(tmp_path, monkeypatch):
    """Kurulum durup ADA eski sürümle yeniden açılırsa kullanıcı nedenini görür; başarılıysa sessiz kalır."""
    pending = tmp_path / "pending.txt"
    monkeypatch.setattr(updater, "PENDING_FILE", pending)
    pending.write_text("99.0.0", encoding="utf-8")
    monkeypatch.setattr(updater, "_pending_checked", False)
    s = updater.status()
    assert s["phase"] == "error" and "99.0.0 kurulamadı" in s["error"] and not pending.exists()

    updater._set(phase="idle", error="")
    pending.write_text(ada.__version__, encoding="utf-8")  # kurulum bitti: çalışan sürüm istenen sürüm
    monkeypatch.setattr(updater, "_pending_checked", False)
    assert updater.status()["phase"] == "idle" and not pending.exists()


def test_frozen_app_does_not_pass_its_dll_dir_to_children(monkeypatch):
    """Paketli uygulamada (Windows) ADA'nın açtığı programlar DLL'leri ADA'nın klasöründen yüklememeli."""
    from ada import desktop

    calls = []
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop.sys, "_MEIPASS", "C:/ada/_internal", raising=False)
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setattr(desktop.os, "add_dll_directory", lambda p: calls.append(("add", p)) or "tutamaç", raising=False)
    import ctypes

    fake_k32 = type("K", (), {"SetDllDirectoryW": staticmethod(lambda v: calls.append(("set", v)))})
    monkeypatch.setattr(ctypes, "windll", type("W", (), {"kernel32": fake_k32})(), raising=False)
    desktop._keep_dll_dir_private()
    assert calls == [("add", "C:/ada/_internal"), ("set", None)]


@pytest.mark.skipif(not __import__("sys").platform.startswith("win"), reason="adlandırılmış mutex yalnızca Windows'ta")
def test_only_one_ada_per_data_folder(tmp_path, monkeypatch):
    """Aynı veri klasörüyle ikinci ADA açılmaz; eski kopya kapanınca (yeniden başlatma) yenisi açılabilir."""
    import ctypes
    import subprocess
    import sys

    from ada import desktop

    monkeypatch.setenv("ADA_HOME", str(tmp_path))
    monkeypatch.setattr(desktop, "_INSTANCE_MUTEX", None)
    holder = subprocess.Popen([sys.executable, "-c", "import sys, time; sys.path.insert(0, sys.argv[1]); from ada import desktop; "
                               "print(desktop._claim_single_instance(), flush=True); time.sleep(30)",
                               str(Path(ada.__file__).parent.parent)], stdout=subprocess.PIPE, text=True,
                              env={**__import__("os").environ, "ADA_HOME": str(tmp_path)})
    try:
        assert holder.stdout.readline().strip() == "True"  # ilk kopya kilidi aldı
        assert desktop._claim_single_instance() is False  # ikinci kopya açılmaz
        holder.kill()
        holder.wait()
        assert desktop._claim_single_instance(wait=5) is True  # eski kopya kapandı: yeniden başlatma açılır
    finally:
        holder.kill()
        if desktop._INSTANCE_MUTEX:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(desktop._INSTANCE_MUTEX))
    monkeypatch.setenv("ADA_HOME", str(tmp_path / "baska"))
    monkeypatch.setattr(desktop, "_INSTANCE_MUTEX", None)
    assert desktop._claim_single_instance() is True  # başka veri klasörü başka kilit


def test_update_api():
    client = TestClient(server.app)
    origin = {"Origin": "http://testserver"}
    s = client.get("/api/update").json()
    assert s["current"] == ada.__version__ and s["available"] is False and s["phase"] == "idle"
    assert client.get("/api/status").json()["version"] == ada.__version__
    assert client.post("/api/update/install", headers={"Origin": "http://evil.example"}).status_code == 403
    r = client.post("/api/update/install", headers=origin)
    assert r.status_code == 400 and "kurulu Windows" in r.json()["detail"]


def test_about_has_feedback_link_and_hides_donation_until_configured(monkeypatch):
    from ada import config

    client = TestClient(server.app)
    a = client.get("/api/about").json()
    assert a["version"] == ada.__version__ and a["feedback_url"] == f"https://github.com/{ada.REPO}/issues/new"
    assert a["donate_url"] == "" and a["donate_amounts"] == [1, 2, 3, 4, 5]  # bağış yalnızca 1-5 $
    assert config.UPDATE_URL == f"github:{ada.REPO}"  # varsayılan güncelleme kaynağı yayın deposudur
    monkeypatch.setattr(config, "DONATE_URL", "https://ornek.test/bagis/{amount}")
    assert client.get("/api/about").json()["donate_url"] == "https://ornek.test/bagis/{amount}"


def test_language_setting_api():
    import os

    from ada import config

    client = TestClient(server.app)
    origin = {"Origin": "http://testserver"}
    env_before = config.ENV_FILE.read_text(encoding="utf-8") if config.ENV_FILE.exists() else None
    try:
        assert client.get("/api/status").json()["lang"] == "auto"
        assert client.post("/api/lang", json={"lang": "xx"}, headers=origin).status_code == 400
        assert client.post("/api/lang", json={"lang": "de"}, headers=origin).json() == {"ok": True, "lang": "de"}
        assert client.get("/api/status").json()["lang"] == "de"  # yeniden başlatma gerekmez
        assert not any(f["key"] == "ADA_LANG" for f in client.get("/api/settings").json()["fields"])
    finally:
        os.environ.pop("ADA_LANG", None)
        if env_before is None:
            config.ENV_FILE.unlink(missing_ok=True)
        else:
            config.ENV_FILE.write_text(env_before, encoding="utf-8")


def test_every_language_has_a_complete_dictionary():
    """Her arayüz dili için sözlük vardır, kataloğun tamamını kapsar ve yer tutucuları korur."""
    import json

    from ada import config

    base = Path(ada.__file__).parent.parent / "frontend" / "public" / "assets" / "i18n"
    catalog = json.loads((base / "catalog.json").read_text(encoding="utf-8"))
    assert len(catalog) > 500
    for lang in config.LANGS:
        if lang == "tr":
            continue
        d = json.loads((base / f"{lang}.json").read_text(encoding="utf-8"))
        missing = [k for k in catalog if not str(d.get(k, "")).strip()]
        assert not missing, f"{lang}: {len(missing)} eksik, ör. {missing[:3]}"
        for k in catalog:
            assert sorted(re.findall(r"\{\d+\}", k)) == sorted(re.findall(r"\{\d+\}", d[k])), f"{lang}: {k!r}"


def test_github_release_asset_names(monkeypatch):
    """Yeni ad ADA-Setup.exe; yalnızca eski ad varsa o da kabul edilir."""
    import asyncio

    import httpx

    monkeypatch.setenv("ADA_UPDATE_URL", "github:sahip/depo")

    def release(names):
        async def fake_get(self, url, **kw):
            return httpx.Response(200, request=httpx.Request("GET", url), json={
                "tag_name": "v9.0.0", "body": "", "assets": [
                    {"name": n, "browser_download_url": f"https://ornek.test/{n}", "digest": "sha256:" + "ab" * 32} for n in names]})
        return fake_get

    monkeypatch.setattr("httpx.AsyncClient.get", release(["ADA-Kurulum.exe", "ADA-Setup.exe"]))
    assert asyncio.run(updater.check())["available"] and updater._state["url"].endswith("/ADA-Setup.exe")
    monkeypatch.setattr("httpx.AsyncClient.get", release(["ADA-Kurulum.exe"]))
    asyncio.run(updater.check()); assert updater._state["url"].endswith("/ADA-Kurulum.exe")
    monkeypatch.setattr("httpx.AsyncClient.get", release(["baska.zip"]))
    assert "ADA-Setup.exe" in asyncio.run(updater.check())["error"]
