"""Kendi sunucunuzdaki açık modeller: OpenAI uyumlu Chat Completions API.

vLLM (kiralık GPU sunucusu), Ollama (bu Mac), LM Studio, llama.cpp server gibi her
OpenAI uyumlu uç noktayla çalışır. `agent.agent_loop` ile aynı olayları üretir ve ortak
araç katmanını (onaylar, alt ajanlar, ekran) kullanır.

Görsel anlamayan modellerde (ADA_LLM_VISION=false) ekran kontrolü araçları gönderilmez.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Callable

import openai

from .. import config
from ..tools import computer

@dataclass(frozen=True)
class Endpoint:
    """OpenAI uyumlu bir model uç noktası (kendi sunucu veya Gemini'nin Qwen yedeği)."""

    base_url: str
    api_key: str
    model: str
    vision: bool
    max_tokens: int
    timeout: float
    token_param: str = "max_tokens"  # OpenAI'nin yeni modelleri max_completion_tokens ister


def selfhost_endpoint() -> Endpoint:
    return Endpoint(config.LLM_BASE_URL, config.LLM_API_KEY, config.LLM_MODEL, config.LLM_VISION, config.LLM_MAX_TOKENS, config.LLM_TIMEOUT)


_clients: dict[Endpoint, openai.AsyncOpenAI] = {}


def get_client(ep: Endpoint | None = None) -> openai.AsyncOpenAI:
    ep = ep or selfhost_endpoint()
    if ep not in _clients:
        _clients[ep] = openai.AsyncOpenAI(base_url=ep.base_url, api_key=ep.api_key or "yok", timeout=ep.timeout, max_retries=1)
    return _clients[ep]


def tool_specs(tools: list[dict], vision: bool | None = None) -> list[dict]:
    vision = config.LLM_VISION if vision is None else vision
    specs = []
    for t in tools:
        if t.get("type") == "computer_toolset_20260801":
            if vision:
                specs += [{"type": "function", "function": d} for d in computer.function_declarations()]
        elif "input_schema" in t:
            specs.append({"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}})
    return specs


def user_content(blocks: list[dict], vision: bool | None = None) -> dict:
    """Anthropic biçimli kullanıcı bloklarını OpenAI mesajına çevirir."""
    vision = config.LLM_VISION if vision is None else vision
    parts = []
    for b in blocks:
        if b["type"] == "text":
            parts.append({"type": "text", "text": b["text"]})
        elif b["type"] == "image":
            if vision:
                src = b["source"]
                parts.append({"type": "image_url", "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
            else:
                parts.append({"type": "text", "text": "[Görsel eklendi ama bu model görselleri okuyamıyor.]"})
    if all(p["type"] == "text" for p in parts):
        return {"role": "user", "content": "\n\n".join(p["text"] for p in parts)}
    return {"role": "user", "content": parts}


def _compact_tool_results(messages: list[dict]) -> list[dict]:
    """Son KEEP_FULL_TOOL_RESULTS araç sonucu tam, eskileri kısaltılmış kopya (küçük bağlamlı yerel modeller için)."""
    out, seen, limit = [], 0, config.OLD_TOOL_RESULT_CHARS
    for m in reversed(messages):
        if m.get("role") == "tool" and isinstance(m.get("content"), str):
            seen += 1
            if seen > config.KEEP_FULL_TOOL_RESULTS and len(m["content"]) > limit:
                m = {**m, "content": m["content"][:limit] + f" …[eski araç çıktısı kısaltıldı; {len(m['content']):,} karakterdi]"}
        out.append(m)
    return list(reversed(out))


def _prune_images(messages: list[dict]) -> list[dict]:
    """Gönderilen kopyada yalnızca son N ekran görüntüsü kalır."""
    keep, seen, out = config.GEMINI_KEEP_SCREENSHOTS, 0, []
    for m in reversed(messages):
        if m.get("ada_screenshot"):
            seen += 1
            if seen > keep:
                m = {"role": "user", "content": "[eski ekran görüntüsü kaldırıldı]"}
        out.append({k: v for k, v in m.items() if not k.startswith("ada_")})
    return list(reversed(out))


async def selfhost_loop(
    messages: list[dict],
    *,
    system: list[dict],
    tools: list[dict],
    ui_entry: dict,
    save: Callable[[], None],
    max_steps: int,
    endpoint: Endpoint | None = None,
) -> AsyncIterator[dict]:
    from ..agent import ToolResult, _label, _run_tools, _short

    ep = endpoint or selfhost_endpoint()
    system_text = "\n\n".join(b["text"] for b in system if b.get("type") == "text")
    specs = tool_specs(tools, ep.vision)
    step = 0
    while step < max_steps:
        step += 1
        payload = [{"role": "system", "content": system_text}] + _compact_tool_results(_prune_images(messages))
        text, calls, finish = "", {}, None
        stream = await get_client(ep).chat.completions.create(
            model=ep.model,
            messages=payload,
            tools=specs or openai.NOT_GIVEN,
            stream=True,
            **{ep.token_param: ep.max_tokens},
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            finish = choice.finish_reason or finish
            delta = choice.delta
            # Akıl yürütme modelleri düşünceyi ayrı alanda gönderir (vLLM: reasoning_content, Ollama: reasoning)
            thought = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if thought:
                yield {"type": "thinking", "text": thought}
            if delta.content:
                text += delta.content
                ui_entry["text"] += delta.content
                yield {"type": "text", "text": delta.content}
            for tc in delta.tool_calls or []:
                slot = calls.setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments

        # Bazı yerel modeller (ör. qwen3-coder) yanıt sonuna boş bir "<tool_call>" etiketi sızdırıyor
        cleaned = re.sub(r"(\s*</?tool_call>)+\s*$", "", text)
        if cleaned != text and not calls:
            cut = len(text) - len(cleaned)
            ui_entry["text"] = ui_entry["text"][:-cut]
            text = cleaned
            yield {"type": "text_set", "text": ui_entry["text"]}

        tool_calls = []
        for idx in sorted(calls):
            c = calls[idx]
            c["id"] = c["id"] or f"call_{uuid.uuid4().hex[:12]}"
            tool_calls.append({"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}})

        assistant: dict = {"role": "assistant", "content": text or None}
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        if text or tool_calls:
            messages.append(assistant)

        if not tool_calls:
            if finish == "length":
                yield {"type": "error", "message": "Yanıt uzunluk sınırına ulaştı; 'devam et' yazarak sürdürebilirsiniz."}
            elif not text:
                yield {"type": "error", "message": "Model boş yanıt döndü; tekrar deneyin."}
            save()
            return
        if ui_entry["text"] and not ui_entry["text"].endswith("\n"):
            ui_entry["text"] += "\n\n"
            yield {"type": "text", "text": "\n\n"}

        blocks, bad = [], {}
        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
                if not isinstance(args, dict):
                    raise ValueError
            except ValueError:
                bad[tc["id"]] = ToolResult("INVALID_JSON: araç girdisi geçerli bir JSON nesnesi değil; düzeltip tekrar çağırın.", is_error=True)
                args = {}
            blocks.append({"id": tc["id"], "name": name, "input": args, **({"toolset_name": computer.TOOLSET_NAME} if name in computer.MEMBERS else {})})
            ev = {"type": "step", "label": _label(name), "detail": _short(args)}
            ui_entry["steps"].append({"label": ev["label"], "detail": ev["detail"]})
            yield ev

        results: dict[str, ToolResult] = dict(bad)
        runnable = [b for b in blocks if b["id"] not in bad]
        async for ev in _run_tools(runnable, results):
            if ev["type"] in ("step", "step_error"):
                ui_entry["steps"].append({k: ev[k] for k in ("label", "detail", "agent") if k in ev})
            elif ev["type"] == "files":
                new = [f for f in ev["files"] if f not in ui_entry["files"]]
                if not new:
                    continue
                ui_entry["files"].extend(new)
                ev = {**ev, "files": new}
            yield ev

        images = []
        for b in blocks:
            r = results.get(b["id"]) or ToolResult("Araç sonuç üretmedi.", is_error=True)
            content = ("HATA: " + r.content) if r.is_error else (r.content or "OK")
            messages.append({"role": "tool", "tool_call_id": b["id"], "content": content})
            if r.image and ep.vision:
                import base64

                images.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(r.image).decode()}})
            if r.is_error:
                ui_entry["steps"].append({"label": _label(b["name"]) + " (hata)", "detail": r.content[:300]})
                yield {"type": "step_error", "label": _label(b["name"]), "detail": r.content[:300]}
            new_files = [f for f in r.files if f not in ui_entry["files"]]
            if new_files:
                ui_entry["files"].extend(new_files)
                yield {"type": "files", "files": new_files}
        if images:
            # Araç mesajları görsel taşıyamadığı için ekran görüntüleri ayrı kullanıcı mesajıyla verilir
            messages.append({
                "role": "user",
                "content": [{"type": "text", "text": "Ekran görüntüsü (koordinatlar bu görselin pikselleridir):"}] + images,
                "ada_screenshot": True,
            })
        save()
        await asyncio.sleep(0)

    yield {"type": "error", "message": f"Adım sınırına ({max_steps}) ulaşıldı. 'devam et' yazarak sürdürebilirsiniz."}


def describe_error(e: Exception) -> str | None:
    if isinstance(e, openai.APIConnectionError):
        return f"Model sunucusuna bağlanılamadı ({config.LLM_BASE_URL}). Sunucu veya SSH tüneli kapalı olabilir."
    if isinstance(e, openai.AuthenticationError):
        return "Model sunucusu API anahtarını reddetti (ADA_LLM_API_KEY)."
    if isinstance(e, openai.NotFoundError):
        return f"Sunucuda '{config.LLM_MODEL}' modeli bulunamadı (ADA_LLM_MODEL)."
    if isinstance(e, openai.APIStatusError):
        return f"Model sunucusu hatası ({e.status_code}): {e.message}"
    return None
