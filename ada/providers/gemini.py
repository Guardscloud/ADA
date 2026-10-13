"""Google Gemini sağlayıcısı (google-genai SDK).

`agent.agent_loop` ile aynı olayları üretir; araç çalıştırma, onaylar ve ekran kontrolü
agent.py'deki ortak katmanı kullanır. Konuşma geçmişi `types.Content` dict'leri olarak
saklanır; modelin parçaları (düşünce imzaları dahil) değiştirilmeden geri gönderilir.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
import uuid
from typing import AsyncIterator, Callable

import httpx
from google import genai
from google.genai import errors, types

from .. import config
from ..tools import computer

_client: genai.Client | None = None
# Kotası dolan modeller bir süre atlanır (model adı -> tekrar denenebileceği zaman)
_EXHAUSTED: dict[str, float] = {}
EXHAUSTED_COOLDOWN = 30 * 60
OVERLOAD_COOLDOWN = 3 * 60
REQUEST_TIMEOUT_MS = int(os.getenv("ADA_GEMINI_TIMEOUT_S", "90")) * 1000


def _available_models(chain: list[str] | None = None) -> list[str]:
    chain = chain or config.GEMINI_MODELS
    now = time.time()
    models = [m for m in chain if _EXHAUSTED.get(m, 0) <= now]
    return models or [chain[-1]]


class GeminiQuotaExhausted(Exception):
    """Tüm yedek modellerin ücretsiz kotası doldu."""


class GeminiDeclined(Exception):
    """Gemini turu yanıtsız bıraktı (güvenlik filtresi, boş yanıt); yalnızca Qwen yedeği açıkken fırlatılır."""


def reset_client() -> None:
    """Anahtar arayüzden değişince yeni istemci kurulsun."""
    global _client
    _client = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        if not os.getenv("GEMINI_API_KEY"):
            raise RuntimeError("GEMINI_API_KEY ayarlı değil")
        # SDK varsayılanı yoğunlukta 5 kez, katlanan beklemeyle ve süre sınırı olmadan dener; bu da
        # dakikalarca beklemeye yol açar. Az dene, çabuk vazgeç: yedek model zinciri devreye girsin.
        _client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY"),
            http_options=types.HttpOptions(
                timeout=REQUEST_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=2, initial_delay=1.0, max_delay=4.0),
            ),
        )
    return _client


def _schema(s: dict) -> dict:
    """JSON şemasını Gemini'nin kabul ettiği alt kümeye indirger (boş items vb.)."""
    if not isinstance(s, dict):
        return s
    out = {}
    for k, v in s.items():
        if k == "items" and v == {}:
            out[k] = {"type": "string"}
        elif isinstance(v, dict):
            out[k] = {kk: _schema(vv) for kk, vv in v.items()} if k == "properties" else _schema(v)
        else:
            out[k] = v
    return out


def declarations(tools: list[dict]) -> list[types.FunctionDeclaration]:
    """agent.build_tools çıktısını Gemini fonksiyon tanımlarına çevirir."""
    decls = []
    for t in tools:
        if t.get("type") == "computer_toolset_20260801":
            for d in computer.function_declarations():
                decls.append(types.FunctionDeclaration(name=d["name"], description=d["description"], parameters_json_schema=d["parameters"]))
        elif "input_schema" in t:
            decls.append(types.FunctionDeclaration(name=t["name"], description=t["description"], parameters_json_schema=_schema(t["input_schema"])))
    return decls


def user_content(blocks: list[dict]) -> dict:
    """Anthropic biçimli kullanıcı bloklarını (metin + görsel) Gemini Content'ine çevirir."""
    parts = []
    for b in blocks:
        if b["type"] == "text":
            parts.append(types.Part(text=b["text"]))
        elif b["type"] == "image":
            src = b["source"]
            parts.append(types.Part(inline_data=types.Blob(mime_type=src["media_type"], data=base64.b64decode(src["data"]))))
    return types.Content(role="user", parts=parts).model_dump(mode="json", exclude_none=True)


def text_message(text: str) -> dict:
    return types.Content(role="user", parts=[types.Part(text=text)]).model_dump(mode="json", exclude_none=True)


def _shorten(text: str) -> str:
    limit = config.OLD_TOOL_RESULT_CHARS
    if len(text) <= limit:
        return text
    return text[:limit] + f" …[eski araç çıktısı kısaltıldı; {len(text):,} karakterdi. Gerekirse aracı yeniden çağır]"


def _compact_tool_results(contents: list[types.Content]) -> list[types.Content]:
    """Gönderilen kopyada son KEEP_FULL_TOOL_RESULTS araç sonucu tam kalır, eskileri kısaltılır.
    Yalnızca kullanıcı tarafındaki fonksiyon yanıtları değişir; modelin parçaları (imzalar) aynen kalır."""
    seen = 0
    for c in reversed(contents):
        if c.role != "user" or not c.parts:
            continue
        for p in c.parts:
            fr = p.function_response
            if fr is None or not isinstance(fr.response, dict):
                continue
            seen += 1
            if seen > config.KEEP_FULL_TOOL_RESULTS:
                fr.response = {k: _shorten(v) if isinstance(v, str) else v for k, v in fr.response.items()}
    return contents


def _prune_images(contents: list[types.Content]) -> list[types.Content]:
    """Gönderilen kopyada yalnızca son N ekran görüntüsünü tutar (kayıttaki geçmiş değişmez)."""
    keep = config.GEMINI_KEEP_SCREENSHOTS
    seen = 0
    for c in reversed(contents):
        if c.role != "user" or not c.parts:
            continue
        for i in range(len(c.parts) - 1, -1, -1):
            p = c.parts[i]
            if p.inline_data is not None and (p.inline_data.display_name or "").startswith("ekran"):
                seen += 1
                if seen > keep:
                    c.parts[i] = types.Part(text="[eski ekran görüntüsü kaldırıldı]")
    return contents


async def gemini_loop(
    messages: list[dict],
    *,
    system: list[dict],
    tools: list[dict],
    ui_entry: dict,
    save: Callable[[], None],
    max_steps: int,
    models: list[str] | None = None,
    handoff: bool | None = None,
) -> AsyncIterator[dict]:
    """`models`: denenecek model zinciri (varsayılan ADA_GEMINI_MODELS). `handoff`: Gemini yapamazsa
    beklemeden hata fırlat ki başka bir motor devralsın (varsayılan: Qwen yedeği açıksa)."""
    from ..agent import ToolResult, _label, _run_tools, _short

    handoff = config.QWEN_FALLBACK if handoff is None else handoff
    system_text = "\n\n".join(b["text"] for b in system if b.get("type") == "text")
    gen_config = types.GenerateContentConfig(
        system_instruction=system_text,
        tools=[types.Tool(function_declarations=declarations(tools))],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        thinking_config=types.ThinkingConfig(include_thoughts=True),
        max_output_tokens=min(config.MAX_TOKENS, 65_536),
    )
    step = 0
    overload_retries = 0
    while step < max_steps:
        step += 1
        contents = _compact_tool_results(_prune_images([types.Content.model_validate(m) for m in messages]))
        parts: list[types.Part] = []
        finish = None
        candidates = _available_models(models)
        model = candidates[0]
        text_mark = len(ui_entry["text"])  # başarısız denemenin yarım metni geri alınır
        try:
            stream = await get_client().aio.models.generate_content_stream(model=model, contents=contents, config=gen_config)
            async for chunk in stream:
                cand = (chunk.candidates or [None])[0]
                if cand is None:
                    continue
                if cand.finish_reason:
                    finish = str(cand.finish_reason)
                for p in (cand.content.parts if cand.content and cand.content.parts else []):
                    parts.append(p)
                    if p.text and not p.thought and p.text.strip(" ,\n") == "thought":
                        continue  # bazı hafif modeller düşünce işaretini düz metin olarak sızdırıyor
                    if p.text and p.thought:
                        yield {"type": "thinking", "text": p.text}
                    elif p.text:
                        ui_entry["text"] += p.text
                        yield {"type": "text", "text": p.text}
        except errors.ClientError as e:
            ui_entry["text"] = ui_entry["text"][:text_mark]
            if e.code in (429, 404) and len(candidates) > 1:
                # Kota doldu veya model bu hesapta yok: bir süre atla, sıradaki modelle turu tekrarla
                _EXHAUSTED[model] = time.time() + (24 * 3600 if e.code == 404 else EXHAUSTED_COOLDOWN)
                step -= 1
                yield {"type": "text_set", "text": ui_entry["text"]}
                yield {"type": "step", "label": "Model değiştiriliyor", "detail": f"{model} → {candidates[1]}"}
                continue
            if e.code == 429:
                raise GeminiQuotaExhausted(str(e))
            raise
        except (errors.ServerError, httpx.TimeoutException, asyncio.TimeoutError) as e:
            # Yanıtın ortasında da gelebilir; yarım yanıt atılır ve tur baştan denenir
            ui_entry["text"] = ui_entry["text"][:text_mark]
            if isinstance(e, errors.ServerError) and e.code not in (500, 502, 503, 504):
                raise
            step -= 1
            yield {"type": "text_set", "text": ui_entry["text"]}
            if len(candidates) > 1:
                _EXHAUSTED[model] = time.time() + OVERLOAD_COOLDOWN
                yield {"type": "step", "label": "Model yoğun, değiştiriliyor", "detail": f"{model} → {candidates[1]}"}
                continue
            # Tüm modeller yoğun: artan beklemeyle aynı modeli yeniden dene
            overload_retries += 1
            if overload_retries > (1 if handoff else 4):  # yedek varsa uzun bekleme yerine yedeğe geç
                raise
            wait = 5 * overload_retries
            yield {"type": "step", "label": "Gemini yoğun, bekleniyor", "detail": f"{wait} sn sonra tekrar"}
            await asyncio.sleep(wait)
            continue
        overload_retries = 0

        calls = [p for p in parts if p.function_call]
        blocked = bool(finish and any(k in finish for k in ("SAFETY", "PROHIBITED", "BLOCKLIST", "RECITATION")))
        if handoff and not calls and (blocked or not parts):
            # Yarım/boş yanıt geçmişe eklenmez; tur aynı noktadan Qwen ile sürdürülür
            ui_entry["text"] = ui_entry["text"][:text_mark]
            raise GeminiDeclined(f"Gemini yanıt vermedi ({finish})" if blocked else "Gemini boş yanıt döndü")
        if parts:
            messages.append(types.Content(role="model", parts=parts).model_dump(mode="json", exclude_none=True))
        if not calls:
            if finish and "MAX_TOKENS" in finish:
                yield {"type": "error", "message": "Yanıt uzunluk sınırına ulaştı; 'devam et' yazarak sürdürebilirsiniz."}
            elif blocked:
                yield {"type": "error", "message": f"Gemini yanıtı güvenlik filtresi nedeniyle kesti ({finish})."}
            elif not parts:
                yield {"type": "error", "message": "Gemini boş yanıt döndü; tekrar deneyin."}
            save()
            return
        if ui_entry["text"] and not ui_entry["text"].endswith("\n"):
            ui_entry["text"] += "\n\n"
            yield {"type": "text", "text": "\n\n"}

        # Ortak araç katmanının beklediği biçime çevir
        blocks = []
        for p in calls:
            fc = p.function_call
            blocks.append({
                "id": fc.id or uuid.uuid4().hex[:12],
                "call_id": fc.id,
                "name": fc.name,
                "input": dict(fc.args or {}),
                **({"toolset_name": computer.TOOLSET_NAME} if fc.name in computer.MEMBERS else {}),
            })
        for b in blocks:
            ev = {"type": "step", "label": _label(b["name"]), "detail": _short(b["input"])}
            ui_entry["steps"].append({"label": ev["label"], "detail": ev["detail"]})
            yield ev

        results: dict[str, ToolResult] = {}
        async for ev in _run_tools(blocks, results):
            if ev["type"] in ("step", "step_error"):
                ui_entry["steps"].append({k: ev[k] for k in ("label", "detail", "agent") if k in ev})
            elif ev["type"] == "files":
                new = [f for f in ev["files"] if f not in ui_entry["files"]]
                if not new:
                    continue
                ui_entry["files"].extend(new)
                ev = {**ev, "files": new}
            yield ev

        response_parts: list[types.Part] = []
        image_parts: list[types.Part] = []
        for b in blocks:
            r = results.get(b["id"]) or ToolResult("Araç sonuç üretmedi.", is_error=True)
            payload = {"error": r.content} if r.is_error else {"result": r.content or "OK"}
            part = types.Part.from_function_response(name=b["name"], response=payload)
            if b["call_id"]:
                part.function_response.id = b["call_id"]
            response_parts.append(part)
            if r.image:
                # Görseller ayrı parça olarak eklenir: tüm Gemini sürümleri kullanıcı turunda görseli okur
                image_parts.append(types.Part(text=f"[{b['name']} sonucu — ekran görüntüsü; koordinatlar bu görselin pikselleridir]"))
                image_parts.append(types.Part(inline_data=types.Blob(mime_type="image/png", data=r.image, display_name=f"ekran-{b['id']}")))
            if r.is_error:
                ui_entry["steps"].append({"label": _label(b["name"]) + " (hata)", "detail": r.content[:300]})
                yield {"type": "step_error", "label": _label(b["name"]), "detail": r.content[:300]}
            new_files = [f for f in r.files if f not in ui_entry["files"]]
            if new_files:
                ui_entry["files"].extend(new_files)
                yield {"type": "files", "files": new_files}

        messages.append(types.Content(role="user", parts=response_parts + image_parts).model_dump(mode="json", exclude_none=True))
        save()

    yield {"type": "error", "message": f"Adım sınırına ({max_steps}) ulaşıldı. 'devam et' yazarak sürdürebilirsiniz."}
