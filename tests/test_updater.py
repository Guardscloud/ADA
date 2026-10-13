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
