"""Claude Code motoru: bu bilgisayarda kurulu `claude` komutunu (Claude Code) ADA-AGI'nin motoru yapar.

API anahtarı gerekmez: Claude Code'da açık olan oturum (Claude aboneliği) kullanılır. Düşünen taraf
Claude Code'dur, iş yapan taraf yine ADA'dır: Claude Code'un kendi araçları kapatılır (`--tools ""`),
ADA'nın araçları ona yalnızca bu tur için açılan yerel bir MCP ucundan verilir. Böylece onaylar, işlem
günlüğü, yedekler, günlük sınırlar ve acil durdurma diğer motorlardakiyle aynı yoldan geçer.

`claude_code_loop`, `selfhost_loop` ile aynı sözleşmeye uyar: geçmiş OpenAI Chat Completions biçiminde
tutulur, yalnızca tamamlanan adımlar eklenir ve her adımda `save()` çağrılır. Claude Code her turda
yeniden başlatıldığı için önceki konuşma ona metin dökümü olarak verilir.

Ayarlar (ortam değişkeni): ADA_CLAUDE_CODE (varsayılan açık), ADA_CLAUDE_CODE_MODELS (varsayılan
"opus,sonnet,haiku"), ADA_CLAUDE_CODE_PATH (komutun tam yolu; boşsa kendisi bulur).
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import AsyncIterator, Callable

from .. import config
from ..tools import computer

PROVIDER_ID = "claudecode"
LABEL = "Claude Code (abonelik)"
MCP_SERVER = "ada"
_PREFIX = f"mcp__{MCP_SERVER}__"
_PROTOCOL = "2025-06-18"

# Başka bir Claude Code oturumunun içinden başlatıldıysak (ör. ADA terminalden açıldıysa) o oturuma ait
# değişkenler alt sürece geçmemeli; API anahtarı da geçmez ki abonelik oturumu kullanılsın.
_STRIP_ENV = {
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_PID", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SSE_PORT",
}

_TRANSCRIPT_CHARS = 120_000  # önceki konuşmanın modele verilen dökümü en fazla bu kadar (son kısmı tutulur)
_STEP_RESULT_CHARS = 4000    # devralınan turda önceki motorun araç sonuçları


def enabled() -> bool:
    return os.getenv("ADA_CLAUDE_CODE", "true").strip().lower() in ("1", "true", "yes", "evet", "on")


def models() -> list[str]:
    return [m.strip() for m in os.getenv("ADA_CLAUDE_CODE_MODELS", "opus,sonnet,haiku").split(",") if m.strip()]


class ClaudeCodeError(Exception):
    """Claude Code turu bitiremedi. `rest`: motorun dinleneceği süre (sn); `provider_wide`: tüm modelleri etkiler."""

    def __init__(self, reason: str, rest: float = 600, provider_wide: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.rest = rest
        self.provider_wide = provider_wide


# ---------------------------------------------------------------- komutu bulma ve durum

def find_exe() -> str | None:
    """`claude` komutunun yolu. npm kurulumunda PATH'teki dosya bir .cmd sarmalayıcıdır; asıl program
    yanındaki node_modules klasöründedir ve doğrudan çalıştırılır (cmd.exe tırnaklama sorunları olmasın)."""
    explicit = os.getenv("ADA_CLAUDE_CODE_PATH", "").strip()
    if explicit:
        return explicit if Path(explicit).is_file() else None
    name = "claude.exe" if config.IS_WIN else "claude"
    found = shutil.which("claude")
    cands: list[Path] = []
    if found:
        cands.append(Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / name)
    if config.IS_WIN and os.getenv("APPDATA"):
        cands.append(Path(os.environ["APPDATA"]) / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / name)
    cands += [Path.home() / ".local" / "bin" / name, Path.home() / ".claude" / "local" / name]
    if not config.IS_WIN:
        cands += [Path("/usr/local/bin/claude"), Path("/opt/homebrew/bin/claude")]
    for c in cands:
        if c.is_file():
            return str(c)
    return found


def _child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
    # Araçlar (komut, alt ajan, onay bekleyen işlem) dakikalarca sürebilir
    env.setdefault("MCP_TOOL_TIMEOUT", "3600000")
    env.setdefault("MCP_TIMEOUT", "60000")
    return env


def _flags() -> int:
    return subprocess.CREATE_NO_WINDOW if config.IS_WIN else 0


async def status() -> dict:
    """Claude Code kurulu mu, oturum açık mı? Döner: {"state": "ok" | "nologin" | "missing" | "off", "detail": ...}"""
    if not enabled():
        return {"state": "off", "detail": "Kapalı (ADA_CLAUDE_CODE=false)"}
    exe = find_exe()
    if not exe:
        return {"state": "missing", "detail": "Claude Code bu bilgisayarda kurulu değil"}
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, "auth", "status", stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=_child_env(), creationflags=_flags(),
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            return {"state": "ok", "detail": "Kurulu (oturum durumu okunamadı)", "path": exe}
    except OSError as e:
        return {"state": "missing", "detail": f"Claude Code çalıştırılamadı: {e}"}
    try:
        info = json.loads(out.decode("utf-8", "replace"))
    except ValueError:
        info = None
    if not isinstance(info, dict):
        # Eski sürümlerde bu komut JSON vermez: çıkış kodu yeterli
        ok = proc.returncode == 0
        return {"state": "ok" if ok else "nologin", "path": exe,
                "detail": "Kurulu" if ok else "Kurulu ama oturum açılmamış — terminalde `claude` çalıştırıp /login yazın"}
    if not info.get("loggedIn"):
        return {"state": "nologin", "path": exe,
                "detail": "Kurulu ama oturum açılmamış — terminalde `claude` çalıştırıp /login yazın"}
    plan = info.get("subscriptionType")
    who = info.get("email") or ""
    detail = " · ".join(p for p in (who, f"{plan} aboneliği" if plan else "oturum açık") if p)
    return {"state": "ok", "detail": detail, "path": exe}


def engine(model: str):
    from ..engines import Engine, _specialty

    m = model.lower()
    tier, speed = (5, 2) if re.search(r"opus|fable", m) else (3, 4) if "haiku" in m else (4, 3)
    return Engine(
        id=f"{PROVIDER_ID}:{model}", provider=PROVIDER_ID, model=model, label=f"Claude Code · {model.capitalize()}",
        family=PROVIDER_ID, base_url="", local=False, vision=True, tools=True, tier=tier, speed=speed,
        cost=1, context=200_000, specialty=_specialty(m, True), coords="pixel", max_tokens=32000,
    )


async def scan() -> tuple[dict, list]:
    st = await status()
    return st, ([engine(m) for m in models()] if st["state"] in ("ok", "nologin") else [])


# ---------------------------------------------------------------- geçmişi isteme çevirme

def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f" …[kısaltıldı; {len(text):,} karakterdi]"


def _text_of(content) -> str:
    """OpenAI biçimli içerikten metin (görseller yer tutucuyla)."""
    if isinstance(content, str):
        return content
    out = []
    for item in content or []:
        if item.get("type") == "text":
            out.append(item["text"])
        elif item.get("type") == "image_url":
            out.append("[görsel]")
    return "\n".join(out)


def _transcript(messages: list[dict], result_chars: int) -> str:
    lines = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            if m.get("ada_screenshot"):
                lines.append("[ekran görüntüsü — gerekiyorsa yeniden al]")
            else:
                lines.append("Kullanıcı: " + _text_of(m.get("content")))
        elif role == "assistant":
            if m.get("content"):
                lines.append("ADA: " + m["content"])
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                lines.append(f"[araç çağrısı: {fn.get('name')} {_clip(fn.get('arguments') or '', 400)}]")
        elif role == "tool":
            lines.append("[araç sonucu: " + _clip(str(m.get("content") or ""), result_chars) + "]")
    return "\n".join(lines)


def build_prompt(messages: list[dict]) -> list[dict]:
    """OpenAI biçimli geçmişi tek bir kullanıcı mesajının içerik bloklarına çevirir: önceki konuşmanın
    dökümü, şimdiki istek (görselleriyle) ve — tur başka bir motordan devralındıysa — o ana dek yapılanlar."""
    idx = next((i for i in range(len(messages) - 1, -1, -1)
                if messages[i].get("role") == "user" and not messages[i].get("ada_screenshot")), None)
    if idx is None:
        return [{"type": "text", "text": _transcript(messages, _STEP_RESULT_CHARS) or "Devam et."}]
    blocks: list[dict] = []
    earlier = _transcript(messages[:idx], config.OLD_TOOL_RESULT_CHARS)
    if earlier:
        if len(earlier) > _TRANSCRIPT_CHARS:
            earlier = "…[konuşmanın başı kısaltıldı]\n" + earlier[-_TRANSCRIPT_CHARS:]
        blocks.append({"type": "text", "text": (
            "<onceki_konusma>\n" + earlier + "\n</onceki_konusma>\n\n"
            "Yukarıdaki, bu görevin şimdiye kadarki dökümüdür (bilgi içindir). Şimdiki istek aşağıdadır:"
        )})
    content = messages[idx].get("content")
    for item in ([{"type": "text", "text": content}] if isinstance(content, str) else (content or [])):
        if item.get("type") == "text":
            blocks.append({"type": "text", "text": item["text"]})
        elif item.get("type") == "image_url":
            head, _, data = item["image_url"]["url"].partition(",")
            mime = head.removeprefix("data:").split(";")[0] or "image/png"
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
    after = _transcript(messages[idx + 1:], _STEP_RESULT_CHARS)
    if after:
        blocks.append({"type": "text", "text": (
            "<bu_istekte_yapilanlar>\n" + after + "\n</bu_istekte_yapilanlar>\n\n"
            "Bu istek için yukarıdaki adımlar zaten yapıldı. Kaldığın yerden devam et; tamamlanan adımları tekrarlama."
        )})
    if not any(b["type"] == "text" for b in blocks):
        blocks.append({"type": "text", "text": "Ekteki görsele bak."})
    return blocks


def tool_specs(tools: list[dict]) -> list[dict]:
    """ADA araç tanımlarını MCP araç tanımlarına çevirir (ekran araç seti üyeleri tek tek açılır)."""
    specs = []
    for t in tools:
        if t.get("type") == "computer_toolset_20260801":
            specs += [{"name": d["name"], "description": d.get("description", ""), "inputSchema": d["parameters"]}
                      for d in computer.function_declarations()]
        elif "input_schema" in t:
            specs.append({"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]})
    return specs


_TOOLS_NOTE = (
    "\n\nÇALIŞMA ORTAMI: ADA'nın motoru olarak çalışıyorsun. Kullanabileceğin araçlar yalnızca `mcp__ada__` "
    "önekiyle sunulanlardır (talimatlarda `run_shell` olarak geçen araç sende `mcp__ada__run_shell` adındadır). "
    "Başka aracın yoktur; Bash, Read, Write gibi araçları arama. Yanıtlarını kullanıcının dilinde yaz."
)


# ---------------------------------------------------------------- hata sınıflandırma

def classify_error(message: str, *, limit_reset: float | None = None) -> ClaudeCodeError:
    """Claude Code'un hata metninden neden ve dinlenme süresi çıkarır."""
    msg = (message or "").strip()
    low = msg.lower()
    if limit_reset or re.search(r"usage limit|hit your limit|limit reached|out of extra usage|rate.?limit|\b429\b", low):
        wait = max(300.0, (limit_reset or 0) - time.time()) if limit_reset else 3600.0
        hours = round(wait / 3600, 1)
        return ClaudeCodeError(f"abonelik kullanım sınırı doldu ({hours} sa sonra açılır)" if wait >= 3600
                               else "abonelik kullanım sınırı doldu", wait)
    if re.search(r"/login|not logged in|log in|logged out|oauth|invalid api key|authentication|unauthorized|\b401\b", low):
        return ClaudeCodeError("Claude Code oturumu açık değil (terminalde `claude` çalıştırıp /login yazın)", 6 * 3600, True)
    if re.search(r"model.{0,40}(not found|not available|does not exist|not exist|no access|unavailable)|\b404\b", low):
        return ClaudeCodeError("bu model abonelikte kullanılamıyor", 24 * 3600)
    if re.search(r"overloaded|\b(500|502|503|504|529)\b|econn|enotfound|etimedout|network|fetch failed|timed? ?out|connection", low):
        return ClaudeCodeError("Anthropic sunucusuna ulaşılamadı", 180, True)
    return ClaudeCodeError("Claude Code hatası: " + (msg[:160] or "bilinmeyen"), 600)


# ---------------------------------------------------------------- tur

class _Turn:
    """Bir turun durumu: Claude Code'un çıktısı ve MCP ucundan gelen araç çağrıları burada buluşur."""

    def __init__(self, messages: list[dict], tools: list[dict], ui_entry: dict, save: Callable[[], None], max_steps: int):
        self.messages = messages
        self.ui_entry = ui_entry
        self.save = save
        self.max_steps = max_steps
        self.specs = tool_specs(tools)
        self.names = {s["name"] for s in self.specs}
        self.token = secrets.token_urlsafe(24)
        self.events: asyncio.Queue = asyncio.Queue()
        self.lock = asyncio.Lock()  # araçlar modelin verdiği sırayla, tek tek çalışır
        self.context = contextvars.copy_context()  # araçlar turun bağlamıyla çalışır (arka plan işi mi, koordinat biçimi...)
        self.text = ""          # son kaydedilen adımdan beri gelen yanıt metni
        self.steps = 0
        self.limit_hit = False
        self.limit_reset: float | None = None
        self.result: dict | None = None
        self.tasks: set[asyncio.Task] = set()

    def emit(self, event: dict) -> None:
        self.events.put_nowait(event)

    # ---- Claude Code'un çıktısı (stream-json satırları)

    def on_line(self, ev: dict) -> None:
        kind = ev.get("type")
        if ev.get("parent_tool_use_id"):
            return
        if kind == "stream_event":
            e = ev.get("event") or {}
            delta = e.get("delta") or {}
            if e.get("type") == "content_block_delta":
                if delta.get("type") == "text_delta" and delta.get("text"):
                    self.text += delta["text"]
                    self.ui_entry["text"] += delta["text"]
                    self.emit({"type": "text", "text": delta["text"]})
                elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                    self.emit({"type": "thinking", "text": delta["thinking"]})
        elif kind == "rate_limit_event":
            info = ev.get("rate_limit_info") or {}
            if info.get("status") == "rejected" and info.get("resetsAt"):
                self.limit_reset = float(info["resetsAt"])
        elif kind == "result":
            self.result = ev

    # ---- MCP (Streamable HTTP, yalnızca bu tur için 127.0.0.1'de)

    async def handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            while True:
                request = await _read_request(reader)
                if request is None:
                    return
                method, path, body = request
                if path.rstrip("/") != f"/mcp/{self.token}":
                    await _respond(writer, 404, b"")
                    return
                if method != "POST":
                    await _respond(writer, 405, b"", extra="Allow: POST\r\n")
                    continue
                try:
                    payload = json.loads(body or b"null")
                except ValueError:
                    await _respond(writer, 400, _dumps(_error(None, -32700, "Parse error")))
                    continue
                batch = payload if isinstance(payload, list) else [payload]
                if any(isinstance(m, dict) and m.get("method") == "tools/call" for m in batch):
                    # Araç dakikalarca sürebilir: başlıklar hemen gider, sonuç hazır olunca SSE ile iletilir
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
                                 b"Connection: close\r\n\r\n")
                    await writer.drain()
                    for m in batch:
                        job = asyncio.ensure_future(self.rpc(m))
                        while not job.done():
                            await asyncio.wait({job}, timeout=15)
                            if not job.done():
                                writer.write(b": bekliyor\n\n")
                                await writer.drain()
                        reply = job.result()
                        if reply is not None:
                            writer.write(b"event: message\ndata: " + _dumps(reply) + b"\n\n")
                            await writer.drain()
                    return
                replies = [r for r in [await self.rpc(m) for m in batch] if r is not None]
                if not replies:
                    await _respond(writer, 202, b"")
                else:
                    await _respond(writer, 200, _dumps(replies if isinstance(payload, list) else replies[0]))
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            pass
        finally:
            self.tasks.discard(task)
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def rpc(self, msg) -> dict | None:
        if not isinstance(msg, dict) or "method" not in msg:
            return None  # istemcinin yanıtları/bozuk iletiler
        mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if "id" not in msg:
            return None  # bildirim (notifications/initialized vb.)
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": params.get("protocolVersion") or _PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ada", "version": "1"},
            }}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": mid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": self.specs}}
        if method == "tools/call":
            args = params.get("arguments")
            # Araç turun bağlamının bir kopyasında çalışır; bağlantı görevi kendi bağlamındadır
            job = asyncio.get_running_loop().create_task(
                self.call(str(params.get("name") or ""), args if isinstance(args, dict) else {}), context=self.context.copy())
            self.tasks.add(job)
            try:
                result = await job
            finally:
                self.tasks.discard(job)
            return {"jsonrpc": "2.0", "id": mid, "result": result}
        if method in ("resources/list", "prompts/list", "resources/templates/list"):
            key = {"resources/list": "resources", "prompts/list": "prompts", "resources/templates/list": "resourceTemplates"}[method]
            return {"jsonrpc": "2.0", "id": mid, "result": {key: []}}
        return _error(mid, -32601, f"Method not found: {method}")

    async def call(self, name: str, args: dict) -> dict:
        from ..agent import ToolResult, _label, _run_tools, _short

        name = name.removeprefix(_PREFIX)
        async with self.lock:
            if self.steps >= self.max_steps:
                self.limit_hit = True
                return _mcp_result("Adım sınırına ulaşıldı. Başka araç çağırma; şimdiye kadar yapılanları kısaca özetle.", True)
            if name not in self.names:
                return _mcp_result(f"Bilinmeyen araç: {name}", True)
            self.steps += 1
            ui = self.ui_entry
            block = {"id": f"call_{uuid.uuid4().hex[:12]}", "name": name, "input": args,
                     **({"toolset_name": computer.TOOLSET_NAME} if name in computer.MEMBERS else {})}
            text, self.text = self.text, ""
            if ui["text"] and not ui["text"].endswith("\n"):
                ui["text"] += "\n\n"
                self.emit({"type": "text", "text": "\n\n"})
            ev = {"type": "step", "label": _label(name), "detail": _short(args)}
            ui["steps"].append({"label": ev["label"], "detail": ev["detail"]})
            self.emit(ev)

            results: dict[str, ToolResult] = {}
            async for ev in _run_tools([block], results):
                if ev["type"] in ("step", "step_error"):
                    ui["steps"].append({k: ev[k] for k in ("label", "detail", "agent") if k in ev})
                elif ev["type"] == "files":
                    new = [f for f in ev["files"] if f not in ui["files"]]
                    if not new:
                        continue
                    ui["files"].extend(new)
                    ev = {**ev, "files": new}
                self.emit(ev)

            r = results.get(block["id"]) or ToolResult("Araç sonuç üretmedi.", is_error=True)
            content = ("HATA: " + r.content) if r.is_error else (r.content or "OK")
            self.messages.append({"role": "assistant", "content": text or None, "tool_calls": [{
                "id": block["id"], "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }]})
            self.messages.append({"role": "tool", "tool_call_id": block["id"], "content": content})
            image = base64.b64encode(r.image).decode() if r.image else None
            if image:
                self.messages.append({
                    "role": "user", "ada_screenshot": True,
                    "content": [{"type": "text", "text": "Ekran görüntüsü (koordinatlar bu görselin pikselleridir):"},
                                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + image}}],
                })
            if r.is_error:
                ui["steps"].append({"label": _label(name) + " (hata)", "detail": r.content[:300]})
                self.emit({"type": "step_error", "label": _label(name), "detail": r.content[:300]})
            new_files = [f for f in r.files if f not in ui["files"]]
            if new_files:
                ui["files"].extend(new_files)
                self.emit({"type": "files", "files": new_files})
            self.save()
            return _mcp_result(content, r.is_error, image)


def _mcp_result(text: str, is_error: bool = False, image_b64: str | None = None) -> dict:
    content: list[dict] = [{"type": "text", "text": text}]
    if image_b64:
        content.append({"type": "image", "data": image_b64, "mimeType": "image/png"})
    return {"content": content, "isError": is_error}


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _dumps(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, bytes] | None:
    line = await reader.readline()
    if not line.strip():
        return None
    parts = line.decode("latin-1").split()
    if len(parts) < 2:
        raise ValueError("bozuk istek satırı")
    headers: dict[str, str] = {}
    while True:
        h = await reader.readline()
        if h in (b"\r\n", b"\n", b""):
            break
        key, _, value = h.decode("latin-1").partition(":")
        headers[key.strip().lower()] = value.strip()
    if "chunked" in headers.get("transfer-encoding", "").lower():
        body = b""
        while True:
            size = int((await reader.readline()).split(b";")[0].strip() or b"0", 16)
            if size == 0:
                while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                    pass
                break
            body += await reader.readexactly(size)
            await reader.readexactly(2)
    else:
        body = await reader.readexactly(int(headers.get("content-length") or 0))
    return parts[0].upper(), parts[1].split("?")[0], body


_REASONS = {200: "OK", 202: "Accepted", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}


async def _respond(writer: asyncio.StreamWriter, code: int, body: bytes, extra: str = "") -> None:
    head = f"HTTP/1.1 {code} {_REASONS.get(code, 'OK')}\r\nContent-Length: {len(body)}\r\n{extra}"
    if body:
        head += "Content-Type: application/json\r\n"
    writer.write(head.encode("latin-1") + b"\r\n" + body)
    await writer.drain()


def _args(exe: str, model: str, tmp: Path, has_tools: bool) -> list[str]:
    args = [
        exe, "-p", "--output-format", "stream-json", "--input-format", "stream-json", "--verbose",
        "--include-partial-messages", "--no-session-persistence", "--disable-slash-commands",
        "--model", model, "--system-prompt-file", str(tmp / "system.txt"),
        "--tools", "",  # Claude Code'un kendi araçları kapalı: her iş ADA'nın araç katmanından geçer
        "--strict-mcp-config", "--permission-prompts", "none",
    ]
    if has_tools:
        args += ["--mcp-config", str(tmp / "mcp.json"), "--allowedTools", f"mcp__{MCP_SERVER}"]
    return args


async def claude_code_loop(
    messages: list[dict],
    *,
    system: list[dict],
    tools: list[dict],
    ui_entry: dict,
    save: Callable[[], None],
    max_steps: int,
    model: str,
) -> AsyncIterator[dict]:
    """Turu Claude Code ile yürütür. `messages` OpenAI biçimindedir ve tamamlanan her adımda güncellenir."""
    exe = find_exe()
    if not exe:
        raise ClaudeCodeError("Claude Code kurulu değil", 6 * 3600, True)
    turn = _Turn(messages, tools, ui_entry, save, max_steps)
    tmp = Path(tempfile.mkdtemp(prefix="ada-cc-"))
    server = proc = reader = None
    stderr_tail: list[str] = []
    try:
        system_text = "\n\n".join(b["text"] for b in system if b.get("type") == "text")
        (tmp / "system.txt").write_text(system_text + (_TOOLS_NOTE if turn.specs else ""), encoding="utf-8")
        if turn.specs:
            server = await asyncio.start_server(turn.handle_conn, "127.0.0.1", 0, limit=64 * 1024 * 1024)
            port = server.sockets[0].getsockname()[1]
            (tmp / "mcp.json").write_text(json.dumps({"mcpServers": {MCP_SERVER: {
                "type": "http", "url": f"http://127.0.0.1:{port}/mcp/{turn.token}"}}}), encoding="utf-8")
        prompt = {"type": "user", "message": {"role": "user", "content": build_prompt(messages)}}
        try:
            proc = await asyncio.create_subprocess_exec(
                *_args(exe, model, tmp, bool(turn.specs)), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=str(config.WORKSPACE), env=_child_env(), creationflags=_flags(),
                limit=64 * 1024 * 1024,  # araç sonuçlarındaki görseller tek satırda gelir
            )
        except OSError as e:
            raise ClaudeCodeError(f"Claude Code başlatılamadı: {e}", 3600, True) from e

        async def pump() -> None:
            try:
                proc.stdin.write(_dumps(prompt) + b"\n")
                await proc.stdin.drain()
                proc.stdin.close()
            except (ConnectionError, OSError):
                pass

            async def drain_stderr() -> None:
                async for raw in proc.stderr:
                    stderr_tail.append(raw.decode("utf-8", "replace").rstrip())
                    del stderr_tail[:-20]

            err_task = asyncio.ensure_future(drain_stderr())
            try:
                async for raw in proc.stdout:
                    try:
                        ev = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(ev, dict):
                        turn.on_line(ev)
                await proc.wait()
                await err_task
            finally:
                err_task.cancel()
                turn.emit({"type": "_eof"})

        reader = asyncio.ensure_future(pump())
        while True:
            ev = await turn.events.get()
            if ev["type"] == "_eof":
                break
            yield ev
        await reader  # okuma sırasında beklenmeyen hata olduysa burada yükselir

        result = turn.result
        if result is None or result.get("is_error"):
            detail = str((result or {}).get("result") or "") or " ".join(stderr_tail[-5:])
            if result is None and not detail:
                detail = f"beklenmedik biçimde kapandı (çıkış kodu {proc.returncode})"
            raise classify_error(detail, limit_reset=turn.limit_reset)

        if turn.text:
            messages.append({"role": "assistant", "content": turn.text})
        if turn.limit_hit:
            yield {"type": "error", "limit": "steps", "message": f"Adım sınırına ({max_steps}) ulaşıldı. 'devam et' yazarak sürdürebilirsiniz."}
        elif not turn.text and turn.steps == 0:
            yield {"type": "error", "message": "Model boş yanıt döndü; tekrar deneyin."}
        save()
    finally:
        for t in list(turn.tasks):
            t.cancel()
        if reader is not None and not reader.done():
            reader.cancel()
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        if server is not None:
            server.close()
        shutil.rmtree(tmp, ignore_errors=True)


async def ask(model: str, prompt: str) -> str:
    """Araçsız kısa bir soru (Ayarlar'daki "Dene" düğmesi için)."""
    ui = {"text": "", "steps": [], "files": []}
    messages = [{"role": "user", "content": prompt}]
    async for _ in claude_code_loop(messages, system=[{"type": "text", "text": "Kısa ve net yanıt ver."}], tools=[],
                                    ui_entry=ui, save=lambda: None, max_steps=1, model=model):
        pass
    return ui["text"].strip()
