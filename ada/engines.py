"""Yapay zekâ motorları: bu bilgisayardaki yerel modeller + isteğe bağlı bulut API anahtarları.

ADA-AGI üst katmanı (bkz. router.py) işleri bu motorlar arasında paylaştırır. Burada:
  - Bilinen bulut sağlayıcıların kataloğu (anahtar girilince modelleri listelenir),
  - Yerel model sunucularının taranması (Ollama, LM Studio, llama.cpp, Jan, vLLM...),
  - Her motor için yetenek profili (araç çağırma, görsel okuma, kalite/hız/maliyet tahmini),
  - Kullanıcının seçimleri (motor açık/kapalı, görev türüne sabitlenen motor) ai.json'da.

API anahtarları ai.json'a değil .env dosyasına yazılır (çalışma alanının dışında).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from . import config

AI_FILE = config.DATA_DIR / "ai.json"
_lock = threading.RLock()


# ---------------------------------------------------------------- katalog

@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    family: str  # "gemini" (yerel SDK) | "openai" (OpenAI uyumlu Chat Completions)
    base_url: str
    key_env: str = ""
    key_url: str = ""
    local: bool = False
    max_tokens: int = 16000
    token_param: str = "max_tokens"
    fallback_models: tuple[str, ...] = ()
    note: str = ""
    models_url: str = ""  # model listesi base_url + "/models" dışında bir yerdeyse


CLOUD: list[Provider] = [
    Provider("gemini", "Google Gemini", "gemini", "https://generativelanguage.googleapis.com/v1beta", "GEMINI_API_KEY",
             "https://aistudio.google.com/apikey", max_tokens=65536, fallback_models=tuple(config.GEMINI_MODELS),
             note="Ücretsiz katmanı var"),
    Provider("anthropic", "Anthropic Claude", "openai", "https://api.anthropic.com/v1/", "ANTHROPIC_API_KEY",
             "https://console.anthropic.com/settings/keys", max_tokens=32000,
             fallback_models=("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"), note="Ücretli; en güçlü modeller"),
    Provider("openai", "OpenAI", "openai", "https://api.openai.com/v1", "OPENAI_API_KEY", "https://platform.openai.com/api-keys",
             max_tokens=32000, token_param="max_completion_tokens", note="Ücretli"),
    Provider("deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY",
             "https://platform.deepseek.com/api_keys", max_tokens=8192, fallback_models=("deepseek-chat",), note="Ucuz"),
    Provider("qwen", "Alibaba Qwen (DashScope)", "openai", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
             "DASHSCOPE_API_KEY", "https://modelstudio.console.alibabacloud.com/", max_tokens=8192,
             fallback_models=("qwen-plus", "qwen-max")),
    Provider("mistral", "Mistral", "openai", "https://api.mistral.ai/v1", "MISTRAL_API_KEY", "https://console.mistral.ai/api-keys"),
    Provider("groq", "Groq (çok hızlı)", "openai", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "https://console.groq.com/keys",
             max_tokens=8192, note="Ücretsiz katmanı var"),
    Provider("xai", "xAI Grok", "openai", "https://api.x.ai/v1", "XAI_API_KEY", "https://console.x.ai"),
    Provider("openrouter", "OpenRouter (yüzlerce model)", "openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
             "https://openrouter.ai/keys", note="Tek anahtarla birçok aile"),
    Provider("together", "Together AI", "openai", "https://api.together.xyz/v1", "TOGETHER_API_KEY",
             "https://api.together.ai/settings/api-keys", max_tokens=8192),
    # Yalnızca Chat Completions ucundaki modeller alınır (bkz. _ZEN_OTHER); ücretsizler OpenCode dışında çalışmaz
    Provider("zen", "OpenCode Zen", "openai", "https://opencode.ai/inference/openai/v1", "OPENCODE_API_KEY",
             "https://opencode.ai/console", max_tokens=8192, models_url="https://opencode.ai/inference/v1/models",
             fallback_models=("kimi-k3", "glm-5.3", "deepseek-v4-pro"), note="Kodlama için seçilmiş modeller"),
]

# Yerel sunucular: (kimlik, ad, kök adres, OpenAI uyumlu yol)
LOCAL: list[tuple[str, str, str]] = [
    ("ollama", "Ollama", "http://127.0.0.1:11434"),
    ("lmstudio", "LM Studio", "http://127.0.0.1:1234"),
    ("llamacpp", "llama.cpp / LocalAI", "http://127.0.0.1:8080"),
    ("jan", "Jan", "http://127.0.0.1:1337"),
    ("vllm", "vLLM", "http://127.0.0.1:8000"),
    ("textgen", "text-generation-webui", "http://127.0.0.1:5000"),
    ("kobold", "KoboldCpp", "http://127.0.0.1:5001"),
]

PROVIDERS: dict[str, Provider] = {p.id: p for p in CLOUD}
for _id, _label, _root in LOCAL:
    PROVIDERS[_id] = Provider(_id, _label, "openai", _root + "/v1", local=True, max_tokens=config.LLM_MAX_TOKENS)

# Eski ayar adları: DashScope anahtarı ADA_QWEN_API_KEY olarak da girilmiş olabilir
KEY_ALIASES = {"DASHSCOPE_API_KEY": ("ADA_QWEN_API_KEY",)}
KEY_ENVS = [p.key_env for p in CLOUD]

# Sohbet modeli olmayanlar (gömme, ses, görsel üretimi...) motor olarak alınmaz
_NOT_CHAT = re.compile(
    r"embed|tts|whisper|transcri|audio|speech|dall-?e|imagen|image-gen|-image|moderation|realtime|rerank|veo|lyria|"
    r"aqa|babbage|davinci|guard|ocr|live|native-audio|computer-use|robotics|learnlm|search-preview|sora|codex-mini",
    re.I,
)
# Zen'de Chat Completions dışında bir uçtan (Responses, Messages, Gemini, SystemOne) sunulanlar ve
# yalnızca OpenCode'un kendi arayüzünde çalışan ücretsiz modeller
_ZEN_OTHER = re.compile(r"^(claude|gpt|gemini|grok|muse|jev|test$)|-backup$|-free$|^big-pickle$", re.I)


def api_key(provider_id: str) -> str:
    p = PROVIDERS.get(provider_id)
    if p is None:
        custom = _custom(provider_id)
        return os.getenv(custom["key_env"], "") if custom and custom.get("key_env") else ""
    if not p.key_env:
        return ""
    value = os.getenv(p.key_env, "")
    for alias in KEY_ALIASES.get(p.key_env, ()):
        value = value or os.getenv(alias, "")
    return value


# ---------------------------------------------------------------- motor

@dataclass
class Engine:
    id: str
    provider: str
    model: str
    label: str
    family: str
    base_url: str
    local: bool = False
    vision: bool = False
    tools: bool = True
    tier: int = 3       # 1-5 kalite tahmini
    speed: int = 3      # 1-5
    cost: int = 1       # 0 ücretsiz/yerel ... 3 pahalı
    context: int = 32000
    params_b: float = 0.0
    loaded: bool = False  # yerel: şu an bellekte (soğuk yükleme beklemesi yok)
    specialty: list[str] = field(default_factory=list)  # "kod", "derin", "ekran"
    coords: str = "pixel"  # ekran koordinatları: "pixel" | "norm1000"
    enabled: bool = True
    user_set: bool = False  # kullanıcı açtı/kapattı: tarama bunu değiştirmez
    available: bool = True  # sunucu çalışıyor / anahtar var
    max_tokens: int = 16000
    token_param: str = "max_tokens"
    version: float = 0.0
    passive: bool = False  # şimdilik devre dışı bırakılan aile (Qwen): listede görünür ama kullanılmaz

    @property
    def toolset(self) -> str:
        # Küçük bağlamlı yerel modellerde istem uzunluğu hızı belirler: temel araçlar gönderilir
        return "core" if self.local else "full"

    def public(self) -> dict:
        d = asdict(self)
        d["toolset"] = self.toolset
        d["provider_label"] = provider_label(self.provider)
        return d


# Claude Code: bu bilgisayarda kurulu `claude` komutu; anahtar yerine açık oturumu (aboneliği) kullanır.
# Katalogda (PROVIDERS) yer almaz çünkü ne anahtarı ne adresi vardır (bkz. providers/claude_code.py).
CLAUDE_CODE = "claudecode"
# OpenCode: bu bilgisayarda kurulu `opencode` komutu; OpenCode'da bağlı sağlayıcıları kullanır (bkz. providers/opencode.py)
OPENCODE = "opencode"


def is_passive(provider: str, model: str = "") -> bool:
    """Qwen pasifken (config.QWEN_ENABLED kapalı) Qwen modelleri ve DashScope sağlayıcısı kullanılmaz."""
    return not config.QWEN_ENABLED and (provider == "qwen" or "qwen" in model.lower())


def provider_label(pid: str) -> str:
    if pid in PROVIDERS:
        return PROVIDERS[pid].label
    if pid == CLAUDE_CODE:
        return "Claude Code (abonelik)"
    if pid == OPENCODE:
        return "OpenCode (CLI)"
    c = _custom(pid)
    return c["label"] if c else pid


# ---------------------------------------------------------------- profil tahmini

# (desen, kalite, hız, maliyet) — ilk eşleşen kullanılır; sıra önemlidir (lite/mini önce)
_CLOUD_RULES: list[tuple[str, int, int, int]] = [
    (r"opus", 5, 2, 3),
    (r"sonnet", 4, 3, 2),
    (r"haiku", 3, 5, 1),
    (r"gemini.*(flash-lite|lite)", 3, 5, 0),
    (r"gemini.*flash", 4, 5, 0),
    (r"gemini.*pro", 5, 3, 1),
    (r"gemma", 2, 4, 0),
    (r"nano", 2, 5, 1),
    (r"(gpt-5|o\d).*mini|gpt-4o-mini|gpt-4\.1-mini", 3, 4, 1),
    (r"gpt-5|^o[34]\b|^o[34]-|gpt-4\.1", 5, 3, 3),
    (r"gpt-4o", 4, 4, 2),
    (r"deepseek-reasoner|deepseek-r1|\br1\b", 4, 2, 1),
    (r"deepseek", 4, 3, 1),
    (r"qwen.*(max)", 5, 3, 2),
    (r"qwen.*(turbo|flash)", 3, 5, 0),
    (r"qwen.*coder", 4, 4, 1),
    (r"qwen.*plus|qwen3", 4, 4, 1),
    (r"magistral|mistral-large", 4, 3, 2),
    (r"codestral|devstral", 4, 4, 1),
    (r"mistral-medium|pixtral-large", 4, 4, 1),
    (r"mistral-small|ministral|open-mistral", 2, 5, 0),
    (r"grok.*(mini|fast)", 4, 5, 1),
    (r"grok", 5, 3, 3),
    (r"llama.*(405b|maverick|70b)", 4, 4, 1),
    (r"kimi|glm|minimax", 4, 3, 1),
    (r"llama|mixtral|scout|8b", 3, 5, 0),
]

_VISION = re.compile(r"vision|-vl|vl-|\bvl\b|pixtral|llava|gpt-4o|gpt-4\.1|gpt-5|gemini|claude|grok-4|llama-4|gemma-3|"
                     r"maverick|scout|mistral-medium|mistral-small-3|qwen.*omni|minicpm-v|moondream", re.I)
_CODE = re.compile(r"coder|codestral|devstral|code|sonnet|opus|gpt-5|deepseek|kimi|glm", re.I)
_REASON = re.compile(r"opus|reason|r1\b|thinking|pro|o3|o4|gpt-5(?!.*(mini|nano))|magistral|grok-4|max", re.I)


def _version(model: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)", model)
    try:
        return float(m.group(1)) if m else 0.0
    except ValueError:
        return 0.0


def _specialty(model: str, vision: bool) -> list[str]:
    out = []
    if _CODE.search(model):
        out.append("kod")
    if _REASON.search(model):
        out.append("derin")
    if vision:
        out.append("ekran")
    return out


def _coords(provider: str, model: str) -> str:
    m = model.lower()
    if provider == "gemini" or "gemini" in m:
        return "norm1000"
    if re.search(r"qwen3[-_.]?vl|qwen3-omni", m):
        return "norm1000"  # Qwen3-VL konumları 0-1000 arası verir (Qwen2.5-VL piksel)
    return "pixel"


def cloud_engine(p: Provider, model: str, *, vision: bool | None = None, tools: bool | None = None, context: int = 0) -> Engine:
    m = model.lower()
    tier, speed, cost = 3, 3, 1
    for pattern, t, s, c in _CLOUD_RULES:
        if re.search(pattern, m):
            tier, speed, cost = t, s, c
            break
    if p.id == "groq":
        speed = 5
    if p.id == "openrouter" and m.endswith(":free"):
        cost = 0
    vis = bool(_VISION.search(m)) if vision is None else vision
    if p.id in ("anthropic", "gemini"):
        vis = True
    return Engine(
        id=f"{p.id}:{model}", provider=p.id, model=model, label=_pretty(model), family=p.family, base_url=p.base_url,
        local=False, vision=vis, tools=True if tools is None else tools, tier=tier, speed=speed, cost=cost,
        context=context or (1_000_000 if p.id == "gemini" else 128_000), specialty=_specialty(m, vis),
        coords=_coords(p.id, model), max_tokens=p.max_tokens, token_param=p.token_param, version=_version(m),
    )


def local_engine(pid: str, base_url: str, model: str, *, caps: list[str] | None, params_b: float, family: str = "",
                 context: int = 0, loaded: bool = False) -> Engine:
    m = model.lower()
    vision = ("vision" in caps) if caps is not None else bool(_VISION.search(m))
    tools = ("tools" in caps) if caps is not None else True
    if params_b <= 0:
        mm = re.search(r"(\d+(?:\.\d+)?)b\b", m)
        params_b = float(mm.group(1)) if mm else 7.0
    tier = 1 if params_b < 4 else 2 if params_b < 10 else 3 if params_b < 25 else 4 if params_b < 80 else 5
    if "moe" in family.lower() or re.search(r"a\d+b", m):
        tier = max(1, tier - 1)  # uzman karışımı: etkin parametre küçük
    # Bu bilgisayarda modeller yavaş diskten yüklenir: bellekte olmayan model ilk istekte bekletir
    speed = 3 if loaded else 2
    if params_b < 10:
        speed += 1
    return Engine(
        id=f"{pid}:{model}", provider=pid, model=model, label=_pretty(model) + " (yerel)", family="openai", base_url=base_url,
        local=True, vision=vision, tools=tools, tier=tier, speed=min(speed, 5), cost=0, context=context or 16384,
        params_b=round(params_b, 1), loaded=loaded, specialty=_specialty(m, vision), coords=_coords(pid, model),
        max_tokens=config.LLM_MAX_TOKENS, version=_version(m),
    )


def _pretty(model: str) -> str:
    name = model.split("/")[-1]
    name = name.removesuffix(":latest")
    return name


# ---------------------------------------------------------------- kalıcı durum

def _default_state() -> dict:
    return {"engines": {}, "routes": {}, "custom": [], "local_status": {}, "cloud_status": {}, "scanned": ""}


def load_state() -> dict:
    with _lock:
        try:
            data = json.loads(AI_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        state = _default_state()
        state.update({k: v for k, v in data.items() if k in state})
        return state


def save_state(state: dict) -> None:
    with _lock:
        AI_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = AI_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(AI_FILE)


def _custom(pid: str) -> dict | None:
    return next((c for c in load_state().get("custom", []) if c.get("id") == pid), None)


def all_engines() -> list[Engine]:
    """Kayıtlı tüm motorlar (anahtar/sunucu durumu güncel)."""
    state = load_state()
    out = []
    names = set(Engine.__dataclass_fields__)
    for data in state["engines"].values():
        try:
            e = Engine(**{k: v for k, v in data.items() if k in names})
        except TypeError:
            continue
        e.passive = is_passive(e.provider, e.model)
        e.available = not e.passive and _is_available(e, state)
        out.append(e)
    return out


def _is_available(e: Engine, state: dict) -> bool:
    if e.provider in (CLAUDE_CODE, OPENCODE):
        return state["cloud_status"].get(e.provider, {}).get("state") == "ok"
    if e.local:
        st = state["local_status"].get(e.provider, {}).get("state")
        if e.provider in PROVIDERS:
            # Ollama kurulu ama kapalıysa ADA onu kendisi başlatabilir
            return st == "running" or (e.provider == "ollama" and st == "installed")
        return st != "down"
    if e.provider in PROVIDERS:
        return bool(api_key(e.provider))
    return _custom(e.provider) is not None


def get(engine_id: str) -> Engine | None:
    return next((e for e in all_engines() if e.id == engine_id), None)


def set_enabled(engine_id: str, enabled: bool) -> None:
    with _lock:
        state = load_state()
        if engine_id not in state["engines"]:
            raise KeyError(engine_id)
        state["engines"][engine_id].update(enabled=bool(enabled), user_set=True)
        save_state(state)


def set_route(kind: str, engine_id: str) -> None:
    with _lock:
        state = load_state()
        if engine_id:
            if engine_id not in state["engines"]:
                raise KeyError(engine_id)
            state["routes"][kind] = engine_id
        else:
            state["routes"].pop(kind, None)
        save_state(state)


def routes() -> dict[str, str]:
    return dict(load_state().get("routes", {}))


def add_custom(label: str, base_url: str, key_env: str) -> dict:
    base_url = base_url.strip().rstrip("/")
    if not re.match(r"^https?://", base_url):
        raise ValueError("Adres http:// veya https:// ile başlamalı")
    with _lock:
        state = load_state()
        n = 1
        ids = {c["id"] for c in state["custom"]}
        while f"ozel{n}" in ids:
            n += 1
        item = {"id": f"ozel{n}", "label": label.strip() or f"Özel sunucu {n}", "base_url": base_url, "key_env": key_env}
        state["custom"].append(item)
        save_state(state)
        return item


def set_custom_key(pid: str, key_env: str) -> None:
    with _lock:
        state = load_state()
        for c in state["custom"]:
            if c["id"] == pid:
                c["key_env"] = key_env
        save_state(state)


def remove_custom(pid: str) -> None:
    with _lock:
        state = load_state()
        state["custom"] = [c for c in state["custom"] if c["id"] != pid]
        state["engines"] = {k: v for k, v in state["engines"].items() if v.get("provider") != pid}
        state["routes"] = {k: v for k, v in state["routes"].items() if not v.startswith(pid + ":")}
        state["local_status"].pop(pid, None)
        save_state(state)


# ---------------------------------------------------------------- tarama

async def _get_json(client: httpx.AsyncClient, url: str, **kw):
    r = await client.get(url, **kw)
    r.raise_for_status()
    return r.json()


def _ollama_exe() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    cands = [Path("/usr/local/bin/ollama"), Path("/Applications/Ollama.app/Contents/Resources/ollama")]
    if os.getenv("LOCALAPPDATA"):
        cands.insert(0, Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Ollama" / "ollama.exe")
    return next((str(c) for c in cands if c.is_file()), None)


def _lmstudio_installed() -> bool:
    return any(p.exists() for p in (
        Path(os.getenv("LOCALAPPDATA", "_")) / "Programs" / "LM Studio",
        Path.home() / ".lmstudio",
        Path("/Applications/LM Studio.app"),
    ))


async def _scan_ollama(client: httpx.AsyncClient, root: str) -> tuple[dict, list[Engine]]:
    tags = await _get_json(client, root + "/api/tags")
    try:
        ps = await _get_json(client, root + "/api/ps")
        loaded = {m.get("name") for m in ps.get("models", [])}
    except (httpx.HTTPError, ValueError):
        loaded = set()

    async def one(m: dict) -> Engine | None:
        name = m.get("name") or m.get("model")
        details = m.get("details") or {}
        caps, context = None, 0
        try:
            r = await client.post(root + "/api/show", json={"model": name}, timeout=15)
            info = r.json()
            caps = info.get("capabilities")
            num_ctx = re.search(r"num_ctx\s+(\d+)", info.get("parameters") or "")
            context = int(num_ctx.group(1)) if num_ctx else 0
        except (httpx.HTTPError, ValueError):
            pass
        if caps is not None and "completion" not in caps:
            return None  # gömme modeli vb.
        if caps is None and _NOT_CHAT.search(name):
            return None
        ps_txt = str(details.get("parameter_size", "")).upper().rstrip("B")
        try:
            params = float(ps_txt) if ps_txt else 0.0
        except ValueError:
            params = 0.0
        return local_engine("ollama", root + "/v1", name, caps=caps, params_b=params, family=str(details.get("family", "")),
                            context=context or 4096, loaded=name in loaded)

    found = await asyncio.gather(*(one(m) for m in tags.get("models", [])))
    engines = [e for e in found if e]
    return {"state": "running", "detail": f"{len(engines)} sohbet modeli", "url": root}, engines


async def _scan_lmstudio(client: httpx.AsyncClient, root: str) -> tuple[dict, list[Engine]]:
    try:
        data = await _get_json(client, root + "/api/v0/models")
        rows = data.get("data", [])
        engines = []
        for m in rows:
            if m.get("type") not in (None, "llm", "vlm"):
                continue
            caps = ["completion"] + (["vision"] if m.get("type") == "vlm" else []) + (["tools"] if "tool_use" in (m.get("capabilities") or ["tool_use"]) else [])
            engines.append(local_engine("lmstudio", root + "/v1", m["id"], caps=caps, params_b=0, context=m.get("max_context_length") or 0,
                                        loaded=m.get("state") == "loaded"))
        return {"state": "running", "detail": f"{len(engines)} model", "url": root}, engines
    except (httpx.HTTPError, ValueError, KeyError):
        return await _scan_openai_local(client, "lmstudio", root)


async def _scan_openai_local(client: httpx.AsyncClient, pid: str, root: str, key: str = "") -> tuple[dict, list[Engine]]:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    base = root if root.endswith("/v1") else root + "/v1"
    data = await _get_json(client, base + "/models", headers=headers)
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("OpenAI uyumlu bir model listesi değil")
    engines = [local_engine(pid, base, m["id"], caps=None, params_b=0) for m in rows
               if isinstance(m, dict) and m.get("id") and not _NOT_CHAT.search(m["id"])]
    return {"state": "running", "detail": f"{len(engines)} model", "url": root}, engines


async def scan_local() -> dict:
    """Bu bilgisayardaki model sunucularını tarar. Döner: {sağlayıcı: durum}."""
    statuses: dict[str, dict] = {}
    found: list[Engine] = []
    own = f"http://127.0.0.1:{os.getenv('ADA_APP_PORT') or config.PORT}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=1.5)) as client:
        async def probe(pid: str, root: str):
            try:
                if pid == "ollama":
                    return pid, await _scan_ollama(client, root)
                if pid == "lmstudio":
                    return pid, await _scan_lmstudio(client, root)
                if root == own:
                    return pid, ({"state": "missing"}, [])
                return pid, await _scan_openai_local(client, pid, root)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                return pid, ({"state": "missing"}, [])

        results = await asyncio.gather(*(probe(pid, root) for pid, _, root in LOCAL))
        state = load_state()
        customs = state.get("custom", [])

        async def probe_custom(c: dict):
            try:
                return c["id"], await _scan_openai_local(client, c["id"], c["base_url"], os.getenv(c.get("key_env") or "", ""))
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
                return c["id"], ({"state": "down", "detail": f"Ulaşılamadı: {type(e).__name__}", "url": c["base_url"]}, [])

        results += await asyncio.gather(*(probe_custom(c) for c in customs))

    for pid, (st, engines) in results:
        if st["state"] == "missing":
            if pid == "ollama" and _ollama_exe():
                st = {"state": "installed", "detail": "Kurulu ama çalışmıyor — ADA gerektiğinde başlatır", "url": "http://127.0.0.1:11434"}
            elif pid == "lmstudio" and _lmstudio_installed():
                st = {"state": "installed", "detail": "Kurulu; LM Studio'da 'Local Server'ı açın", "url": "http://127.0.0.1:1234"}
        statuses[pid] = st
        if pid in PROVIDERS or any(c["id"] == pid for c in customs):
            for e in engines:
                e.label = e.label if pid in PROVIDERS else f"{_pretty(e.model)} ({provider_label(pid)})"
                if pid not in PROVIDERS:
                    e.local = False if not re.search(r"localhost|127\.0\.0\.1|0\.0\.0\.0", e.base_url) else True
            found += engines

    # Kapalı/kurulu-ama-çalışmayan sunucuların bilinen modelleri korunur (ADA gerekince başlatır)
    _merge(found, local_status=statuses, drop_missing={pid for pid, st in statuses.items() if st["state"] in ("running", "missing")})
    return statuses


async def list_cloud(pid: str) -> list[Engine]:
    """Anahtarı girilmiş bir bulut sağlayıcının modellerini listeler (anahtarı da doğrular)."""
    p = PROVIDERS[pid]
    key = api_key(pid)
    if not key:
        return []
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=6.0)) as client:
        if p.family == "gemini":
            data = await _get_json(client, p.base_url + "/models", params={"pageSize": 1000}, headers={"x-goog-api-key": key})
            out = []
            for m in data.get("models", []):
                name = m.get("name", "").removeprefix("models/")
                if "generateContent" not in (m.get("supportedGenerationMethods") or []) or not name.startswith("gemini"):
                    continue
                if _NOT_CHAT.search(name) or re.search(r"exp|preview-\d\d-\d\d|tuning|001$", name):
                    continue
                out.append(cloud_engine(p, name, context=m.get("inputTokenLimit") or 0))
            return out
        headers = {"Authorization": f"Bearer {key}"}
        if pid == "anthropic":
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
        data = await _get_json(client, p.models_url or p.base_url.rstrip("/") + "/models", headers=headers)
        rows = data.get("data", data if isinstance(data, list) else [])
        out = []
        for m in rows:
            mid = m.get("id") if isinstance(m, dict) else None
            if not mid or _NOT_CHAT.search(mid) or (pid == "zen" and _ZEN_OTHER.search(mid)):
                continue
            vision = tools = None
            if pid == "openrouter":
                arch = m.get("architecture") or {}
                vision = "image" in (arch.get("input_modalities") or [])
                params = m.get("supported_parameters")
                tools = ("tools" in params) if isinstance(params, list) else None
                if tools is False:
                    continue
            out.append(cloud_engine(p, mid, vision=vision, tools=tools, context=m.get("context_length") or 0))
        return out


async def scan_cloud(only: str | None = None) -> dict:
    """Anahtarı olan bulut sağlayıcıları listeler. Ağ hatasında bilinen modellerle devam edilir."""
    statuses: dict[str, dict] = {}
    found: list[Engine] = []
    targets = [p for p in CLOUD if (only is None or p.id == only)]

    async def one(p: Provider):
        if not api_key(p.id) or is_passive(p.id):
            return p.id, {"state": "nokey"}, []
        try:
            engines = await list_cloud(p.id)
            return p.id, {"state": "ok", "detail": f"{len(engines)} model"}, engines
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            msg = "Anahtar reddedildi" if code in (400, 401, 403) else f"Hata {code}"
            return p.id, {"state": "error", "detail": msg}, []
        except (httpx.HTTPError, ValueError) as e:
            # İnternet yoksa bilinen modellerle devam
            fallback = [cloud_engine(p, m) for m in p.fallback_models]
            return p.id, {"state": "offline", "detail": f"Liste alınamadı ({type(e).__name__})"}, fallback

    results = await asyncio.gather(*(one(p) for p in targets))
    for pid, st, engines in results:
        statuses[pid] = st
        found += engines
    _default_cloud_enable(found)
    _merge(found, cloud_status=statuses, drop_missing={pid for pid, st in statuses.items() if st["state"] in ("ok", "nokey")})
    return statuses


def _default_cloud_enable(found: list[Engine]) -> None:
    """Yeni bulunan bulut modellerinden hangileri varsayılan açık olsun: sağlayıcı başına en iyi birkaç model."""
    by_provider: dict[str, list[Engine]] = {}
    for e in found:
        by_provider.setdefault(e.provider, []).append(e)
    for pid, items in by_provider.items():
        if pid == "gemini":
            wanted = set(config.GEMINI_MODELS)
            pros = sorted((e for e in items if "pro" in e.model and "lite" not in e.model), key=lambda e: e.version, reverse=True)
            if pros:
                wanted.add(pros[0].model)
            for e in items:
                e.enabled = e.model in wanted
            continue
        ranked = sorted(items, key=lambda e: (e.tier * 2 + e.speed - e.cost, e.version), reverse=True)
        top = {e.id for e in ranked[: (2 if pid == "openrouter" else 3)]}
        for e in items:
            e.enabled = e.id in top


def _merge(found: list[Engine], *, drop_missing: set[str], local_status: dict | None = None,
           cloud_status: dict | None = None) -> None:
    with _lock:
        state = load_state()
        old = state["engines"]
        new: dict[str, dict] = {}
        for eid, data in old.items():
            pid = data.get("provider")
            # Taranan sağlayıcının artık listede olmayan modelleri silinir; diğerleri korunur
            if pid in drop_missing:
                continue
            new[eid] = data
        for e in found:
            prev = old.get(e.id)
            d = asdict(e)
            if prev and prev.get("user_set"):
                d["enabled"] = prev.get("enabled", True)
                d["user_set"] = True
            elif prev and not e.local:
                d["enabled"] = prev.get("enabled", d["enabled"])
            if e.local and not e.tools:
                d["enabled"] = bool(prev and prev.get("user_set") and prev.get("enabled"))
            new[e.id] = d
        state["engines"] = new
        if local_status is not None:
            state["local_status"] = local_status
        if cloud_status is not None:
            state["cloud_status"] = {**state.get("cloud_status", {}), **cloud_status}
        state["routes"] = {k: v for k, v in state["routes"].items() if v in new}
        state["scanned"] = datetime.now().isoformat(timespec="seconds")
        save_state(state)


async def scan_claude_code() -> dict:
    """Claude Code kurulu mu, oturum açık mı? Açıksa modelleri motor olarak eklenir."""
    from .providers import claude_code

    st, found = await claude_code.scan()
    _merge(found, cloud_status={CLAUDE_CODE: st}, drop_missing={CLAUDE_CODE})
    return st


async def scan_opencode() -> dict:
    """OpenCode kurulu mu, bağlı sağlayıcısı var mı? Varsa modelleri motor olarak eklenir."""
    from .providers import opencode

    st, found = await opencode.scan()
    _merge(found, cloud_status={OPENCODE: st}, drop_missing={OPENCODE})
    return st


async def scan_agents() -> None:
    """Bu bilgisayardaki kodlama ajanları (Claude Code, OpenCode)."""
    await asyncio.gather(scan_claude_code(), scan_opencode())


async def scan_all() -> None:
    await asyncio.gather(scan_local(), scan_cloud(), scan_agents())


# ---------------------------------------------------------------- yerel sunucuyu başlatma

_ollama_started = 0.0


async def ollama_up(root: str = "http://127.0.0.1:11434") -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            return (await c.get(root + "/api/version")).status_code == 200
    except httpx.HTTPError:
        return False


async def ensure_ollama(wait_s: float = 25.0) -> bool:
    """Ollama kapalıysa başlatır (kuruluysa). Çalışıyor ya da başlatıldıysa True."""
    global _ollama_started
    if await ollama_up():
        return True
    exe = _ollama_exe()
    if not exe:
        return False
    if time.time() - _ollama_started > 60:
        _ollama_started = time.time()
        flags = 0
        if config.IS_WIN:
            flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags, start_new_session=not config.IS_WIN)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        await asyncio.sleep(1)
        if await ollama_up():
            return True
    return False


# Ollama'nın varsayılan bağlamı 4096'dır; ADA'nın istemi (talimat + araçlar) ~7K token tuttuğu için
# kırpılır ve model görevi göremez. OpenAI uyumlu uç istek başına num_ctx kabul etmez: sunucu
# ayarı (OLLAMA_CONTEXT_LENGTH) gerekir. ADA bunu kendisi ayarlar ve sunucuyu yeniden başlatır.
OLLAMA_MIN_CTX = int(os.getenv("ADA_OLLAMA_CTX", "32768"))
OLLAMA_BUSY = 0  # şu an Ollama'yı kullanan tur sayısı (meşgulken yeniden başlatılmaz)
_ctx_fix: dict = {"done": False, "last": 0.0, "message": ""}


def _user_env(name: str) -> str:
    if config.IS_WIN:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                return str(winreg.QueryValueEx(k, name)[0])
        except OSError:
            return ""
    return os.getenv(name, "")


def _set_user_env(name: str, value: str) -> None:
    os.environ[name] = value  # ADA'nın başlattığı Ollama da görsün
    if not config.IS_WIN:
        return
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    try:  # açık programlara ortam değişkeninin değiştiğini duyur
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 3000, None)
    except Exception:  # noqa: BLE001
        pass


async def ollama_loaded_context(root: str = "http://127.0.0.1:11434") -> int | None:
    """Bellekteki Ollama modellerinin en küçük bağlam uzunluğu (yüklü model yoksa None)."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            models = (await c.get(root + "/api/ps")).json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return None
    values = [m.get("context_length") for m in models if m.get("context_length")]
    return min(values) if values else None


async def restart_ollama() -> bool:
    """Ollama'yı (tepsi uygulaması dahil) kapatıp güncel ortamla yeniden başlatır."""
    exe = _ollama_exe()
    if not exe:
        return False
    if config.IS_WIN:
        for name in ("ollama app.exe", "ollama.exe"):
            subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        await asyncio.sleep(2)
        app = Path(exe).with_name("ollama app.exe")
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        target = [str(app)] if app.is_file() else [exe, "serve"]
        subprocess.Popen(target, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags, env=os.environ.copy())
    else:
        subprocess.run(["pkill", "-f", "ollama serve"], capture_output=True)
        await asyncio.sleep(2)
        subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env=os.environ.copy())
    for _ in range(30):
        await asyncio.sleep(1)
        if await ollama_up():
            return True
    return False


async def ensure_ollama_context(force_restart: bool = False) -> str | None:
    """Ollama bağlamı yetersizse kalıcı olarak düzeltir. Yapılan düzeltmeyi açıklayan metin döner."""
    if not _ollama_exe() or not any(e.provider == "ollama" and e.enabled and not e.passive for e in all_engines()):
        return None
    wanted = str(OLLAMA_MIN_CTX)
    try:
        current = int(_user_env("OLLAMA_CONTEXT_LENGTH") or 0)
    except ValueError:
        current = 0
    changed = False
    if current < OLLAMA_MIN_CTX:
        _set_user_env("OLLAMA_CONTEXT_LENGTH", wanted)
        changed = True
    else:
        os.environ["OLLAMA_CONTEXT_LENGTH"] = str(current)
    loaded = await ollama_loaded_context()
    stale = loaded is not None and loaded < OLLAMA_MIN_CTX  # sunucu eski ayarla açılmış
    if not (changed or stale or force_restart):
        return None
    if time.time() - _ctx_fix["last"] < 300 or OLLAMA_BUSY > 0:
        return None  # yakın zamanda denendi ya da şu an kullanılıyor: sonra tekrar denenir
    _ctx_fix["last"] = time.time()
    ok = await restart_ollama() if await ollama_up() or changed else True
    msg = (f"Ollama bağlamı {loaded or 4096} → {wanted} token yapıldı ve sunucu yeniden başlatıldı"
           if ok else "Ollama bağlam ayarı yapıldı ama sunucu yeniden başlatılamadı")
    _ctx_fix.update(done=ok, message=msg)
    return msg


def local_servers_ok() -> bool | None:
    """Gözcü için: açık yerel motorların sunucusu yanıt veriyor mu? Yerel motor yoksa None."""
    used = {e.provider: e.base_url for e in all_engines() if e.local and e.enabled and not e.passive}
    if not used:
        return None
    for base in used.values():
        try:
            if httpx.get(base.rstrip("/") + "/models", timeout=4).status_code < 500:
                return True
        except httpx.HTTPError:
            continue
    return False
