"""ADA web sunucusu (FastAPI).

Görev çalıştırma `runtime` modülündedir; burası arayüz, giriş, SSE akışı ve yönetim uçlarıdır.
Sunucu açılınca zamanlayıcı, tetikleyici izleyici, hedef yürütücü ve rutin motoru arka planda başlar.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

from . import approval, config, goals, router, routines, runtime, safety, scheduler, settings, skills, store, terminal, tor, triggers, updater, watchdog
from .agent import dumps
from .runtime import RUNS
from .tools import REGISTRY, ToolError, safe_path

STATIC = Path(__file__).parent / "static"
SESSIONS: set[str] = set()
# Masaüstü uygulaması pencereyi öne getiren fonksiyonu buraya koyar (ikinci kopya açılınca çağrılır)
SHOW_WINDOW_HOOK = None
# Arayüzdeki Kontrol menüsünün "ADA'dan çık" seçeneği için (masaüstü uygulaması doldurur)
QUIT_HOOK = None
# Parola açıkken bile girişsiz erişilebilen yollar (tek kopya denetimi yalnızca 127.0.0.1'den)
PUBLIC_PATHS = ("/login", "/api/login", "/api/instance", "/api/instance/show")


async def _scheduled_runner(title: str, prompt: str) -> None:
    await runtime.start_background(title, prompt)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    tasks = [
        asyncio.create_task(scheduler.Scheduler(_scheduled_runner).loop()),
        asyncio.create_task(triggers.Watcher().loop()),
        asyncio.create_task(goals.Runner().loop()),
        asyncio.create_task(routines.Engine().loop()),
        asyncio.create_task(terminal.KEEPER.loop()),
        asyncio.create_task(watchdog.WATCHDOG.loop()),
        asyncio.create_task(router.discovery_loop()),  # yerel modelleri ve anahtarlı bulut modellerini tanır
        asyncio.create_task(updater.loop()),  # yeni sürüm var mı diye bakar
    ]
    tor.warm_up()  # ilk web isteği Tor'un açılmasını beklemesin
    yield
    for t in tasks:
        t.cancel()
    terminal.kill_all()
    tor.stop()


app = FastAPI(title="ADA-AGI", lifespan=lifespan)


# ---------------------------------------------------------------- kimlik doğrulama

LOGIN_HTML = """<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ADA · Giriş</title><style>body{font-family:-apple-system,Segoe UI,sans-serif;background:#f6f5f2;color:#1d1d1b;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
form{background:#fff;border:1px solid #e4e2dc;border-radius:14px;padding:28px;width:320px}h1{margin:0 0 4px;letter-spacing:.08em}p{color:#6b6a66;margin:0 0 18px;font-size:14px}
input{width:100%;box-sizing:border-box;padding:10px;border:1px solid #e4e2dc;border-radius:8px;font-size:15px;margin-bottom:10px}button{width:100%;padding:10px;border:0;border-radius:8px;background:#2f5597;color:#fff;font-weight:600;font-size:15px}
.err{color:#b3261e;font-size:13px;margin-top:8px}@media(prefers-color-scheme:dark){body{background:#161615;color:#ecebe7}form{background:#1f1f1d;border-color:#34332f}input{background:#2a2926;color:#ecebe7;border-color:#34332f}}</style></head>
<body><form id="f"><h1>ADA</h1><p>Devam etmek için parolanızı girin.</p><input type="password" id="p" placeholder="Parola" autofocus><button>Giriş</button><div class="err" id="e"></div></form>
<script>document.getElementById('f').onsubmit=async e=>{e.preventDefault();const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:document.getElementById('p').value})});
if(r.ok)location.href='/';else document.getElementById('e').textContent='Parola hatalı';};</script></body></html>"""


@app.middleware("http")
async def require_login(request: Request, call_next):
    if config.PASSWORD and request.url.path not in PUBLIC_PATHS:
        if request.cookies.get("ada_session") not in SESSIONS:
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "Giriş gerekli"}, status_code=401)
            return RedirectResponse("/login")
    return await call_next(request)


class LoginIn(BaseModel):
    password: str


@app.get("/login")
async def login_page():
    if not config.PASSWORD:
        return RedirectResponse("/")
    return HTMLResponse(LOGIN_HTML)


@app.post("/api/login")
async def login(body: LoginIn):
    if not config.PASSWORD or not hmac.compare_digest(body.password, config.PASSWORD):
        raise HTTPException(401, "Parola hatalı")
    token = secrets.token_urlsafe(32)
    SESSIONS.add(token)
    resp = JSONResponse({"ok": True})
    resp.set_cookie("ada_session", token, httponly=True, samesite="lax", max_age=30 * 24 * 3600)
    return resp


@app.post("/api/logout")
async def logout(request: Request):
    SESSIONS.discard(request.cookies.get("ada_session", ""))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("ada_session")
    return resp


# ---------------------------------------------------------------- sayfa ve durum

DIST = STATIC / "dist"  # React arayüzü (frontend/ → npm run build)


@app.get("/")
async def index():
    page = DIST / "index.html"
    if not page.exists():
        return HTMLResponse(
            "<h2>Arayüz derlenmemiş</h2><p>Terminalde: <code>cd frontend && npm install && npm run build</code></p>",
            status_code=503,
        )
    # index.html her zaman tazelensin: pencere önbelleği eski derlemenin dosyalarını göstermesin
    # (assets/ altındaki dosyaların adları içerik özetli olduğundan önbelleğe alınabilir).
    return FileResponse(page, headers={"Cache-Control": "no-cache"})


@app.get("/assets/{path:path}")
async def assets(path: str):
    # Paketli uygulamada static/ bir kısayol (symlink) olabilir: kökü de çözümleyerek karşılaştır
    root = DIST.resolve()
    p = (root / "assets" / path).resolve()
    if not p.is_relative_to(root) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


# ---------------------------------------------------------------- tek kopya (masaüstü)

def _local(request: Request) -> bool:
    return bool(request.client and request.client.host in ("127.0.0.1", "::1", "localhost"))


@app.get("/api/instance")
async def instance(request: Request):
    """İkinci ADA kopyası açılırken çalışan kopyayı tanımak için (sır içermez)."""
    if not _local(request):
        raise HTTPException(404)
    return {"app": "ADA", "pid": os.getpid()}


@app.post("/api/instance/show")
async def instance_show(request: Request):
    """Çalışan kopyanın penceresini öne getirir (ikinci kopya bunu çağırıp kapanır)."""
    if not _local(request):
        raise HTTPException(404)
    if SHOW_WINDOW_HOOK is None:
        return {"ok": False}
    try:
        SHOW_WINDOW_HOOK()
    except Exception:  # noqa: BLE001
        return {"ok": False}
    return {"ok": True}


@app.get("/api/status")
async def status():
    from .agent import active_model

    if config.PROVIDER == "adaagi":
        from . import engines

        has_key = any(e.enabled and e.available for e in await asyncio.to_thread(engines.all_engines))
    elif config.PROVIDER == "gemini":
        has_key = bool(os.getenv("GEMINI_API_KEY")) or config.QWEN_FALLBACK
    elif config.PROVIDER == "selfhost":
        has_key = bool(config.LLM_BASE_URL)
    else:
        has_key = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
    return {
        "provider": config.PROVIDER,
        "key_name": {"gemini": "GEMINI_API_KEY", "selfhost": "ADA_LLM_BASE_URL", "adaagi": "Yapay zekâ motoru"}.get(config.PROVIDER, "ANTHROPIC_API_KEY"),
        "model": await asyncio.to_thread(active_model),
        "fallback_model": config.QWEN_MODEL if config.PROVIDER == "gemini" and config.QWEN_FALLBACK else None,
        "effort": config.EFFORT if config.PROVIDER == "anthropic" else "-",
        "autonomy": config.AUTONOMY,
        "safety_mode": config.safety_mode(),
        "api_key": has_key,
        "computer": config.ENABLE_COMPUTER,
        "shell": config.ENABLE_SHELL,
        "mac_apps": config.ENABLE_MAC_APPS,
        "email_imap": bool(config.IMAP_USER and config.IMAP_PASSWORD),
        "python": config.ENABLE_PYTHON,
        "password": bool(config.PASSWORD),
        "desktop": QUIT_HOOK is not None,
        "lang": os.getenv("ADA_LANG", "auto"),
        "version": updater.status()["current"],
        "update": updater.status()["latest"] if updater.status()["available"] else None,
        "setup_needed": not config.SETUP_FLAG.exists(),
        "halted": safety.halt_info(),
        "usage": safety.usage(),
        "tools": sorted(REGISTRY) + ["web_search", "web_fetch"] + (["computer"] if config.ENABLE_COMPUTER else []),
    }


# ---------------------------------------------------------------- konuşmalar

class MessageIn(BaseModel):
    text: str
    attachments: list[str] = []


@app.get("/api/conversations")
async def list_conversations():
    items = store.list_all()
    for item in items:
        run = RUNS.get(item["id"])
        item["running"] = bool(run and not run.done)
    return items


@app.post("/api/conversations")
async def new_conversation():
    conv = store.create()
    return {"id": conv.id, "title": conv.title}


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    conv = store.load(conv_id)
    if conv is None:
        raise HTTPException(404, "Konuşma bulunamadı")
    run = RUNS.get(conv_id)
    return {"id": conv.id, "title": conv.title, "ui": conv.ui, "running": bool(run and not run.done)}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    run = RUNS.get(conv_id)
    if run and not run.done:
        raise HTTPException(409, "Görev çalışırken silinemez")
    store.delete(conv_id)
    RUNS.pop(conv_id, None)
    return {"ok": True}


@app.post("/api/conversations/{conv_id}/messages")
async def send_message(conv_id: str, body: MessageIn):
    conv = store.load(conv_id)
    if conv is None:
        raise HTTPException(404, "Konuşma bulunamadı")
    if runtime.is_running(conv_id):
        raise HTTPException(409, "Bu konuşmada zaten çalışan bir görev var")
    if not body.text.strip():
        raise HTTPException(400, "Boş mesaj")
    try:
        runtime.start(conv, body.text, body.attachments)
    except ToolError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(423 if safety.is_halted() else 409, str(e))
    return {"ok": True}


@app.post("/api/conversations/{conv_id}/stop")
async def stop(conv_id: str):
    run = RUNS.get(conv_id)
    if run is None or run.done or run.task is None:
        raise HTTPException(404, "Çalışan görev yok")
    run.task.cancel()
    return {"ok": True}


@app.get("/api/conversations/{conv_id}/stream")
async def stream(conv_id: str, offset: int = 0):
    run = RUNS.get(conv_id)
    if run is None:
        raise HTTPException(404, "Çalışan görev yok")

    async def gen():
        i = offset
        run.watchers += 1
        try:
            while True:
                async with run.changed:
                    while i >= len(run.events) and not run.done:
                        try:
                            await asyncio.wait_for(run.changed.wait(), timeout=15)
                        except asyncio.TimeoutError:
                            break
                    batch = run.events[i:]
                if not batch:
                    if run.done:
                        return
                    yield ": ping\n\n"  # bağlantıyı canlı tut
                    continue
                for event in batch:
                    i += 1
                    yield dumps(event)
                    if event["type"] == "done":
                        return
        finally:
            run.watchers -= 1

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- onaylar

class ApprovalIn(BaseModel):
    approved: bool
    always: bool = False


@app.post("/api/approvals/{approval_id}")
async def resolve_approval(approval_id: str, body: ApprovalIn):
    if not approval.resolve(approval_id, body.approved, body.always):
        raise HTTPException(404, "Onay isteği bulunamadı veya zaman aşımına uğradı")
    return {"ok": True}


@app.get("/api/always-allow")
async def list_always_allow():
    return [{"key": k, "tool": k.split("|")[0], "reason": k.split("|", 1)[1], "since": v} for k, v in approval.allowed().items()]


class AllowIn(BaseModel):
    key: str


@app.post("/api/always-allow/remove")
async def remove_always_allow(body: AllowIn):
    approval.set_always_allowed(body.key, False)
    safety.log("kalıcı_izin_kaldırıldı", {"key": body.key}, ok=True)
    return {"ok": True}


# ---------------------------------------------------------------- ayarlar

@app.get("/api/settings")
async def get_settings():
    return {"fields": settings.current(), "env_file": str(config.ENV_FILE), "workspace": str(config.WORKSPACE)}


class SettingsIn(BaseModel):
    changes: dict[str, str]


@app.post("/api/settings")
async def save_settings(body: SettingsIn):
    try:
        changed = settings.update(dict(body.changes))
    except ValueError as e:
        raise HTTPException(400, str(e))
    safety.log("ayarlar", {"değişen": changed}, ok=True)
    return {"ok": True, "changed": changed, "restart_required": True}


# ---------------------------------------------------------------- yapay zekâ motorları (ADA-AGI)

def _ai_payload() -> dict:
    from . import engines, router

    state = engines.load_state()
    data = router.stats()
    cloud = []
    for p in engines.CLOUD:
        key = engines.api_key(p.id)
        cloud.append({
            "id": p.id, "label": p.label, "key_env": p.key_env, "key_url": p.key_url, "note": p.note,
            "key_set": bool(key), "key_mask": settings.mask(key), "status": state["cloud_status"].get(p.id, {}),
            "passive": engines.is_passive(p.id),
        })
    local = [{"id": pid, "label": label, "url": root, **state["local_status"].get(pid, {"state": "unknown"})}
             for pid, label, root in engines.LOCAL]
    custom = [{**c, "key_set": bool(os.getenv(c.get("key_env") or "", "")), **state["local_status"].get(c["id"], {})}
              for c in state["custom"]]
    rows = []
    for e in sorted(engines.all_engines(), key=lambda e: (not e.local, e.provider, -e.tier, e.model)):
        d = e.public()
        d["stats"] = data["engines"].get(e.id, {})
        d["cooldown"] = data["cooldowns"].get(e.id)
        rows.append(d)
    return {
        "provider": config.PROVIDER,
        "active": config.PROVIDER == "adaagi",
        "engines": rows,
        "cloud": cloud,
        "claude_code": state["cloud_status"].get(engines.CLAUDE_CODE, {}),
        "opencode": state["cloud_status"].get(engines.OPENCODE, {}),
        "local": local,
        "custom": custom,
        "kinds": router.KINDS,
        "role_kinds": router.ROLE_KINDS,
        "routes": engines.routes(),
        "preview": router.preview(),
        "decisions": data.get("decisions", [])[:15],
        "scanned": state.get("scanned", ""),
    }


@app.get("/api/ai")
async def ai_status():
    return await asyncio.to_thread(_ai_payload)


class AIScanIn(BaseModel):
    target: str = "all"  # all | local | <sağlayıcı kimliği>


@app.post("/api/ai/scan")
async def ai_scan(request: Request):
    from . import engines

    _guard(request)
    body = await _json_body(request, AIScanIn)
    if body.target == "local":
        await asyncio.gather(engines.scan_local(), engines.scan_agents())  # Claude Code / OpenCode da bu bilgisayardadır
    elif body.target == "all":
        await engines.scan_all()
    elif body.target in engines.PROVIDERS and not engines.PROVIDERS[body.target].local:
        await engines.scan_cloud(body.target)
    else:
        raise HTTPException(400, "Bilinmeyen tarama hedefi")
    return await asyncio.to_thread(_ai_payload)


class AIKeyIn(BaseModel):
    provider: str
    key: str = ""


@app.post("/api/ai/key")
async def ai_key(request: Request):
    """Bulut sağlayıcı anahtarını kaydeder (boş: kaldırır), modellerini listeleyip anahtarı doğrular.
    Yeniden başlatma gerekmez."""
    from . import engines, router
    from .providers import gemini

    _guard(request)
    body = await _json_body(request, AIKeyIn)
    p = engines.PROVIDERS.get(body.provider)
    if p is None or p.local:
        raise HTTPException(400, "Bilinmeyen sağlayıcı")
    if engines.is_passive(p.id):
        raise HTTPException(400, f"{p.label} şimdilik pasif; daha sonra eklenecek.")
    key = body.key.strip()
    if any(c.isspace() for c in key):
        raise HTTPException(400, "Anahtar boşluk içeremez")
    changes = {p.key_env: key}
    for alias in engines.KEY_ALIASES.get(p.key_env, ()):
        if os.getenv(alias):
            changes[alias] = key  # eski adla kayıtlı anahtar da güncellensin
    settings.update(changes)
    if p.id == "gemini":
        gemini.reset_client()
    router.clear_cooldowns(p.id)
    status = await engines.scan_cloud(p.id)
    safety.log("yapay_zeka_anahtari", {"sağlayıcı": p.id, "durum": status.get(p.id, {}).get("state")}, ok=True)
    return await asyncio.to_thread(_ai_payload)


class AIEngineIn(BaseModel):
    id: str
    enabled: bool


@app.post("/api/ai/engine")
async def ai_engine(request: Request):
    from . import engines

    _guard(request)
    body = await _json_body(request, AIEngineIn)
    if body.enabled and engines.is_passive(*body.id.partition(":")[::2]):
        raise HTTPException(400, "Bu model şimdilik pasif; daha sonra eklenecek.")
    try:
        engines.set_enabled(body.id, body.enabled)
    except KeyError:
        raise HTTPException(404, "Motor bulunamadı")
    return await asyncio.to_thread(_ai_payload)


class AIRouteIn(BaseModel):
    kind: str
    engine: str = ""


@app.post("/api/ai/route")
async def ai_route(request: Request):
    from . import engines, router

    _guard(request)
    body = await _json_body(request, AIRouteIn)
    if body.kind not in router.KINDS:
        raise HTTPException(400, "Bilinmeyen görev türü")
    try:
        engines.set_route(body.kind, body.engine)
    except KeyError:
        raise HTTPException(404, "Motor bulunamadı")
    return await asyncio.to_thread(_ai_payload)


class AICustomIn(BaseModel):
    label: str = ""
    base_url: str
    key: str = ""


@app.post("/api/ai/custom")
async def ai_custom(request: Request):
    from . import engines

    _guard(request)
    body = await _json_body(request, AICustomIn)
    try:
        item = engines.add_custom(body.label, body.base_url, "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if body.key.strip():
        key_env = f"ADA_CUSTOM_{item['id'].upper()}_KEY"
        settings.update({key_env: body.key.strip()})
        engines.set_custom_key(item["id"], key_env)
    await engines.scan_local()
    return await asyncio.to_thread(_ai_payload)


class AIIdIn(BaseModel):
    id: str


@app.post("/api/ai/custom/remove")
async def ai_custom_remove(request: Request):
    from . import engines

    _guard(request)
    body = await _json_body(request, AIIdIn)
    engines.remove_custom(body.id)
    return await asyncio.to_thread(_ai_payload)


@app.post("/api/ai/ollama/start")
async def ai_ollama_start(request: Request):
    from . import engines

    _guard(request)
    if not await engines.ensure_ollama():
        raise HTTPException(400, "Ollama başlatılamadı (kurulu değil ya da açılamadı). https://ollama.com adresinden kurabilirsiniz.")
    await engines.scan_local()
    return await asyncio.to_thread(_ai_payload)


@app.post("/api/ai/test")
async def ai_test(request: Request):
    """Bir motora araçsız kısa bir soru sorar; yanıt ve süreyi döner (motor gerçekten çalışıyor mu?)."""
    import time as _time

    from . import engines, router

    _guard(request)
    body = await _json_body(request, AIIdIn)
    eng = engines.get(body.id)
    if eng is None:
        raise HTTPException(404, "Motor bulunamadı")
    if eng.passive:
        raise HTTPException(400, "Bu model şimdilik pasif; daha sonra eklenecek.")
    t0 = _time.monotonic()
    try:
        async with asyncio.timeout(240 if eng.local else 60):
            if eng.family == "gemini":
                from .providers.gemini import get_client

                r = await get_client().aio.models.generate_content(model=eng.model, contents="Yalnızca 'hazırım' yaz.")
                answer = (r.text or "").strip()
            elif eng.family == "claudecode":
                from .providers import claude_code

                answer = await claude_code.ask(eng.model, "Yalnızca 'hazırım' yaz.")
            elif eng.family == "opencode":
                from .providers import opencode

                answer = await opencode.ask(eng.model, "Yalnızca 'hazırım' yaz.")
            else:
                from .providers.selfhost import Endpoint, get_client as oa_client

                if eng.provider == "ollama":
                    await engines.ensure_ollama()
                ep = Endpoint(eng.base_url, engines.api_key(eng.provider), eng.model, eng.vision, 64,
                              config.LLM_TIMEOUT if eng.local else 60.0, eng.token_param)
                r = await oa_client(ep).chat.completions.create(
                    model=eng.model, messages=[{"role": "user", "content": "Yalnızca 'hazırım' yaz."}], **{eng.token_param: 400}
                )
                answer = (r.choices[0].message.content or "").strip() if r.choices else ""
    except Exception as e:  # noqa: BLE001
        failure = router._failure(e, eng) if not isinstance(e, TimeoutError) else ("yanıt vermedi (zaman aşımı)", 0)
        reason = failure[0] if failure else f"{type(e).__name__}: {str(e)[:200]}"
        if failure:
            router.cool(eng.id, failure[1], reason)  # yönlendirici de bu motoru bir süre atlasın
        return {"ok": False, "error": reason, "seconds": round(_time.monotonic() - t0, 1)}
    router.clear_cooldowns(eng.id)  # çalıştığı görüldü: dinlenmeden çıkar
    return {"ok": True, "answer": answer[:200] or "(boş yanıt)", "seconds": round(_time.monotonic() - t0, 1)}


class AIModeIn(BaseModel):
    provider: str


@app.post("/api/ai/mode")
async def ai_mode(request: Request):
    _guard(request)
    body = await _json_body(request, AIModeIn)
    try:
        settings.update({"ADA_PROVIDER": body.provider})
    except ValueError as e:
        raise HTTPException(400, str(e))
    safety.log("ayarlar", {"değişen": ["ADA_PROVIDER"], "değer": body.provider}, ok=True)
    return {"ok": True, "restart_required": body.provider != config.PROVIDER}


@app.post("/api/permissions/request")
async def request_permissions():
    from . import permissions

    results = await asyncio.to_thread(permissions.request_all)
    safety.log("izinler", {"sonuç": {r["name"]: r["ok"] for r in results}}, ok=all(r["ok"] for r in results))
    return results


class ClientErrorIn(BaseModel):
    kind: str
    message: str
    detail: str = ""


@app.post("/api/client-error")
async def client_error(body: ClientErrorIn):
    """Arayüzdeki (uygulama penceresi) JavaScript hatalarını işlem günlüğüne yazar."""
    safety.log("arayüz_hatası", {"tür": body.kind[:20]}, ok=False, result=f"{body.message[:200]} | {body.detail[:200]}")
    return {"ok": True}


class OpenUrlIn(BaseModel):
    url: str


@app.post("/api/open-url")
async def open_url(body: OpenUrlIn, request: Request):
    """Arayüzdeki bir dış linki ADA penceresinin dışında açar: Tor açıksa Tor üzerinden, değilse varsayılan
    tarayıcıda (pencere siteye gidip ADA'ya dönülemez hâle gelmesin)."""
    _guard(request)
    if not _local(request):
        raise HTTPException(404)
    url = body.url.strip()
    if not url.lower().startswith(("http://", "https://")) or len(url) > 4000:
        raise HTTPException(400, "Yalnızca http:// ve https:// bağlantıları açılabilir.")
    try:
        if tor.enabled():
            msg = await asyncio.to_thread(tor.open_url, url)
        else:
            import webbrowser

            webbrowser.open(url)
            msg = f"Açıldı: {url}"
    except tor.TorError as e:
        raise HTTPException(503, str(e)) from e
    return {"ok": True, "message": msg}


class PermOpenIn(BaseModel):
    name: str


@app.post("/api/permissions/open")
async def open_permission_settings(body: PermOpenIn):
    from . import permissions

    return {"ok": True, "pane": permissions.open_settings(body.name)}


@app.get("/api/login-item")
async def login_item_status():
    from . import login_item

    if not login_item.supported():
        return {"supported": False, "enabled": False}
    try:
        return {"supported": True, "enabled": await asyncio.to_thread(login_item.is_enabled)}
    except Exception as e:  # noqa: BLE001 - izin verilmemiş olabilir
        return {"supported": True, "enabled": False, "error": str(e)}


class LoginItemIn(BaseModel):
    enabled: bool


@app.post("/api/login-item")
async def login_item_set(body: LoginItemIn):
    from . import login_item

    try:
        enabled = await asyncio.to_thread(login_item.set_enabled, body.enabled)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    safety.log("açılışta_başlat", {"enabled": enabled}, ok=True)
    return {"supported": True, "enabled": enabled}


@app.post("/api/restart")
async def restart():
    if any(not r.done for r in RUNS.values()):
        raise HTTPException(409, "Çalışan görevler var; bitmelerini bekleyin veya durdurun.")

    async def later():
        await asyncio.sleep(0.5)
        settings.restart()

    asyncio.create_task(later())
    return {"ok": True}


# ---------------------------------------------------------------- ilk açılış soruları

class SetupIn(BaseModel):
    name: str = ""
    autonomy: str = "smart"
    autostart: bool = True
    watch: bool = True
    computer: bool = True


def _setup_name() -> str:
    from .tools import memory

    try:
        return str(memory._load().get("kullanici_adi", {}).get("name", ""))
    except (OSError, ValueError, AttributeError):
        return ""


@app.get("/api/setup")
async def setup_state():
    """Kurulumdan sonraki ilk açılışta sorulan soruların şu anki yanıtları (sihirbaz bunlarla açılır)."""
    from . import login_item

    autostart = {"supported": login_item.supported(), "enabled": False}
    if autostart["supported"]:
        try:
            autostart["enabled"] = await asyncio.to_thread(login_item.is_enabled)
        except Exception:  # noqa: BLE001 - izin verilmemiş olabilir
            pass
    return {
        "needed": not config.SETUP_FLAG.exists(),
        "name": _setup_name(),
        "autonomy": config.AUTONOMY if config.AUTONOMY in ("full", "smart", "ask") else "smart",
        "watch": config.WATCH,
        "computer": config.ENABLE_COMPUTER,
        "autostart": autostart,
    }


@app.post("/api/setup")
async def setup_save(request: Request):
    """Sihirbazın yanıtlarını kaydeder ve kurulumu tamamlandı olarak işaretler."""
    from . import login_item
    from .tools import memory

    _guard(request)
    body = await _json_body(request, SetupIn)
    if body.autonomy not in ("full", "smart", "ask"):
        raise HTTPException(400, "Geçersiz özerklik seviyesi")
    wanted = {
        "ADA_AUTONOMY": body.autonomy,
        "ADA_WATCH": "true" if body.watch else "false",
        "ADA_ENABLE_COMPUTER": "true" if body.computer else "false",
    }
    current = {"ADA_AUTONOMY": config.AUTONOMY, "ADA_WATCH": "true" if config.WATCH else "false",
               "ADA_ENABLE_COMPUTER": "true" if config.ENABLE_COMPUTER else "false"}
    settings.update(dict(wanted))
    name = " ".join(body.name.split())[:60]
    if name:
        data = memory._load()
        data["kullanici_adi"] = {"content": f"Kullanıcının adı {name}. Ona bu adla hitap et.", "name": name,
                                 "updated": datetime.now().isoformat(timespec="seconds")}
        memory._save(data)
    autostart_error = ""
    if login_item.supported():
        try:
            await asyncio.to_thread(login_item.set_enabled, body.autostart)
        except Exception as e:  # noqa: BLE001
            autostart_error = str(e)
    config.SETUP_FLAG.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    safety.log("ilk_kurulum", {"özerklik": body.autonomy, "açılışta_başlat": body.autostart, "gözcü": body.watch,
                               "ekran": body.computer}, ok=not autostart_error, result=autostart_error)
    return {"ok": True, "restart_required": wanted != current, "autostart_error": autostart_error}


# ---------------------------------------------------------------- kontrol menüsü (masaüstü)

class ControlFolderIn(BaseModel):
    which: str


@app.post("/api/control/open-folder")
async def control_open_folder(request: Request):
    """Çıktılar / rutinler / veri klasörünü bu bilgisayarın dosya yöneticisinde açar."""
    import subprocess
    import sys

    _guard(request)
    if not _local(request):
        raise HTTPException(403, "Klasörler yalnızca ADA'nın çalıştığı bilgisayarda açılabilir.")
    body = await _json_body(request, ControlFolderIn)
    path = {"outputs": config.OUTPUTS_DIR, "routines": config.ROUTINES_DIR, "home": config.HOME_DIR}.get(body.which)
    if path is None:
        raise HTTPException(400, "Bilinmeyen klasör")
    if config.IS_WIN:
        os.startfile(str(path))  # noqa: S606
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
    return {"ok": True, "path": str(path)}


@app.post("/api/control/quit")
async def control_quit(request: Request):
    """Masaüstü uygulamasını tamamen kapatır (arka plan işleri de durur)."""
    _guard(request)
    if not _local(request) or QUIT_HOOK is None:
        raise HTTPException(400, "Bu seçenek yalnızca masaüstü uygulamasında kullanılabilir.")

    async def later():
        await asyncio.sleep(0.3)  # yanıt arayüze ulaşsın
        QUIT_HOOK()

    asyncio.create_task(later())
    return {"ok": True}


# ---------------------------------------------------------------- arayüz dili

class LangIn(BaseModel):
    lang: str


@app.post("/api/lang")
async def set_lang(request: Request):
    """Arayüz dilini kaydeder ("auto": seçim yok, İngilizce). Hemen geçerlidir; arayüz sayfayı yeniler."""
    _guard(request)
    body = await _json_body(request, LangIn)
    try:
        settings.update({"ADA_LANG": body.lang})
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "lang": body.lang}


# ---------------------------------------------------------------- hakkında: geri bildirim ve bağış

@app.get("/api/about")
async def about():
    """Geri bildirim ve bağış bağlantıları ile geri bildirime eklenecek sürüm/sistem bilgisi."""
    import platform

    return {
        "version": updater.status()["current"],
        "system": f"{platform.system()} {platform.release()} ({platform.version()}), {platform.machine()}",
        "feedback_url": config.FEEDBACK_URL,
        "releases_url": config.RELEASES_URL,
        "donate_url": config.DONATE_URL,
        "donate_amounts": list(config.DONATE_AMOUNTS),
    }


# ---------------------------------------------------------------- uygulama içi güncelleme

@app.get("/api/update")
async def update_status():
    return updater.status()


@app.post("/api/update/check")
async def update_check(request: Request):
    _guard(request)
    return await updater.check()


@app.post("/api/update/install")
async def update_install(request: Request):
    """Yeni sürümü indirir, imzasını doğrular ve kurulumu başlatır; ADA kapanıp yeni sürümle yeniden açılır."""
    _guard(request)
    if any(not r.done for r in RUNS.values()):
        raise HTTPException(409, "Çalışan görevler var; bitmelerini bekleyin veya durdurun.")

    def close_app() -> None:
        # Kurulum dosyaları değiştirebilsin diye hemen çıkılır (kurulum ADA'yı yeniden açar)
        safety.log("güncelleme", {"sürüm": updater.status()["latest"]}, ok=True)
        terminal.kill_all()
        os._exit(0)

    try:
        updater.start_install(close_app)
    except updater.UpdateError as e:
        raise HTTPException(400, str(e))
    return updater.status()


# ---------------------------------------------------------------- güvenlik

@app.post("/api/emergency-stop")
async def emergency_stop():
    return await safety.emergency_stop("arayüz")


@app.post("/api/resume")
async def resume():
    safety.resume("arayüz")
    return {"ok": True}


@app.get("/api/audit")
async def audit(limit: int = 200):
    return safety.recent(min(max(limit, 1), 1000))


@app.get("/api/backups")
async def backups():
    return safety.list_backups(200)


@app.post("/api/backups/{backup_id}/restore")
async def restore_backup(backup_id: str):
    try:
        msg = safety.restore(backup_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    safety.log("restore_backup", {"backup_id": backup_id, "kaynak": "arayüz"}, ok=True, result=msg)
    return {"ok": True, "message": msg}


# ---------------------------------------------------------------- hedefler, tetikleyiciler, beceriler

@app.get("/api/goals")
async def list_goals():
    return [{**g, "running": runtime.is_running(g["conv_id"])} for g in goals.load_all()]


class GoalIn(BaseModel):
    title: str
    objective: str
    max_sessions: int = 30


@app.post("/api/goals")
async def create_goal(body: GoalIn):
    if not body.title.strip() or not body.objective.strip():
        raise HTTPException(400, "Başlık ve hedef gerekli")
    return goals.create(body.title.strip(), body.objective.strip(), max_sessions=body.max_sessions)


class GoalAction(BaseModel):
    action: str  # pause | resume


@app.post("/api/goals/{goal_id}")
async def goal_action(goal_id: str, body: GoalAction):
    if body.action == "resume" and safety.is_halted():
        raise HTTPException(423, "ADA acil durdurma modunda; önce 'Devam et' deyin.")
    g = goals.set_status(goal_id, {"pause": "paused", "resume": "active"}.get(body.action, ""))
    if g is None:
        raise HTTPException(404, "Hedef bulunamadı veya geçersiz işlem")
    return g


@app.delete("/api/goals/{goal_id}")
async def delete_goal(goal_id: str):
    g = goals.get(goal_id)
    if g is None:
        raise HTTPException(404, "Hedef bulunamadı")
    if runtime.is_running(g["conv_id"]):
        RUNS[g["conv_id"]].task.cancel()
    goals.delete(goal_id)
    return {"ok": True}


@app.get("/api/triggers")
async def list_triggers():
    return [{**t, "description": triggers.describe(t)} for t in triggers.load()]


@app.delete("/api/triggers/{trigger_id}")
async def delete_trigger(trigger_id: str):
    if not triggers.remove(trigger_id):
        raise HTTPException(404, "Bulunamadı")
    return {"ok": True}


@app.get("/api/skills")
async def list_skills():
    return skills.list_all()


@app.delete("/api/skills/{slug}")
async def delete_skill(slug: str):
    if not skills.delete(slug):
        raise HTTPException(404, "Bulunamadı")
    return {"ok": True}


# ---------------------------------------------------------------- zamanlanmış görevler

@app.get("/api/schedules")
async def list_schedules():
    return [{**s, "description": scheduler.describe(s["spec"])} for s in scheduler.load()]


@app.delete("/api/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    if not scheduler.remove(schedule_id):
        raise HTTPException(404, "Bulunamadı")
    return {"ok": True}


# ---------------------------------------------------------------- rutinler

ROUTINE_MAX_BYTES = 1 << 20  # yüklenen rutin dosyası en fazla 1 MB


class RoutineIn(BaseModel):
    name: str | None = None
    content: str | None = None


class RoutinePatch(BaseModel):
    enabled: bool | None = None
    time: str | None = None
    weekdays: list[int] | None = None
    interval_minutes: int | None = None
    name: str | None = None
    content: str | None = None
    notify: bool | None = None


async def _json_body(request: Request, model: type[BaseModel]):
    """Gövdeyi elle doğrular: FastAPI'nin 422 hata listesi yerine arayüzün gösterebildiği Türkçe 400 döner."""
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "Geçersiz istek: JSON gövde bekleniyor.")
    if not isinstance(data, dict):
        raise HTTPException(400, "Geçersiz istek: JSON nesnesi bekleniyor.")
    try:
        return model.model_validate(data)
    except ValidationError as e:
        fields = sorted({str(err["loc"][0]) for err in e.errors() if err.get("loc")})
        raise HTTPException(400, "Geçersiz alan: " + (", ".join(fields) or "?"))


def _routine_or_404(routine_id: str) -> dict:
    r = routines.get(routine_id)
    if r is None:
        raise HTTPException(404, "Rutin bulunamadı")
    return r


@app.get("/api/routines")
async def list_routines():
    items = await asyncio.to_thread(routines.sync)
    return [routines.summary(r) for r in sorted(items, key=lambda r: r["name"].casefold())]


@app.get("/api/routine-template")
async def routine_template():
    return {"content": routines.TEMPLATE}


@app.post("/api/routines/upload")
async def upload_routine(file: UploadFile | None = File(None)):
    if file is None or not file.filename:
        raise HTTPException(400, "Dosya seçilmedi.")
    name = re.sub(r"[^\w.\-() ]+", "_", Path(file.filename).name).strip() or "rutin.md"
    if Path(name).suffix.lower() not in (".md", ".markdown", ".txt"):
        raise HTTPException(400, f"{name}: yalnızca Markdown (.md) ya da .txt dosyası rutin olarak yüklenebilir.")
    data = await file.read(ROUTINE_MAX_BYTES + 1)
    if len(data) > ROUTINE_MAX_BYTES:
        raise HTTPException(400, f"{name}: dosya çok büyük (en fazla 1 MB).")
    try:
        r = await asyncio.to_thread(routines.create, routines.decode(data), name)
    except ValueError as e:
        raise HTTPException(400, f"{name}: {e}")
    safety.log("routine_upload", {"file": r["file"], "kaynak": "arayüz"}, ok=True, result=f"{r['name']} — {routines.describe(r['spec'])}")
    return routines.summary(r)


@app.post("/api/routines")
async def create_routine(request: Request):
    body = await _json_body(request, RoutineIn)
    content, name = body.content or "", (body.name or "").strip()
    try:
        # Ad verildiyse ve dosyada ad yazmıyorsa ön bilgiye eklenir
        overrides = {"name": name} if name and "name" not in routines.parse(content)["meta"] else None
        r = await asyncio.to_thread(routines.create, content, None, overrides)
    except ValueError as e:
        raise HTTPException(400, str(e))
    safety.log("routine_create", {"file": r["file"], "kaynak": "arayüz"}, ok=True, result=f"{r['name']} — {routines.describe(r['spec'])}")
    return routines.summary(r)


@app.get("/api/routines/{routine_id}")
async def get_routine(routine_id: str):
    return routines.detail(_routine_or_404(routine_id))


@app.post("/api/routines/{routine_id}/run")
async def run_routine(routine_id: str):
    r = _routine_or_404(routine_id)
    try:
        entry = await routines.run_now(routine_id)
    except LookupError:
        raise HTTPException(404, "Rutin bulunamadı")
    except RuntimeError as e:
        raise HTTPException(423 if safety.is_halted() else 409, str(e))
    safety.log("routine_run_now", {"routine_id": routine_id, "kaynak": "arayüz"}, ok=True, result=r["name"])
    return {"ok": True, "conv_id": entry["conv_id"]}


@app.post("/api/routines/{routine_id}")
async def update_routine(routine_id: str, request: Request):
    _routine_or_404(routine_id)
    changes = (await _json_body(request, RoutinePatch)).model_dump(exclude_none=True)
    try:
        r = await asyncio.to_thread(routines.update, routine_id, **changes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if r is None:
        raise HTTPException(404, "Rutin bulunamadı")
    logged = {k: (f"{len(v)} karakter" if k == "content" else v) for k, v in changes.items()}
    safety.log("routine_update", {"routine_id": routine_id, **logged, "kaynak": "arayüz"}, ok=True, result=f"{r['name']} — {routines.describe(r['spec'])}")
    return routines.summary(r)


@app.delete("/api/routines/{routine_id}")
async def delete_routine(routine_id: str):
    r = _routine_or_404(routine_id)
    try:
        deleted = await asyncio.to_thread(routines.delete, routine_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    if not deleted:
        raise HTTPException(404, "Rutin bulunamadı")
    safety.log("routine_delete", {"routine_id": routine_id, "file": r["file"], "kaynak": "arayüz"}, ok=True, result=r["name"])
    return {"ok": True}


# ---------------------------------------------------------------- terminal
#
# Terminal bilgisayarda komut çalıştırır: başka bir web sitesinin (tarayıcıda açık bir sekmenin)
# 127.0.0.1'deki ADA'ya istek atıp komut çalıştıramaması için tüm terminal uçları Origin denetler;
# WebSocket'te HTTP ara katmanı çalışmadığından parola çerezi de burada ayrıca denetlenir.

def _same_origin(headers, host: str | None) -> bool:
    origin = headers.get("origin")
    if not origin:  # tarayıcı dışı istemci (tarayıcılar çapraz isteklerde Origin'i her zaman gönderir)
        return True
    return bool(host) and urlsplit(origin).netloc.lower() == host.lower()


def _guard(request: Request) -> None:
    if not _same_origin(request.headers, request.headers.get("host")):
        raise HTTPException(403, "Bu istek ADA arayüzünden gelmiyor.")


class TerminalIn(BaseModel):
    command: str = ""
    title: str = ""
    cwd: str = ""
    cols: int = 120
    rows: int = 32


class SavedCommandIn(BaseModel):
    command: str
    title: str = ""
    cwd: str = ""
    autostart: bool = True


class SavedCommandPatch(BaseModel):
    command: str | None = None
    title: str | None = None
    cwd: str | None = None
    autostart: bool | None = None


@app.get("/api/terminal")
async def terminal_state():
    return {
        "sessions": terminal.list_sessions(),
        "saved": terminal.KEEPER.status(),
        "shell": " ".join(terminal.default_shell()),
    }


@app.post("/api/terminal/sessions")
async def terminal_open(request: Request):
    _guard(request)
    body = await _json_body(request, TerminalIn)
    try:
        s = terminal.open_session(body.command, body.title, body.cwd, body.cols, body.rows)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    safety.log("terminal_aç", {"command": body.command or "(kabuk)", "kaynak": "arayüz"}, ok=True, result=s.id)
    return s.info()


@app.delete("/api/terminal/sessions/{session_id}")
async def terminal_close(session_id: str, request: Request):
    _guard(request)
    if not terminal.close_session(session_id):
        raise HTTPException(404, "Oturum bulunamadı")
    return {"ok": True}


@app.post("/api/terminal/saved")
async def terminal_add_saved(request: Request):
    _guard(request)
    body = await _json_body(request, SavedCommandIn)
    try:
        item = terminal.add_saved(body.command, body.title, body.cwd, body.autostart)
    except ValueError as e:
        raise HTTPException(400, str(e))
    safety.log("terminal_kalıcı_komut", {"command": body.command, "kaynak": "arayüz"}, ok=True, result=item["id"])
    return item


@app.post("/api/terminal/saved/{saved_id}")
async def terminal_update_saved(saved_id: str, request: Request):
    _guard(request)
    body = await _json_body(request, SavedCommandPatch)
    try:
        item = terminal.update_saved(saved_id, **body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if item is None:
        raise HTTPException(404, "Kalıcı komut bulunamadı")
    return item


@app.post("/api/terminal/saved/{saved_id}/start")
async def terminal_start_saved(saved_id: str, request: Request):
    _guard(request)
    terminal.KEEPER.reset(saved_id)
    try:
        return terminal.start_saved(saved_id).info()
    except LookupError as e:
        raise HTTPException(404, str(e))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(409, str(e))


@app.delete("/api/terminal/saved/{saved_id}")
async def terminal_remove_saved(saved_id: str, request: Request):
    _guard(request)
    if not terminal.remove_saved(saved_id):
        raise HTTPException(404, "Kalıcı komut bulunamadı")
    safety.log("terminal_kalıcı_komut_sil", {"id": saved_id, "kaynak": "arayüz"}, ok=True)
    return {"ok": True}


@app.websocket("/api/terminal/sessions/{session_id}/ws")
async def terminal_ws(ws: WebSocket, session_id: str):
    if not _same_origin(ws.headers, ws.headers.get("host")) or (
        config.PASSWORD and ws.cookies.get("ada_session") not in SESSIONS
    ):
        await ws.close(code=4403)
        return
    s = terminal.get(session_id)
    if s is None:
        await ws.close(code=4404)
        return
    await ws.accept()
    q = s.subscribe()
    try:
        await ws.send_json({"t": "out", "d": s.backlog()})
        if not s.alive:
            await ws.send_json({"t": "exit", "code": s.exit_code})

        async def pump_out() -> None:
            while True:
                msg = await q.get()
                # Birikmiş çıktıyı tek mesajda gönder (hızlı akan çıktıda tarayıcı yorulmasın)
                while msg["t"] == "out" and not q.empty():
                    nxt = q.get_nowait()
                    if nxt["t"] != "out":
                        await ws.send_json(msg)
                        msg = nxt
                        break
                    msg = {"t": "out", "d": msg["d"] + nxt["d"]}
                await ws.send_json(msg)

        sender = asyncio.create_task(pump_out())
        try:
            while True:
                data = await ws.receive_json()
                kind = data.get("t") if isinstance(data, dict) else None
                if kind == "in" and isinstance(data.get("d"), str):
                    s.write(data["d"])
                elif kind == "resize":
                    s.resize(data.get("cols", 120), data.get("rows", 32))
        finally:
            sender.cancel()
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        s.unsubscribe(q)


# ---------------------------------------------------------------- gözcü

@app.get("/api/watch")
async def watch_status():
    return watchdog.WATCHDOG.status()


@app.get("/api/watch/checklist")
async def watch_checklist():
    await asyncio.to_thread(watchdog.ensure_checklist)
    return {"content": watchdog.checklist_text(), "file": str(watchdog.CHECKLIST_FILE)}


class ChecklistIn(BaseModel):
    content: str


@app.post("/api/watch/checklist")
async def watch_save_checklist(request: Request):
    _guard(request)
    body = await _json_body(request, ChecklistIn)
    try:
        await asyncio.to_thread(watchdog.save_checklist, body.content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    safety.log("gözcü_listesi", {"kaynak": "arayüz", "madde": watchdog.checklist_items().count("\n") + 1 if watchdog.checklist_items() else 0}, ok=True)
    return {"ok": True, "items": watchdog.checklist_items()}


@app.post("/api/watch/check")
async def watch_check_now(request: Request):
    _guard(request)
    if safety.is_halted():
        raise HTTPException(423, "ADA acil durdurma modunda; önce 'Devam et' deyin.")
    if not watchdog.checklist_items():
        raise HTTPException(400, "Kontrol listesinde etkin madde yok.")
    conv_id = await watchdog.WATCHDOG.maybe_checklist(datetime.now(), force=True)
    if conv_id is None:
        raise HTTPException(409, "Bir kontrol zaten çalışıyor.")
    return {"ok": True, "conv_id": conv_id}


# ---------------------------------------------------------------- dosyalar

@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    name = re.sub(r"[^\w.\-() ]+", "_", Path(file.filename or "dosya").name).strip() or "dosya"
    dest = config.UPLOADS_DIR / name
    if dest.exists():
        dest = config.UPLOADS_DIR / f"{dest.stem}_{uuid.uuid4().hex[:6]}{dest.suffix}"
    with dest.open("wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    return {"path": dest.relative_to(config.WORKSPACE).as_posix(), "name": dest.name}


@app.get("/api/voice")
async def voice_status():
    from . import transcribe

    return transcribe.status()


@app.post("/api/voice/transcribe")
async def voice_transcribe(request: Request, file: UploadFile = File(...)):
    from . import transcribe

    _guard(request)
    audio = await file.read(transcribe.MAX_BYTES + 1)
    try:
        return {"text": await transcribe.transcribe(audio, file.content_type or "audio/wav")}
    except transcribe.TranscribeError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/voice/dictation")
async def voice_dictation(request: Request):
    from . import transcribe

    _guard(request)
    if not _local(request):  # tuş vuruşu bu bilgisayarın ekranına gider: ağdan açılamaz
        raise HTTPException(403, "Sesle yazma yalnızca bu bilgisayardaki ADA penceresinden açılabilir.")
    try:
        transcribe.windows_dictation()
    except transcribe.TranscribeError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@app.get("/api/files/{path:path}")
async def download(path: str):
    try:
        p = safe_path(path, must_exist=True)
    except ToolError:
        raise HTTPException(404, "Dosya bulunamadı")
    if not p.is_file():
        raise HTTPException(404, "Dosya bulunamadı")
    return FileResponse(p, filename=p.name)


@app.get("/api/files")
async def list_outputs():
    files = sorted(
        (p for p in config.OUTPUTS_DIR.rglob("*") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [{"path": p.relative_to(config.WORKSPACE).as_posix(), "size": p.stat().st_size} for p in files[:200]]


def main() -> None:
    import uvicorn

    from .agent import active_model

    print(f"\n  ADA çalışıyor → http://{config.HOST}:{config.PORT}")
    print(f"  Yapay zekâ: {config.PROVIDER} ({active_model()}) · özerklik: {config.AUTONOMY}\n")
    if config.HOST not in ("127.0.0.1", "localhost") and not config.PASSWORD:
        print("  UYARI: Sunucu ağa açık ama ADA_PASSWORD ayarlı değil!\n")
    uvicorn.run(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
