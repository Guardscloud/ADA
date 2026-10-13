"""Gemini'nin Qwen yedeği: öncelik her zaman Gemini'dedir.

Gemini bir turu yapamazsa (tüm modellerin kotası dolu, sunucu yoğun, güvenlik filtresi, boş yanıt,
geçersiz anahtar) tur kaldığı yerden Qwen ile (OpenAI uyumlu API) sürdürülür. Konuşma geçmişi Gemini
biçiminde kalır: Qwen'e çevrilmiş bir kopya verilir, Qwen'in ürettiği adımlar Gemini biçimine çevrilip
geçmişe eklenir. Böylece sonraki tur yine önce Gemini ile denenir.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from typing import AsyncIterator, Callable

import httpx
import openai
from google.genai import errors, types

from .. import config
from ..tools import computer
from .gemini import GeminiDeclined, GeminiQuotaExhausted, gemini_loop
from .selfhost import Endpoint, selfhost_loop

# Gemini 3, geçmişteki fonksiyon çağrılarında düşünce imzası ister; başka modelin (Qwen) ürettiği
# çağrılar için belgelenmiş "doğrulamayı atla" imzası kullanılır.
SKIP_SIGNATURE = b"skip_thought_signature_validator"
# Yarıda durdurulmuş görevde sonucu hiç gelmemiş araç çağrısı için verilen yanıt
INTERRUPTED = "Bu araç çağrısı çalıştırılmadı: görev o sırada durdurulmuştu. Sonucuna ihtiyaç varsa aracı yeniden çağır."


class QwenFailed(Exception):
    """Gemini yapamadı, Qwen de başarısız oldu."""

    def __init__(self, gemini_reason: str, qwen_message: str):
        super().__init__(f"{gemini_reason}; Qwen: {qwen_message}")
        self.gemini_reason = gemini_reason
        self.qwen_message = qwen_message


def endpoint() -> Endpoint:
    return Endpoint(config.QWEN_BASE_URL, config.QWEN_API_KEY, config.QWEN_MODEL, config.QWEN_VISION, config.QWEN_MAX_TOKENS, config.QWEN_TIMEOUT)


def gemini_failed(e: BaseException) -> str | None:
    """Gemini'nin yapamadığı durumlar için kısa açıklama; Qwen'e devredilmeyecek hatalarda None."""
    if isinstance(e, GeminiQuotaExhausted):
        return "Gemini kotası doldu"
    if isinstance(e, GeminiDeclined):
        return str(e)
    if isinstance(e, errors.ClientError):
        if e.code == 402:
            return "Gemini ön ödemeli kredisi bitti"
        return f"Gemini isteği reddetti ({e.code})"
    if isinstance(e, errors.ServerError):
        return f"Gemini sunucu hatası ({e.code})"
    if isinstance(e, (httpx.HTTPError, asyncio.TimeoutError)):
        return "Gemini yanıt vermedi (bağlantı/zaman aşımı)"
    if isinstance(e, RuntimeError) and "GEMINI_API_KEY" in str(e):
        return "GEMINI_API_KEY yok"
    return None


# ---------------------------------------------------------------- biçim dönüşümleri

def _image_url(blob: types.Blob) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{blob.mime_type};base64,{base64.b64encode(blob.data or b'').decode()}"}}


def _user_message(items: list[dict], screenshot: bool) -> dict:
    if all(i["type"] == "text" for i in items):
        msg = {"role": "user", "content": "\n\n".join(i["text"] for i in items)}
    else:
        msg = {"role": "user", "content": items}
    if screenshot:
        msg["ada_screenshot"] = True
    return msg


def to_openai(messages: list[dict], vision: bool) -> list[dict]:
    """Gemini Content dict'lerini OpenAI Chat Completions mesajlarına çevirir (düşünce parçaları atılır)."""
    out: list[dict] = []
    pending: list[tuple[str, str]] = []  # yanıtı beklenen (çağrı kimliği, araç adı)

    def close_pending():
        # OpenAI her araç çağrısına bir yanıt ister
        for cid, _ in pending:
            out.append({"role": "tool", "tool_call_id": cid, "content": "HATA: " + INTERRUPTED})
        pending.clear()

    for m in messages:
        c = types.Content.model_validate(m)
        parts = c.parts or []
        if c.role == "model":
            close_pending()
            text = "".join(p.text for p in parts if p.text and not p.thought)
            calls = []
            for p in parts:
                if p.function_call:
                    cid = f"call_{uuid.uuid4().hex[:12]}"
                    args = json.dumps(dict(p.function_call.args or {}), ensure_ascii=False)
                    calls.append({"id": cid, "type": "function", "function": {"name": p.function_call.name, "arguments": args}})
                    pending.append((cid, p.function_call.name))
            if text or calls:
                msg: dict = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            continue

        extra: list[dict] = []
        screenshot = False
        for p in parts:
            if p.function_response:
                fr = p.function_response
                resp = fr.response or {}
                content = ("HATA: " + str(resp["error"])) if "error" in resp else str(resp.get("result", json.dumps(resp, ensure_ascii=False)))
                idx = next((i for i, (_, name) in enumerate(pending) if name == fr.name), 0 if pending else None)
                if idx is None:
                    extra.append({"type": "text", "text": f"[{fr.name} sonucu] {content}"})
                    continue
                cid, _ = pending.pop(idx)
                out.append({"role": "tool", "tool_call_id": cid, "content": content})
            elif p.inline_data:
                screenshot = screenshot or (p.inline_data.display_name or "").startswith("ekran")
                extra.append(_image_url(p.inline_data) if vision else {"type": "text", "text": "[Görsel var ama bu model görselleri okuyamıyor.]"})
            elif p.text and not p.thought:
                extra.append({"type": "text", "text": p.text})
        close_pending()
        if extra:
            out.append(_user_message(extra, screenshot))
    close_pending()
    return out


def to_gemini(messages: list[dict]) -> list[dict]:
    """Qwen'in eklediği OpenAI mesajlarını Gemini Content dict'lerine çevirir."""
    out: list[dict] = []
    names: dict[str, str] = {}
    pending: list[str] = []  # yanıtı gelmemiş çağrılar (ör. yarıda durdurulmuş görev)
    user_parts: list[types.Part] = []

    def flush():
        # Gemini her fonksiyon çağrısından sonra bir yanıt bekler
        for cid in pending:
            user_parts.insert(0, types.Part.from_function_response(name=names[cid], response={"error": INTERRUPTED}))
        pending.clear()
        if user_parts:
            out.append(types.Content(role="user", parts=list(user_parts)).model_dump(mode="json", exclude_none=True))
            user_parts.clear()

    for m in messages:
        role = m.get("role")
        if role == "assistant":
            flush()
            parts = []
            if m.get("content"):
                parts.append(types.Part(text=m["content"]))
            for tc in m.get("tool_calls") or []:
                names[tc["id"]] = tc["function"]["name"]
                pending.append(tc["id"])
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except ValueError:
                    args = {}
                parts.append(types.Part(
                    function_call=types.FunctionCall(name=tc["function"]["name"], args=args if isinstance(args, dict) else {}),
                    thought_signature=SKIP_SIGNATURE,
                ))
            if parts:
                out.append(types.Content(role="model", parts=parts).model_dump(mode="json", exclude_none=True))
        elif role == "tool":
            if m.get("tool_call_id") in pending:
                pending.remove(m["tool_call_id"])
            content = m.get("content") or ""
            payload = {"error": content[6:]} if content.startswith("HATA: ") else {"result": content}
            user_parts.append(types.Part.from_function_response(name=names.get(m.get("tool_call_id"), "arac"), response=payload))
        elif role == "user":
            content = m.get("content")
            items = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
            for item in items:
                if item.get("type") == "text":
                    user_parts.append(types.Part(text=item["text"]))
                elif item.get("type") == "image_url":
                    head, _, data = item["image_url"]["url"].partition(",")
                    mime = head.removeprefix("data:").split(";")[0] or "image/png"
                    name = f"ekran-qwen-{uuid.uuid4().hex[:8]}" if m.get("ada_screenshot") else None
                    user_parts.append(types.Part(inline_data=types.Blob(mime_type=mime, data=base64.b64decode(data), display_name=name)))
    flush()
    return out


# ---------------------------------------------------------------- döngü

def describe_qwen_error(e: Exception) -> str:
    if isinstance(e, openai.APIConnectionError):
        return f"sunucuya bağlanılamadı ({config.QWEN_BASE_URL})"
    if isinstance(e, openai.AuthenticationError):
        return "API anahtarı reddedildi (ADA_QWEN_API_KEY / DASHSCOPE_API_KEY)"
    if isinstance(e, openai.NotFoundError):
        return f"'{config.QWEN_MODEL}' modeli bulunamadı (ADA_QWEN_MODEL)"
    if isinstance(e, openai.APIStatusError):
        return f"hata ({e.status_code}): {e.message}"
    return f"{type(e).__name__}: {e}"


async def qwen_loop(
    messages: list[dict],
    *,
    system: list[dict],
    tools: list[dict],
    ui_entry: dict,
    save: Callable[[], None],
    max_steps: int,
    reason: str,
) -> AsyncIterator[dict]:
    """Turu Qwen ile sürdürür; `messages` (Gemini biçimi) her tamamlanan adımda güncellenir."""
    from ..agent import CORE_TOOLS

    ep = endpoint()
    if config.QWEN_TOOLSET == "core":
        tools = [t for t in tools if t.get("name") in CORE_TOOLS]
    oa = to_openai(messages, ep.vision)
    base_g, base_o = len(messages), len(oa)

    def sync():
        messages[base_g:] = to_gemini(oa[base_o:])
        save()

    yield {"type": "text_set", "text": ui_entry["text"]}  # Gemini'nin yarım kalan metni ekrandan da silinir
    ev = {"type": "step", "label": "Qwen devraldı", "detail": f"{reason} → {ep.model}"}
    ui_entry["steps"].append({"label": ev["label"], "detail": ev["detail"]})
    yield ev
    computer.COORDS_OVERRIDE.set(config.QWEN_SCREEN_COORDS == "norm1000")
    try:
        async for ev in selfhost_loop(oa, system=system, tools=tools, ui_entry=ui_entry, save=sync, max_steps=max_steps, endpoint=ep):
            yield ev
    except openai.OpenAIError as e:
        raise QwenFailed(reason, describe_qwen_error(e)) from e


async def gemini_with_qwen(
    messages: list[dict],
    *,
    system: list[dict],
    tools: list[dict],
    ui_entry: dict,
    save: Callable[[], None],
    max_steps: int,
) -> AsyncIterator[dict]:
    """Önce Gemini; Gemini turu bitiremezse aynı tur kaldığı yerden Qwen ile tamamlanır."""
    computer.COORDS_OVERRIDE.set(None)
    start = len(messages)
    try:
        async for ev in gemini_loop(messages, system=system, tools=tools, ui_entry=ui_entry, save=save, max_steps=max_steps):
            yield ev
        return
    except Exception as e:  # noqa: BLE001 - yalnızca Gemini kaynaklı hatalar devredilir
        reason = gemini_failed(e)
        if reason is None:
            raise
    # Adım sınırı tur başınadır: Qwen, Gemini'nin harcadığı adımlardan kalanı kullanır
    used = sum(1 for m in messages[start:] if m.get("role") == "model")
    async for ev in qwen_loop(messages, system=system, tools=tools, ui_entry=ui_entry, save=save, max_steps=max(1, max_steps - used), reason=reason):
        yield ev
