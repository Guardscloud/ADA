"""Tor: açıkken web araçları ve site açma Tor'dan geçer; Tor yoksa doğrudan bağlanılmaz; kapatılabilir."""

from __future__ import annotations

import pytest

from ada import tor
from ada.tools import ToolError
from ada.tools import system as system_tools
from ada.tools import web


def test_off_means_direct(monkeypatch):
    monkeypatch.setenv("ADA_TOR", "false")
    monkeypatch.setattr(tor, "ensure", lambda: pytest.fail("Tor kapalıyken başlatılmamalı"))
    assert tor.proxy_url() is None


def test_on_uses_socks5h(monkeypatch):
    monkeypatch.setenv("ADA_TOR", "true")
    monkeypatch.setattr(tor, "ensure", lambda: 9350)
    assert tor.proxy_url() == "socks5h://127.0.0.1:9350"  # alan adı da Tor'da çözülür


def test_fetch_routes_through_tor(monkeypatch):
    monkeypatch.setenv("ADA_TOR", "true")
    monkeypatch.setattr(tor, "ensure", lambda: 9350)
    seen = {}

    def fake_get(url, **kw):
        seen.update(kw)
        raise web.httpx.ConnectError("yok")

    monkeypatch.setattr(web.httpx, "get", fake_get)
    with pytest.raises(ToolError):
        web.web_fetch("https://example.com")
    assert seen["proxy"] == "socks5h://127.0.0.1:9350"


def test_no_tor_no_direct_request(monkeypatch):
    monkeypatch.setenv("ADA_TOR", "true")

    def broken():
        raise tor.TorError("Tor ağına bağlanılamadı (zaman aşımı).")

    monkeypatch.setattr(tor, "ensure", broken)
    monkeypatch.setattr(web.httpx, "get", lambda *a, **k: pytest.fail("Tor yokken doğrudan istek gönderildi"))
    with pytest.raises(ToolError, match="Ayarlar"):
        web.web_fetch("https://example.com")


def test_open_url_goes_to_tor(monkeypatch):
    monkeypatch.setenv("ADA_TOR", "true")
    opened = []
    monkeypatch.setattr(tor, "open_url", lambda url: opened.append(url) or "ok")
    monkeypatch.setattr(system_tools.subprocess, "Popen", lambda *a, **k: pytest.fail("varsayılan tarayıcı açıldı"))
    monkeypatch.setattr(system_tools.os, "startfile", lambda *a: pytest.fail("varsayılan tarayıcı açıldı"), raising=False)
    from ada.tools import REGISTRY

    assert REGISTRY["open_item"].handler(url="https://example.com") == "ok"
    assert opened == ["https://example.com"]


def test_open_url_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from ada import server

    monkeypatch.setenv("ADA_TOR", "true")
    opened = []
    monkeypatch.setattr(tor, "open_url", lambda url: opened.append(url) or "ok")
    c = TestClient(server.app, client=("127.0.0.1", 5000))
    assert c.post("/api/open-url", json={"url": "https://example.com"}).status_code == 200
    assert opened == ["https://example.com"]
    assert c.post("/api/open-url", json={"url": "file:///C:/Windows/notepad.exe"}).status_code == 400
    # başka bir sitenin sayfasından gelen istek reddedilir
    r = c.post("/api/open-url", json={"url": "https://example.com"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and len(opened) == 1
