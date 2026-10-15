"""Test ortamı: geçici çalışma alanı, sahte Gemini istemcisi, her testten önce temiz durum.

Hiçbir test gerçek API'ye, ekrana, Mail'e veya kullanıcının dosyalarına dokunmaz.
config modülü içe aktarılırken ortam değişkenlerini okuduğu için ayarlar burada, ada
içe aktarılmadan önce yapılır.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from types import SimpleNamespace as NS

_TMP = tempfile.mkdtemp(prefix="ada-test-")
os.environ.update({
    "ADA_HOME": _TMP,
    "ADA_WORKSPACE": os.path.join(_TMP, "workspace"),
    "ADA_PROVIDER": "gemini",
    "GEMINI_API_KEY": "test",
    "ADA_QWEN_ENABLED": "true",  # Qwen varsayılan olarak pasiftir; yönlendirici testleri Qwen motorlarıyla çalışır
    "ADA_QWEN_FALLBACK": "false",  # Qwen yedeği testlerde ayrıca açılır
    "ADA_NOTIFICATIONS": "false",  # testler masaüstüne gerçek bildirim göndermesin
    "ADA_AUTONOMY": "full",
    "ADA_ENABLE_COMPUTER": "true",
    "ADA_ENABLE_MAC_APPS": "false",
    "ADA_NO_DISCOVERY": "1",  # yerel model/bulut taraması testlerde çalışmaz
    "ADA_TOR": "false",  # testler Tor indirmesin/başlatmasın (test_tor.py ayrıca açar)
})

import pytest  # noqa: E402
from google.genai import types  # noqa: E402

from ada import config  # noqa: E402


def gemini_response(*parts, finish="STOP"):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=list(parts)), finish_reason=finish)]
    )


def call(name: str, **args):
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


def text(t: str, thought: bool = False):
    return types.Part(text=t, thought=thought or None)


class FakeGemini:
    """Sahte Gemini. `script(contents, config) -> GenerateContentResponse | Exception` ile yönlendirilir."""

    def __init__(self, script):
        self.script = script
        self.calls: list[dict] = []
        self.aio = NS(models=self)

    async def generate_content_stream(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        result = self.script(contents, config)
        if isinstance(result, Exception):
            raise result

        async def gen():
            for chunk in result if isinstance(result, list) else [result]:
                yield chunk

        return gen()


@pytest.fixture
def fake_gemini(monkeypatch):
    """Kullanım: fake = fake_gemini(lambda contents, cfg: gemini_response(...))"""
    from ada.providers import gemini

    def install(script):
        fake = FakeGemini(script)
        monkeypatch.setattr(gemini, "_client", fake)
        gemini._EXHAUSTED.clear()
        return fake

    return install


@pytest.fixture(autouse=True)
def clean_state():
    """Her test temiz bir çalışma alanı ve sıfır sayaçla başlar."""
    from ada import approval, runtime, safety

    for d in (config.OUTPUTS_DIR, config.UPLOADS_DIR, config.ROUTINES_DIR, config.ROUTINE_REPORTS_DIR):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
    for name in ("goals.json", "triggers.json", "schedules.json", "memory.json", "halted.json", "always_allow.json", "routines.json"):
        (config.DATA_DIR / name).unlink(missing_ok=True)
    for d in ("skills", "backups", "audit"):
        shutil.rmtree(config.DATA_DIR / d, ignore_errors=True)
    safety._counts.clear()
    runtime.RUNS.clear()
    approval.PENDING.clear()
    yield


def last_user_text(contents) -> str:
    part = contents[-1].parts[-1]
    return part.text or ""
