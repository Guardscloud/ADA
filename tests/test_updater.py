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
