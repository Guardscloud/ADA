"""OpenCode motoru: bu bilgisayarda kurulu `opencode` komutunu (OpenCode CLI) ADA-AGI'nin motoru yapar.

OpenCode'da bağlanmış sağlayıcılar (`opencode auth login`: OpenCode Zen/Go, GitHub Copilot, ChatGPT
aboneliği, kendi anahtarları...) ADA'ya ayrıca anahtar girmeden motor olarak gelir. Claude Code motorundaki
gibi düşünen taraf OpenCode, iş yapan taraf ADA'dır: OpenCode'un kendi araçları izinle kapatılır
(`"*": "deny"`), ADA'nın araçları yalnızca bu tur için açılan yerel MCP ucundan verilir (bkz. claude_code._Turn).

Ücretsiz modeller (big-pickle, *-free) yalnızca OpenCode kendi araçlarıyla çalışırken kullanılabilir;
araçları kapatılınca sunucu reddeder (403). Bu modellerde OpenCode kendi araçlarıyla (bash, edit, read...)
olduğu gibi çalışır, ADA'nın araçları da yine MCP'den verilir. OpenCode'un araçları çalışma alanının dışına
erişemez (`external_directory: deny`); yaptığı her işlem ADA'nın işlem günlüğüne ve adımlarına yazılır, ama
ADA'nın onay/yedek katmanından geçmez. Bu yüzden ücretsiz motorlar varsayılan kapalı gelir.

Ayarlar (ortam değişkeni): ADA_OPENCODE (varsayılan açık), ADA_OPENCODE_MODELS (virgülle `sağlayıcı/model`;
boşsa `opencode models` listesi), ADA_OPENCODE_PATH (komutun tam yolu; boşsa kendisi bulur).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import AsyncIterator, Callable

from .. import config
from .claude_code import MCP_SERVER, ClaudeCodeError, _child_env, _flags, _Turn, build_prompt

PROVIDER_ID = "opencode"
LABEL = "OpenCode (CLI)"

# Yerel sunucular ADA'da zaten ayrı motor; OpenCode üzerinden ikinci kez alınmaz
_SKIP_PROVIDERS = {"ollama", "lmstudio", "llama.cpp", "llamacpp", "jan", "vllm"}
_FREE = re.compile(r"^opencode/(big-pickle|.*-free)$")


def enabled() -> bool:
    return os.getenv("ADA_OPENCODE", "true").strip().lower() in ("1", "true", "yes", "evet", "on")


class OpenCodeError(ClaudeCodeError):
    """OpenCode turu bitiremedi (yönlendirici Claude Code hatası gibi ele alır: neden + dinlenme süresi)."""


# ---------------------------------------------------------------- komutu bulma ve durum

def find_exe() -> str | None:
    """`opencode` komutunun yolu. npm kurulumunda PATH'teki dosya .cmd sarmalayıcıdır; asıl program
    opencode-ai paketinin bin klasöründedir ve doğrudan çalıştırılır."""
    explicit = os.getenv("ADA_OPENCODE_PATH", "").strip()
    if explicit:
        return explicit if Path(explicit).is_file() else None
    name = "opencode.exe" if config.IS_WIN else "opencode"
    found = shutil.which("opencode")
    cands: list[Path] = []
    if found:
        cands.append(Path(found).parent / "node_modules" / "opencode-ai" / "bin" / name)
    if config.IS_WIN and os.getenv("APPDATA"):
        cands.append(Path(os.environ["APPDATA"]) / "npm" / "node_modules" / "opencode-ai" / "bin" / name)
    cands += [Path.home() / ".opencode" / "bin" / name]
    if not config.IS_WIN:
        cands += [Path("/usr/local/bin/opencode"), Path("/opt/homebrew/bin/opencode")]
    for c in cands:
        if c.is_file():
            return str(c)
    return found


def _env(config_file: Path | None = None) -> dict[str, str]:
    env = _child_env()
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
    if config_file is not None:
        env["OPENCODE_CONFIG"] = str(config_file)
    return env


async def list_models(exe: str) -> list[str]:
    """`opencode models`: bağlı sağlayıcıların modelleri (`sağlayıcı/model`)."""
    proc = await asyncio.create_subprocess_exec(
        exe, "models", stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=_env(), creationflags=_flags(),
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    lines = re.sub(r"\x1b\[[0-9;]*m", "", out.decode("utf-8", "replace")).splitlines()
    return [ln.strip() for ln in lines if re.fullmatch(r"[\w.\-]+/\S+", ln.strip())]


def is_free(model: str) -> bool:
    return bool(_FREE.match(model))


def usable(models: list[str]) -> list[str]:
    # Muse Spark "contributor" sürümlerinde istemler eğitim için kullanılır: alınmaz
    return [m for m in models if m.split("/", 1)[0] not in _SKIP_PROVIDERS and "contributor" not in m]


async def status() -> tuple[dict, list[str]]:
    """Döner: ({"state": "ok" | "nologin" | "missing" | "off", "detail": ...}, kullanılabilir modeller)."""
    if not enabled():
        return {"state": "off", "detail": "Kapalı (ADA_OPENCODE=false)"}, []
    exe = find_exe()
    if not exe:
        return {"state": "missing", "detail": "OpenCode bu bilgisayarda kurulu değil"}, []
    explicit = [m.strip() for m in os.getenv("ADA_OPENCODE_MODELS", "").split(",") if m.strip()]
    if explicit:
        return {"state": "ok", "detail": f"{len(explicit)} model (ADA_OPENCODE_MODELS)", "path": exe}, explicit
    try:
        models = usable(await list_models(exe))
    except (OSError, asyncio.TimeoutError) as e:
        return {"state": "missing", "detail": f"OpenCode çalıştırılamadı: {type(e).__name__}"}, []
    if not models:
        return {"state": "nologin", "path": exe,
                "detail": "Kurulu ama model bulunamadı — terminalde `opencode auth login` çalıştırın"}, []
    free = sum(is_free(m) for m in models)
    detail = f"{len(models)} model" + (f" ({free} ücretsiz)" if free else "")
    return {"state": "ok", "detail": detail, "path": exe}, models


def engine(model: str):
    from ..engines import Provider, cloud_engine

    shell = Provider(PROVIDER_ID, LABEL, PROVIDER_ID, "")
    e = cloud_engine(shell, model.split("/", 1)[-1])
    e.id, e.model, e.label = f"{PROVIDER_ID}:{model}", model, f"OpenCode · {model}"
    e.context, e.max_tokens = 128_000, 32000
    if is_free(model):
        e.label += " (ücretsiz · OpenCode araçlarıyla)"
        e.cost = 0
    return e


async def scan() -> tuple[dict, list]:
    from ..engines import _default_cloud_enable

    st, models = await status()
    found = [engine(m) for m in models]
    _default_cloud_enable(found)
    for e in found:
        if is_free(e.model):
            e.enabled = False  # kendi araçları ADA'nın onay katmanından geçmez: kullanıcı bilerek açar
    return st, found


# ---------------------------------------------------------------- hata sınıflandırma

def classify_error(message: str) -> OpenCodeError:
    msg = (message or "").strip()
    low = msg.lower()
    if "free tier" in low:
        return OpenCodeError("OpenCode ücretsiz katmanı bu isteği reddetti", 24 * 3600)
    if re.search(r"rate.?limit|quota|usage limit|\b429\b|insufficient|credit|\b402\b", low):
        return OpenCodeError("kota/kredi doldu", 3600)
    if re.search(r"unauthori[sz]ed|api key|authentication|not authenticated|auth login|\b401\b|\b403\b", low):
        return OpenCodeError("OpenCode'da bu sağlayıcının oturumu yok (terminalde `opencode auth login`)", 6 * 3600)
    if re.search(r"model.{0,40}(not found|not available|does not exist|unavailable)|providermodelnotfound|\b404\b", low):
        return OpenCodeError("model OpenCode'da bulunamadı", 24 * 3600)
    if re.search(r"overloaded|\b(500|502|503|504|529)\b|econn|enotfound|etimedout|network|fetch failed|timed? ?out|connection", low):
        return OpenCodeError("sağlayıcıya ulaşılamadı", 180)
    return OpenCodeError("OpenCode hatası: " + (msg[:160] or "bilinmeyen"), 600)


# ---------------------------------------------------------------- tur

class _OpenCodeTurn(_Turn):
    """OpenCode `run --format json` satırları: tamamlanan metin parçaları, adım sonları ve hatalar."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.finished = False
        self.error: str | None = None

    def on_line(self, ev: dict) -> None:
        kind = ev.get("type")
        part = ev.get("part") or {}
        if kind == "text" and part.get("text"):
            text = part["text"]
            if self.ui_entry["text"] and not self.ui_entry["text"].endswith("\n"):
                text = "\n\n" + text
            self.text += text
            self.ui_entry["text"] += text
            self.emit({"type": "text", "text": text})
        elif kind == "reasoning" and part.get("text"):
            self.emit({"type": "thinking", "text": part["text"]})
        elif kind == "tool_use" and not str(part.get("tool") or "").startswith(MCP_SERVER + "_"):
            self.native_tool(part)
        elif kind == "step_finish" and part.get("reason") != "tool-calls":
            self.finished = True
        elif kind == "error":
            err = ev.get("error") or {}
            self.error = str((err.get("data") or {}).get("message") or ev.get("message") or err.get("name") or "hata")

    def native_tool(self, part: dict) -> None:
        """OpenCode'un kendi aracı (ücretsiz modellerde) bitti: adım olarak gösterilir, işlem günlüğüne ve
        geçmişe yazılır ki sonraki tur/motor ne yapıldığını bilsin. CLI yalnızca biten araçları bildirir."""
        from .. import safety

        tool = str(part.get("tool") or "araç")
        state = part.get("state") or {}
        args = state.get("input") if isinstance(state.get("input"), dict) else {}
        failed = state.get("status") == "error"
        output = str(state.get("error") if failed else state.get("output") or "")
        safety.log(f"opencode.{tool}", args, ok=not failed, result=output)
        label = f"OpenCode · {tool}" + (" (hata)" if failed else "")
        detail = str(state.get("title") or "")[:300] or json.dumps(args, ensure_ascii=False)[:300]
        self.ui_entry["steps"].append({"label": label, "detail": detail})
        self.emit({"type": "step_error" if failed else "step", "label": label, "detail": detail})
        text, self.text = self.text, ""
        cid = str(part.get("callID") or part.get("id") or f"oc_{len(self.messages)}")
        self.messages.append({"role": "assistant", "content": text or None, "tool_calls": [{
            "id": cid, "type": "function",
            "function": {"name": f"opencode_{tool}", "arguments": json.dumps(args, ensure_ascii=False)},
        }]})
        self.messages.append({"role": "tool", "tool_call_id": cid, "content": ("HATA: " if failed else "") + output[:8000]})
        self.save()


def _config(system_file: Path, mcp_url: str | None, native: bool = False) -> dict:
    # native=False: OpenCode'un kendi araçları kapalı, her iş ADA'nın araç katmanından geçer.
    # native=True (ücretsiz modeller): kendi araçları açık ama çalışma alanının dışına çıkamaz.
    permission = {"*": "allow", "external_directory": "deny"} if native else {"*": "deny", f"{MCP_SERVER}_*": "allow"}
    cfg: dict = {
        "$schema": "https://opencode.ai/config.json",
        "share": "disabled", "autoupdate": False, "snapshot": False,
        "agent": {"ada": {"mode": "primary", "prompt": "{file:" + system_file.as_posix() + "}", "permission": permission}},
    }
    if mcp_url:
        cfg["mcp"] = {MCP_SERVER: {"type": "remote", "url": mcp_url, "enabled": True, "oauth": False, "timeout": 60000}}
    return cfg


_TOOLS_NOTE = (
    "\n\nÇALIŞMA ORTAMI: ADA'nın motoru olarak çalışıyorsun. Kullanabileceğin araçlar yalnızca `ada_` önekiyle "
    "sunulanlardır (talimatlarda `run_shell` olarak geçen araç sende `ada_run_shell` adındadır). Başka aracın "
    "yoktur; bash, read, write gibi araçları arama. Yanıtlarını kullanıcının dilinde yaz."
)
_NATIVE_NOTE = (
    "\n\nÇALIŞMA ORTAMI: ADA'nın motoru olarak çalışıyorsun. Kendi araçların (bash, read, edit, write...) yalnızca "
    "çalışma klasöründe çalışır; klasör dışına erişme. Ekran, uygulama, e-posta gibi işler için `ada_` önekli "
    "araçları kullan (talimatlarda `run_shell` olarak geçen araç sende `ada_run_shell` adındadır); onay gerektiren "
    "işlemler için mutlaka `ada_` araçlarını seç. Yanıtlarını kullanıcının dilinde yaz."
)


def _split_prompt(blocks: list[dict], tmp: Path) -> tuple[str, list[Path]]:
    """İçerik bloklarını düz metne ve ek dosyalara (görseller) ayırır."""
    texts, files = [], []
    for b in blocks:
        if b["type"] == "text":
            texts.append(b["text"])
        elif b["type"] == "image":
            ext = b["source"]["media_type"].split("/")[-1].replace("jpeg", "jpg")
            f = tmp / f"img{len(files) + 1}.{ext}"
            f.write_bytes(base64.b64decode(b["source"]["data"]))
            files.append(f)
    return "\n\n".join(texts), files


def _args(exe: str, model: str, files: list[Path]) -> list[str]:
    # --pure: kullanıcının OpenCode eklentileri bu turlara karışmaz
    args = [exe, "run", "--format", "json", "--pure", "--agent", "ada", "-m", model]
    for f in files:
        args += ["-f", str(f)]
    return args


async def opencode_loop(
    messages: list[dict],
    *,
    system: list[dict],
    tools: list[dict],
    ui_entry: dict,
    save: Callable[[], None],
    max_steps: int,
    model: str,
) -> AsyncIterator[dict]:
    """Turu OpenCode ile yürütür. `messages` OpenAI biçimindedir ve tamamlanan her adımda güncellenir."""
    exe = find_exe()
    if not exe:
        raise OpenCodeError("OpenCode kurulu değil", 6 * 3600, True)
    turn = _OpenCodeTurn(messages, tools, ui_entry, save, max_steps)
    tmp = Path(tempfile.mkdtemp(prefix="ada-oc-"))
    server = proc = reader = None
    stderr_tail: list[str] = []
    try:
        system_text = "\n\n".join(b["text"] for b in system if b.get("type") == "text")
        native = is_free(model)
        note = _NATIVE_NOTE if native else _TOOLS_NOTE if turn.specs else ""
        (tmp / "system.txt").write_text(system_text + note, encoding="utf-8")
        mcp_url = None
        if turn.specs:
            server = await asyncio.start_server(turn.handle_conn, "127.0.0.1", 0, limit=64 * 1024 * 1024)
            mcp_url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/mcp/{turn.token}"
        cfg = _config(tmp / "system.txt", mcp_url, native)
        (tmp / "opencode.json").write_text(json.dumps(cfg), encoding="utf-8")
        text, files = _split_prompt(build_prompt(messages), tmp)
        try:
            proc = await asyncio.create_subprocess_exec(
                *_args(exe, model, files), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=str(config.WORKSPACE), env=_env(tmp / "opencode.json"), creationflags=_flags(),
                limit=64 * 1024 * 1024,
            )
        except OSError as e:
            raise OpenCodeError(f"OpenCode başlatılamadı: {e}", 3600, True) from e

        async def pump() -> None:
            try:
                proc.stdin.write(text.encode("utf-8"))  # istem stdin'den: komut satırı uzunluk sınırına takılmaz
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
        await reader

        if turn.error or (not turn.finished and proc.returncode):
            detail = turn.error or " ".join(stderr_tail[-5:]) or f"beklenmedik biçimde kapandı (çıkış kodu {proc.returncode})"
            raise classify_error(detail)

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
    async for _ in opencode_loop(messages, system=[{"type": "text", "text": "Kısa ve net yanıt ver."}], tools=[],
                                 ui_entry=ui, save=lambda: None, max_steps=1, model=model):
        pass
    return ui["text"].strip()
