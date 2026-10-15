"""Tor: ADA'nın siteleri kullanıcının kendi bağlantısından değil Tor ağı üzerinden açması.

ADA_TOR=true (varsayılan) iken web_search / web_fetch istekleri ve open_item ile açılan siteler Tor'dan geçer.
Sıra: çalışan bir Tor varsa (ADA'nın kendi Tor'u, Tor Browser 9150, tor servisi 9050) o kullanılır; yoksa
ADA'nın kendi Tor'u (HOME_DIR/tor) başlatılır; kurulu değilse Tor Projesi'nin resmî "Expert Bundle"ı
indirilir, yayımlanan SHA-256 listesiyle doğrulanıp kurulur. Tor açılamazsa istek doğrudan GÖNDERİLMEZ;
kullanıcı istemiyorsa Ayarlar'dan kapatır.

Tarayıcıda açma: Tor Browser kuruluysa site onunla açılır; değilse Edge/Chrome, ADA'ya ayrılmış ayrı bir
profille ve Tor proxy'siyle açılır (kullanıcının kendi tarayıcı profiline ve çerezlerine dokunulmaz).
"""

from __future__ import annotations

import atexit
import hashlib
import io
import os
import platform
import shutil
import socket
import subprocess
import tarfile
import threading
from pathlib import Path

import httpx

from . import config

TOR_DIR = config.HOME_DIR / "tor"
BUNDLE_DIR = TOR_DIR / "bundle"
DATA_DIR = TOR_DIR / "data"
BROWSER_PROFILE = TOR_DIR / "browser-profile"
OWN_PORT = 9350  # ADA'nın kendi Tor'unun SOCKS portu (Tor Browser'ın 9150'si ve servisin 9050'si ile çakışmasın)
KNOWN_PORTS = (OWN_PORT, 9150, 9050)
BOOT_TIMEOUT = 120
DIST = "https://dist.torproject.org/torbrowser"
RELEASE_JSON = "https://aus1.torproject.org/torbrowser/update_3/release/download-{}.json"

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_port: int | None = None


class TorError(Exception):
    pass


def enabled() -> bool:
    return os.getenv("ADA_TOR", "true").strip().lower() not in ("false", "0", "no", "off")


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _is_socks(port: int) -> bool:
    """Port bir SOCKS5 sunucusu mu (Tor öyle yanıt verir); rastgele bir servisi Tor sanmayalım."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as s:
            s.sendall(b"\x05\x01\x00")
            return s.recv(2) == b"\x05\x00"
    except OSError:
        return False


# --- kurulum ---------------------------------------------------------------------------------------------

def _platform() -> tuple[str, str]:
    """(Expert Bundle adı, sürüm bilgisinin okunduğu dosya adı)"""
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    x64 = machine in ("amd64", "x86_64") or arm
    if config.IS_WIN:
        return ("windows-x86_64" if x64 else "windows-i686"), "windows-x86_64"
    if config.IS_MAC:
        return ("macos-aarch64" if arm else "macos-x86_64"), "macos"
    return ("linux-x86_64" if x64 else "linux-i686"), "linux-x86_64"


def _exe_name() -> str:
    return "tor.exe" if config.IS_WIN else "tor"


def _find_tor() -> Path | None:
    own = BUNDLE_DIR / "tor" / _exe_name()
    if own.exists():
        return own
    found = shutil.which("tor")
    return Path(found) if found else None


def install() -> Path:
    """Tor Expert Bundle'ın son kararlı sürümünü indirir, SHA-256 ile doğrular ve HOME_DIR/tor/bundle'a açar."""
    bundle, channel = _platform()
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as http:
            version = http.get(RELEASE_JSON.format(channel)).raise_for_status().json()["version"]
            name = f"tor-expert-bundle-{bundle}-{version}.tar.gz"
            sums = http.get(f"{DIST}/{version}/sha256sums-signed-build.txt").raise_for_status().text
            expected = next((ln.split()[0] for ln in sums.splitlines() if ln.strip().endswith(" " + name)), "")
            if not expected:
                raise TorError(f"{name} için SHA-256 özeti bulunamadı.")
            data = http.get(f"{DIST}/{version}/{name}", timeout=300).raise_for_status().content
    except httpx.HTTPError as e:
        raise TorError(f"Tor indirilemedi: {e}") from e
    if hashlib.sha256(data).hexdigest() != expected.lower():
        raise TorError("İndirilen Tor dosyasının SHA-256 özeti tutmuyor; kurulmadı.")

    tmp = TOR_DIR / "bundle.new"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(tmp, filter="data")
    exe = tmp / "tor" / _exe_name()
    if not exe.exists():
        raise TorError("Tor paketinde tor programı bulunamadı.")
    if not config.IS_WIN:
        exe.chmod(0o755)
    shutil.rmtree(BUNDLE_DIR, ignore_errors=True)
    tmp.rename(BUNDLE_DIR)
    return BUNDLE_DIR / "tor" / _exe_name()


# --- çalıştırma ------------------------------------------------------------------------------------------

def _start(exe: Path) -> None:
    global _proc
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    args = [str(exe), "--SocksPort", f"127.0.0.1:{OWN_PORT}", "--DataDirectory", str(DATA_DIR), "--Log", "notice stdout",
            "--ClientOnly", "1", "__OwningControllerProcess", str(os.getpid())]  # ADA kapanınca Tor da kapanır
    geo = BUNDLE_DIR / "data"
    if exe.is_relative_to(BUNDLE_DIR) and (geo / "geoip").exists():
        args += ["--GeoIPFile", str(geo / "geoip"), "--GeoIPv6File", str(geo / "geoip6")]
    env = dict(os.environ)
    if not config.IS_WIN and not config.IS_MAC:
        env["LD_LIBRARY_PATH"] = str(exe.parent) + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    flags = subprocess.CREATE_NO_WINDOW if config.IS_WIN else 0
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", env=env, creationflags=flags)
    done = threading.Event()
    tail: list[str] = []

    def read():
        for line in proc.stdout:
            tail.append(line.strip())
            del tail[:-5]
            if "Bootstrapped 100%" in line:
                done.set()
        done.set()

    threading.Thread(target=read, daemon=True).start()
    if not done.wait(BOOT_TIMEOUT) or proc.poll() is not None:
        proc.kill()
        reason = tail[-1] if tail else "zaman aşımı"
        raise TorError(f"Tor ağına bağlanılamadı ({reason}).")
    _proc = proc
    atexit.register(stop)


def stop() -> None:
    global _proc
    if _proc and _proc.poll() is None:
        _proc.terminate()
    _proc = None


def ensure() -> int:
    """Kullanılabilir bir Tor SOCKS portu döner; gerekirse Tor'u kurar ve başlatır."""
    global _port
    with _lock:
        if _port and _is_socks(_port):
            return _port
        for port in KNOWN_PORTS:
            if _is_socks(port):
                _port = port
                return port
        if _port_open(OWN_PORT):
            raise TorError(f"{OWN_PORT} portu başka bir program tarafından kullanılıyor.")
        exe = _find_tor() or install()
        _start(exe)
        _port = OWN_PORT
        return OWN_PORT


def proxy_url() -> str | None:
    """Tor açıksa istekler için proxy adresi (alan adı da Tor'da çözülür: socks5h); kapalıysa None."""
    if not enabled():
        return None
    try:
        return f"socks5h://127.0.0.1:{ensure()}"
    except TorError as e:
        raise TorError(f"{e} Siteler Tor olmadan açılmadı; isterseniz Ayarlar'dan Tor'u kapatabilirsiniz.") from e


# --- tarayıcıda açma -------------------------------------------------------------------------------------

def _tor_browser() -> list[str] | None:
    home = Path.home()
    if config.IS_WIN:
        for base in (home / "Desktop", home / "OneDrive" / "Desktop", Path(os.getenv("LOCALAPPDATA", home)),
                     Path(os.getenv("ProgramFiles", "C:/Program Files"))):
            exe = base / "Tor Browser" / "Browser" / "firefox.exe"
            if exe.exists():
                return [str(exe)]
    elif config.IS_MAC:
        app = Path("/Applications/Tor Browser.app")
        if app.exists():
            return ["open", "-a", str(app), "--args"]
    else:
        for exe in (home / "tor-browser" / "Browser" / "start-tor-browser", home / ".local/share/torbrowser/tbb/x86_64/tor-browser/Browser/start-tor-browser"):
            if exe.exists():
                return [str(exe)]
    return None


def _chromium() -> str | None:
    if config.IS_WIN:
        pf, pf86, local = os.getenv("ProgramFiles", ""), os.getenv("ProgramFiles(x86)", ""), os.getenv("LOCALAPPDATA", "")
        candidates = [Path(b) / p for b in (pf86, pf, local) if b
                      for p in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe")]
    elif config.IS_MAC:
        candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                      Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                      Path("/Applications/Chromium.app/Contents/MacOS/Chromium")]
    else:
        candidates = [Path(p) for p in (shutil.which(n) for n in ("chromium", "chromium-browser", "google-chrome", "microsoft-edge")) if p]
    return next((str(c) for c in candidates if c.exists()), None)


def open_url(url: str) -> str:
    """Siteyi Tor üzerinden görünür bir tarayıcıda açar."""
    browser = _tor_browser()
    if browser:
        subprocess.Popen([*browser, url], close_fds=True)
        return f"Tor Browser'da açıldı: {url}"
    port = int(proxy_url().rsplit(":", 1)[1])
    exe = _chromium()
    if not exe:
        raise TorError("Tor Browser ya da Edge/Chrome bulunamadı; siteyi Tor üzerinden açacak tarayıcı yok.")
    BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([
        exe, f"--user-data-dir={BROWSER_PROFILE}", f"--proxy-server=socks5://127.0.0.1:{port}",
        "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",  # alan adı yerelde çözülmesin (DNS sızıntısı)
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--no-first-run", "--no-default-browser-check",
        "--incognito", url,
    ], close_fds=True)
    return f"Tor üzerinden açıldı ({Path(exe).stem}, ayrı profil): {url}"


def status() -> dict:
    port = next((p for p in KNOWN_PORTS if _is_socks(p)), None)
    return {"enabled": enabled(), "running": port is not None, "port": port, "installed": _find_tor() is not None,
            "browser": bool(_tor_browser())}


def warm_up() -> None:
    """Açılışta arka planda Tor'u hazırlar ki ilk web isteği beklemesin."""
    if not enabled():
        return

    def run():
        try:
            ensure()
        except Exception:  # noqa: BLE001 - ilk istekte hata yeniden ve kullanıcıya gösterilir
            pass

    threading.Thread(target=run, daemon=True).start()
