"""Uygulama içi güncelleme: yeni sürüm var mı diye bakar, kurulum programını indirir, doğrular ve çalıştırır.

Kaynak (ADA_UPDATE_URL):
  - "github:sahip/depo"  → o deponun en son GitHub Release'i; "ADA-Setup.exe" adlı ek dosya indirilir
  - "https://…/latest.json" → {"version": "0.2.0", "url": "ADA-Setup.exe", "sha256": "…", "notes": "…"}
    (`url` göreli olabilir; scripts\\publish_update.ps1 bu dosyayı üretir)

Güvenlik: adres https olmalıdır (yalnızca 127.0.0.1 için http kabul edilir). İndirilen dosyanın SHA-256 özeti
(verilmişse) ve Authenticode imzası denetlenir: çalışan ADA.exe imzalıysa güncelleme de AYNI sertifikayla
imzalanmış olmalıdır; değilse kurulmaz. Kurulum sessiz çalışır, ayarlara/verilere dokunmaz ve ADA'yı yeniden açar.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from . import __version__, config

# Yayındaki kurulum dosyası; eski sürümler "ADA-Kurulum.exe" adını arar, ikisi de yayımlanır
ASSET_NAMES = ("ADA-Setup.exe", "ADA-Kurulum.exe")
UPDATES_DIR = config.HOME_DIR / "updates"
CHECK_HOURS = 6
MAX_BYTES = 600 * 1024 * 1024

_lock = threading.Lock()
_state: dict = {
    "latest": "", "url": "", "sha256": "", "notes": "", "checked": "", "error": "",
    "phase": "idle",  # idle | downloading | verifying | installing | error
    "progress": 0, "notified": "",
}
_task: asyncio.Task | None = None


class UpdateError(Exception):
    pass


def source() -> str:
    return os.getenv("ADA_UPDATE_URL", config.UPDATE_URL).strip()


def parse_version(text: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", (text or "").split("+")[0])
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _check_url(url: str) -> str:
    parts = urlsplit(url)
    local = parts.hostname in ("127.0.0.1", "localhost")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise UpdateError("Güncelleme adresi https:// ile başlamalı.")
    return url


def can_install() -> bool:
    return config.FROZEN and config.IS_WIN


def status() -> dict:
    with _lock:
        s = dict(_state)
    return {
        "current": __version__,
        "source": source(),
        "latest": s["latest"],
        "available": bool(s["latest"]) and is_newer(s["latest"]),
        "notes": s["notes"],
        "checked": s["checked"],
        "error": s["error"],
        "phase": s["phase"],
        "progress": s["progress"],
        "can_install": can_install(),
        "auto": config.UPDATE_AUTO_CHECK,
    }


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


async def _fetch(client: httpx.AsyncClient) -> dict:
    src = source()
    if not src:
        raise UpdateError("Güncelleme adresi ayarlı değil (Ayarlar → Genel → Güncelleme adresi).")
    if src.startswith("github:"):
        repo = src.removeprefix("github:").strip().strip("/")
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            raise UpdateError("GitHub kaynağı 'github:sahip/depo' biçiminde olmalı.")
        headers = {"Accept": "application/vnd.github+json"}
        if os.getenv("ADA_UPDATE_TOKEN"):  # özel depo için
            headers["Authorization"] = "Bearer " + os.environ["ADA_UPDATE_TOKEN"]
        r = await client.get(f"https://api.github.com/repos/{repo}/releases/latest", headers=headers)
        if r.status_code == 404:
            raise UpdateError("Bu depoda yayımlanmış sürüm bulunamadı.")
        r.raise_for_status()
        data = r.json()
        assets = {a.get("name"): a for a in data.get("assets", [])}
        asset = next((assets[n] for n in ASSET_NAMES if n in assets), None)
        if asset is None:
            raise UpdateError(f"Son sürümde {ASSET_NAMES[0]} dosyası yok.")
        digest = str(asset.get("digest") or "")
        return {"version": str(data.get("tag_name", "")), "url": asset["browser_download_url"],
                "sha256": digest.removeprefix("sha256:") if digest.startswith("sha256:") else "",
                "notes": str(data.get("body") or "")}
    _check_url(src)
    r = await client.get(src)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or not data.get("version") or not data.get("url"):
        raise UpdateError("Güncelleme dosyası geçersiz (version ve url alanları gerekli).")
    return {"version": str(data["version"]), "url": urljoin(src, str(data["url"])),
            "sha256": str(data.get("sha256") or ""), "notes": str(data.get("notes") or "")}


async def check() -> dict:
    """Kaynağa bakar; sonucu duruma yazar ve durumu döner."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=True) as client:
            info = await _fetch(client)
        _check_url(info["url"])
        _set(latest=info["version"].lstrip("vV"), url=info["url"], sha256=info["sha256"].lower(), notes=info["notes"][:4000],
             checked=datetime.now().isoformat(timespec="seconds"), error="")
    except UpdateError as e:
        _set(error=str(e), checked=datetime.now().isoformat(timespec="seconds"))
    except (httpx.HTTPError, ValueError, KeyError) as e:
        _set(error=f"Güncelleme denetlenemedi ({type(e).__name__}).", checked=datetime.now().isoformat(timespec="seconds"))
    return status()


# ---------------------------------------------------------------- imza

def signer(path: Path | str) -> tuple[str, str]:
    """(durum, imzalayan sertifikanın parmak izi). İmzasız dosyada parmak izi boştur."""
    if not config.IS_WIN:
        return "", ""
    script = ("$s = Get-AuthenticodeSignature -LiteralPath $env:ADA_SIG_FILE; "
              "Write-Output ([string]$s.Status); if ($s.SignerCertificate) { Write-Output $s.SignerCertificate.Thumbprint }")
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=60,
            env={**os.environ, "ADA_SIG_FILE": str(path)}, creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return "", ""
    return (out[0] if out else ""), (out[1] if len(out) > 1 else "")


# "UnknownError": imza sağlam ama kök sertifika bu bilgisayarda güvenilir değil (kendinden imzalı sertifika)
_OK_STATUS = ("Valid", "UnknownError")


def verify(path: Path, sha256: str = "", own_exe: Path | str | None = None) -> None:
    """İndirilen kurulum programını doğrular; sorun varsa UpdateError fırlatır."""
    if sha256:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        if h.hexdigest().lower() != sha256.lower():
            raise UpdateError("İndirilen dosya bozuk ya da değiştirilmiş (SHA-256 özeti tutmuyor).")
    own_status, own_thumb = signer(own_exe or sys.executable)
    if not own_thumb:
        return  # bu kopya imzasız: karşılaştıracak sertifika yok
    new_status, new_thumb = signer(path)
    if new_status not in _OK_STATUS or not new_thumb:
        raise UpdateError(f"Güncelleme dosyasının imzası geçersiz ({new_status or 'okunamadı'}).")
    if new_thumb.upper() != own_thumb.upper():
        raise UpdateError("Güncelleme başka bir sertifikayla imzalanmış; güvenlik nedeniyle kurulmadı.")


# ---------------------------------------------------------------- indirme ve kurulum

async def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True) as client:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            done = 0
            with tmp.open("wb") as f:
                async for chunk in r.aiter_bytes(1 << 18):
                    done += len(chunk)
                    if done > MAX_BYTES:
                        raise UpdateError("Güncelleme dosyası beklenenden büyük.")
                    f.write(chunk)
                    if total:
                        _set(progress=min(99, int(done * 100 / total)))
    tmp.replace(dest)


def launch_installer(path: Path) -> None:
    """Kurulumu sessiz başlatır; kurulum ADA'yı kapatır, dosyaları değiştirir ve ADA'yı yeniden açar."""
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH=1"],
                     close_fds=True, creationflags=flags, cwd=str(path.parent))


async def _run_install(on_ready) -> None:
    with _lock:
        url, sha, version = _state["url"], _state["sha256"], _state["latest"]
    try:
        for old in UPDATES_DIR.glob("*") if UPDATES_DIR.exists() else []:
            old.unlink(missing_ok=True)
        dest = UPDATES_DIR / f"ADA-Setup-{re.sub(r'[^0-9A-Za-z.]', '_', version)}.exe"
        _set(phase="downloading", progress=0, error="")
        await _download(_check_url(url), dest)
        _set(phase="verifying", progress=100)
        await asyncio.to_thread(verify, dest, sha)
        _set(phase="installing")
        await asyncio.to_thread(launch_installer, dest)
        await asyncio.sleep(1.0)  # arayüz "kuruluyor" durumunu görsün
        on_ready()
    except UpdateError as e:
        _set(phase="error", error=str(e))
    except (httpx.HTTPError, OSError) as e:
        _set(phase="error", error=f"Güncelleme indirilemedi ({type(e).__name__}).")


def start_install(on_ready) -> None:
    """İndir → doğrula → kurulumu başlat → `on_ready()` (ADA'yı kapatır). Arka planda çalışır."""
    global _task
    if not can_install():
        raise UpdateError("Güncelleme yalnızca kurulu Windows uygulamasında yapılabilir.")
    s = status()
    if not s["available"]:
        raise UpdateError("Kurulacak yeni sürüm yok.")
    if s["phase"] in ("downloading", "verifying", "installing"):
        return
    _set(phase="downloading", progress=0, error="")
    _task = asyncio.create_task(_run_install(on_ready))


async def loop() -> None:
    """Açılıştan kısa süre sonra ve ardından düzenli aralıklarla yeni sürüm var mı diye bakar."""
    if not config.UPDATE_AUTO_CHECK or os.getenv("ADA_NO_DISCOVERY"):
        return
    await asyncio.sleep(45)
    while True:
        if source():
            s = await check()
            if s["available"] and _state["notified"] != s["latest"]:
                _set(notified=s["latest"])
                try:
                    from .notify import notify

                    notify("ADA güncellemesi var", f"Sürüm {s['latest']} hazır. Kontrol → Güncelle ile kurabilirsiniz.")
                except Exception:  # noqa: BLE001 - bildirim gösterilemezse de denetim sürer
                    pass
        await asyncio.sleep(CHECK_HOURS * 3600)
