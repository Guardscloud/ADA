"""Konuşarak yazma: kaydedilen sesin sunucuda yazıya çevrilmesi."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ada import server, transcribe

ORIGIN = {"Origin": "http://testserver"}
KEYS = ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY")


def _keys(monkeypatch, **set_):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    for k, v in set_.items():
        monkeypatch.setenv(k, v)


def test_provider_order(monkeypatch):
    _keys(monkeypatch)
    assert transcribe.provider() is None and not transcribe.status()["available"]
    _keys(monkeypatch, OPENAI_API_KEY="o", GROQ_API_KEY="g")
    assert transcribe.provider() == "groq"
    _keys(monkeypatch, OPENAI_API_KEY="o", GEMINI_API_KEY="k")
    assert transcribe.provider() == "gemini"


def test_endpoint_transcribes_with_the_first_provider(monkeypatch):
    _keys(monkeypatch, GEMINI_API_KEY="k")
    seen = {}

    async def fake_gemini(audio, mime):
        seen.update(audio=audio, mime=mime)
        return "merhaba ADA"

    monkeypatch.setattr(transcribe, "_gemini", fake_gemini)
    client = TestClient(server.app)
    assert client.get("/api/voice").json()["available"]
    r = client.post("/api/voice/transcribe", files={"file": ("ses.wav", b"RIFFdata", "audio/wav")}, headers=ORIGIN)
    assert r.status_code == 200 and r.json() == {"text": "merhaba ADA"}
    assert seen == {"audio": b"RIFFdata", "mime": "audio/wav"}


def test_windows_dictation_only_from_this_computer(monkeypatch):
    pressed = []
    monkeypatch.setattr(transcribe, "windows_dictation", lambda: pressed.append(1))
    client = TestClient(server.app)
    # TestClient yerel istemci sayılmaz: tuş vuruşu gönderilmez
    assert client.post("/api/voice/dictation", headers=ORIGIN).status_code == 403
    monkeypatch.setattr(server, "_local", lambda request: True)
    assert client.post("/api/voice/dictation", headers=ORIGIN).json() == {"ok": True}
    assert client.post("/api/voice/dictation", headers={"Origin": "http://evil.example"}).status_code == 403
    assert pressed == [1]


def test_endpoint_errors_are_short_turkish_messages(monkeypatch):
    client = TestClient(server.app)
    _keys(monkeypatch)
    r = client.post("/api/voice/transcribe", files={"file": ("ses.wav", b"x", "audio/wav")}, headers=ORIGIN)
    assert r.status_code == 400 and "anahtar" in r.json()["detail"]

    _keys(monkeypatch, GROQ_API_KEY="g")

    async def broken(*a):
        raise ConnectionError("boom")

    monkeypatch.setattr(transcribe, "_whisper", broken)
    r = client.post("/api/voice/transcribe", files={"file": ("ses.wav", b"x", "audio/wav")}, headers=ORIGIN)
    assert r.status_code == 400 and r.json()["detail"] == "Ses yazıya çevrilemedi (ConnectionError)."

    # Başka siteden gelen istek reddedilir
    r = client.post("/api/voice/transcribe", files={"file": ("ses.wav", b"x", "audio/wav")}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
