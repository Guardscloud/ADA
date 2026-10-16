"""Konuşarak yazma: arayüzde kaydedilen sesi yazıya çevirir.

ADA penceresi (Windows'ta WebView2) tarayıcının konuşma tanımasını kullanamaz: o özellik sesi Google'ın
sunucusuna gönderir ve gömülü pencerede "network" hatası verir. Bu yüzden arayüz sesi kendisi kaydeder
(16 kHz mono WAV) ve buraya gönderir. Sırasıyla anahtarı olan ilk sağlayıcı kullanılır: Gemini (ücretsiz
katman), Groq Whisper (ücretsiz katman), OpenAI. Hiçbiri yoksa Windows'ta yerleşik sesle yazma (Win+H) açılır.
"""

from __future__ import annotations

import os

MAX_BYTES = 20 * 1024 * 1024  # ~10 dk 16 kHz WAV

_PROMPT = (
    "Bu ses kaydında söylenenleri, konuşulan dilde, olduğu gibi yazıya dök. Yalnızca metni ver; açıklama, "
    "tırnak ya da etiket ekleme. Anlaşılır konuşma yoksa boş yanıt ver."
)


class TranscribeError(Exception):
    pass


def provider() -> str | None:
    """Kullanılacak sağlayıcı (anahtarı girilmiş ilk sağlayıcı) ya da None."""
    from . import engines

    for pid in ("gemini", "groq", "openai"):
        if engines.api_key(pid):
            return pid
    return None


async def transcribe(audio: bytes, mime: str = "audio/wav") -> str:
    pid = provider()
    if pid is None:
        raise TranscribeError("Yazıya çevirmek için bir anahtar gerekli (Gemini ücretsizdir: Ayarlar → Yapay zekâ).")
    if not audio:
        return ""
    if len(audio) > MAX_BYTES:
        raise TranscribeError("Kayıt çok uzun.")
    try:
        if pid == "gemini":
            return await _gemini(audio, mime)
        return await _whisper(pid, audio, mime)
    except TranscribeError:
        raise
    except Exception as e:  # noqa: BLE001 - sağlayıcı hatası arayüze kısa bir iletiyle döner
        raise TranscribeError(f"Ses yazıya çevrilemedi ({type(e).__name__}).") from e


async def _gemini(audio: bytes, mime: str) -> str:
    from google.genai import types

    from .providers.gemini import _available_models, get_client

    r = await get_client().aio.models.generate_content(
        model=_available_models()[0],
        contents=[types.Part.from_bytes(data=audio, mime_type=mime), _PROMPT],
        config=types.GenerateContentConfig(temperature=0),
    )
    return (r.text or "").strip()


async def _whisper(pid: str, audio: bytes, mime: str) -> str:
    import openai

    from . import engines

    model = {"groq": "whisper-large-v3-turbo", "openai": "whisper-1"}[pid]
    client = openai.AsyncOpenAI(base_url=engines.PROVIDERS[pid].base_url, api_key=engines.api_key(pid),
                                timeout=60.0, max_retries=1)
    ext = "webm" if "webm" in mime else "ogg" if "ogg" in mime else "wav"
    r = await client.audio.transcriptions.create(model=model, file=(f"ses.{ext}", audio, mime))
    return (r.text or "").strip()


def status() -> dict:
    pid = provider()
    return {"available": pid is not None, "provider": pid, "dictation": os.name == "nt"}


def windows_dictation() -> None:
    """Anahtar yoksa Windows'un sesle yazma özelliğini açar (Win+H). Metin kutusu odakta olduğu için
    söylenenler doğrudan oraya yazılır; WebView2 penceresinde de çalışır."""
    if os.name != "nt":
        raise TranscribeError("Sesle yazma yalnızca Windows'ta açılabilir.")
    import ctypes

    user32 = ctypes.windll.user32
    vk_lwin, vk_h, keyup = 0x5B, 0x48, 0x0002
    user32.keybd_event(vk_lwin, 0, 0, 0)
    user32.keybd_event(vk_h, 0, 0, 0)
    user32.keybd_event(vk_h, 0, keyup, 0)
    user32.keybd_event(vk_lwin, 0, keyup, 0)
