"""Sunucu: arayüz dosyaları (paketli uygulamadaki symlink'li düzen dahil)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ada import server


def test_assets_served_through_symlinked_static_dir(tmp_path, monkeypatch):
    real = tmp_path / "Resources" / "dist"
    (real / "assets").mkdir(parents=True)
    (real / "index.html").write_text('<script src="/assets/app.js"></script>')
    (real / "assets" / "app.js").write_text("console.log(1)")
    link = tmp_path / "Frameworks_dist"
    link.symlink_to(real, target_is_directory=True)  # PyInstaller .app düzeni gibi
    monkeypatch.setattr(server, "DIST", link)
    client = TestClient(server.app)
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/../index.html").status_code == 404 or client.get("/assets/%2e%2e/index.html").status_code == 404


def test_first_run_setup_saves_answers_and_is_asked_once():
    import os

    from ada import config
    from ada.tools import memory

    config.SETUP_FLAG.unlink(missing_ok=True)
    env_before = config.ENV_FILE.read_text(encoding="utf-8") if config.ENV_FILE.exists() else None
    client = TestClient(server.app)
    origin = {"Origin": "http://testserver"}
    try:
        assert client.get("/api/status").json()["setup_needed"] is True
        state = client.get("/api/setup").json()
        assert state["needed"] and state["autonomy"] == "full" and state["autostart"]["supported"] is False
        assert client.post("/api/setup", json={"autonomy": "her şey"}, headers=origin).status_code == 400
        assert client.post("/api/setup", json={"autonomy": "ask"}, headers={"Origin": "http://evil.example"}).status_code == 403
        assert config.SETUP_FLAG.exists() is False
        r = client.post("/api/setup", json={"name": "  Kemal  ", "autonomy": "smart", "autostart": True, "watch": False, "computer": True},
                        headers=origin)
        assert r.status_code == 200, r.text
        assert r.json()["restart_required"] is True  # özerklik ve gözcü değişti
        env = config.ENV_FILE.read_text(encoding="utf-8")
        assert "ADA_AUTONOMY=smart" in env and "ADA_WATCH=false" in env
        assert "Kemal" in memory.memory_search("kullanici_adi")
        after = client.get("/api/setup").json()
        assert after["needed"] is False and after["name"] == "Kemal"
        assert client.get("/api/status").json()["setup_needed"] is False
    finally:
        for k in ("ADA_AUTONOMY", "ADA_WATCH", "ADA_ENABLE_COMPUTER"):
            os.environ.pop(k, None)
        os.environ["ADA_AUTONOMY"] = "full"
        os.environ["ADA_ENABLE_COMPUTER"] = "true"
        if env_before is None:
            config.ENV_FILE.unlink(missing_ok=True)
        else:
            config.ENV_FILE.write_text(env_before, encoding="utf-8")


def test_control_endpoints_are_desktop_and_local_only():
    client = TestClient(server.app)
    origin = {"Origin": "http://testserver"}
    assert client.get("/api/status").json()["desktop"] is False
    assert client.post("/api/control/quit", headers=origin).status_code == 400
    # TestClient yerel bir istemci sayılmaz: klasör açılmaz
    assert client.post("/api/control/open-folder", json={"which": "outputs"}, headers=origin).status_code == 403
