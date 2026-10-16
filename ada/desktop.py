"""ADA masaüstü uygulaması: yerel sunucuyu başlatır ve arayüzü yerel bir pencerede açar.

    uv run ada-app           # geliştirme
    ADA.app / ADA.exe        # PyInstaller paketi (scripts/build_*.sh|ps1)

Pencerenin kırmızı kapat düğmesi ADA'yı durdurmaz: pencere Dock'a küçülür ve hedefler,
tetikleyiciler, zamanlanmış görevler çalışmaya devam eder (ADA_KEEP_RUNNING=false ile eski
davranış). Tamamen kapatmak için Cmd+Q veya arayüzdeki Kontrol → "ADA'dan çık". `--hidden` ile küçültülmüş başlar (oturum açılışı için).
Paketlenmiş uygulamada `--ada-run-python <kod>` bayrağı, run_python aracının kodu
çalıştırması için uygulamayı bir Python yorumlayıcısı gibi kullanır.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request


_DLL_DIR_HANDLE = None


def _keep_dll_dir_private() -> None:
    """PyInstaller'ın başlatıcısı SetDllDirectoryW(_internal) çağırır ve bu ayar alt süreçlere miras geçer: ADA'nın
    açtığı programlar (ör. bir VPN uygulaması) DLL'leri ADA'nın klasöründen yükleyip kilitler, güncelleme de o
    dosyaları değiştiremez. Klasör ADA'nın kendi modülleri için AddDllDirectory ile tutulur, alt süreçler için silinir."""
    global _DLL_DIR_HANDLE
    if not (getattr(sys, "frozen", False) and sys.platform == "win32"):
        return
    import ctypes

    try:
        _DLL_DIR_HANDLE = os.add_dll_directory(sys._MEIPASS)  # tutamaç kapanınca klasör listeden çıkar
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    except (OSError, AttributeError):
        pass


_INSTANCE_MUTEX = None


def _claim_single_instance(wait: float = 0) -> bool:
    """Windows: aynı veri klasörünü kullanan yalnızca bir ADA süreci çalışır (adlandırılmış mutex). Port denetiminden
    farklı olarak ilk kopyanın sunucusu henüz açılmamışken ya da başka portla başlatılmışken de geçerlidir;
    süreç kapanınca kilit kendiliğinden kalkar. `wait`: kilidin boşalması için en fazla bu kadar saniye beklenir."""
    global _INSTANCE_MUTEX
    if sys.platform != "win32":
        return True
    import ctypes
    import hashlib
    from pathlib import Path

    # config burada içe aktarılmaz: ADA_PORT'u içe aktarıldığı anda okur (bkz. config._app_home)
    if os.getenv("ADA_HOME"):
        home = Path(os.environ["ADA_HOME"]).expanduser()
    elif getattr(sys, "frozen", False):
        home = Path(os.getenv("APPDATA", Path.home())) / "ADA"
    else:
        home = Path(__file__).resolve().parent.parent
    name = "Local\\ADA-" + hashlib.sha1(str(home.resolve()).lower().encode()).hexdigest()[:16]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    end = time.time() + wait
    while True:
        handle = k32.CreateMutexW(None, False, name)
        if not handle:
            return True  # kilit oluşturulamadı: port denetimi yine çalışır
        if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS değil: ilk kopya biziz
            _INSTANCE_MUTEX = handle
            return True
        k32.CloseHandle(handle)
        if time.time() >= end:
            return False
        time.sleep(0.25)


def _run_python_mode() -> bool:
    if len(sys.argv) >= 3 and sys.argv[1] == "--ada-run-python":
        code = sys.argv[2]
        sys.argv = ["ada-python"]
        exec(compile(code, "<ada-run-python>", "exec"), {"__name__": "__main__"})  # noqa: S102 - run_python aracı
        return True
    return False


def _free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("Boş port bulunamadı")


def _wait_ready(url: str, timeout: float = 60) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url + "/api/status", timeout=2) as r:
                if r.status == 200:
                    return True
        except urllib.error.HTTPError as e:
            if e.code == 401:  # arayüz parolası açık: sunucu hazır, giriş ekranı pencerede gösterilir
                return True
            time.sleep(0.3)
        except Exception:  # noqa: BLE001 - henüz açılmadı
            time.sleep(0.3)
    return False


def _existing_instance(port: int) -> bool:
    """Bu bilgisayarda çalışan bir ADA kopyası var mı? Varsa penceresini öne getirip True döner
    (tek kopya denetimi). `--hidden` ile açılışta yalnızca True döner, pencere açılmaz."""
    import json

    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(base + "/api/instance", timeout=1) as r:
            data = json.load(r)
    except Exception:  # noqa: BLE001 - yanıt yok: çalışan kopya yok
        return False
    if not (isinstance(data, dict) and data.get("app") == "ADA"):
        return False
    if "--hidden" not in sys.argv:
        req = urllib.request.Request(base + "/api/instance/show", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5).close()
        except Exception:  # noqa: BLE001 - pencere gösterilemezse de ikinci kopya açılmaz
            pass
    return True


def _from_quit_command() -> bool:
    """Kapanma isteği Cmd+Q / "Quit ADA"dan mı geliyor? (pywebview bu durumda da `closing` olayını
    aynı iş parçacığında, applicationShouldTerminate_ içinden tetikler.)"""
    import inspect

    return any(f.function == "applicationShouldTerminate_" for f in inspect.stack(0))


def _first_run_permissions(url: str) -> None:
    """İlk açılışta (kullanıcı başındayken) tüm macOS izinlerini tek seferde iste.

    Böylece arka plan görevleri (sabah özeti, tetikleyiciler) izin penceresinde takılmaz. Uygulama sabit
    bir kimlikle imzalıysa izinler güncellemelerde korunur; bayrak dosyası bir kez çalışmasını sağlar.
    """
    import json

    from . import config

    flag = config.HOME_DIR / ".permissions_requested"
    if flag.exists():
        return
    time.sleep(2)  # pencere açılsın
    try:
        req = urllib.request.Request(url + "/api/permissions/request", method="POST")
        with urllib.request.urlopen(req, timeout=600) as r:
            results = json.load(r)
    except Exception as e:  # noqa: BLE001
        print(f"İzin isteği başarısız: {e}", file=sys.stderr)
        return
    flag.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    from .permissions import open_settings

    for name in ("Erişilebilirlik", "Ekran kaydı"):
        if any(r["name"] == name and not r["ok"] for r in results):
            open_settings(name)
            time.sleep(1.5)


# Pencere ADA dışında bir sayfaya giderse (link, dosya önizlemesi) adres çubuğu olmadığından geri dönülemiyordu:
# sayfanın üstüne Geri / ADA'ya dön çubuğu eklenir; Alt+Sol ok ve farenin geri tuşu da çalışır.
_BACK_LABELS = {
    "tr": ("Geri", "ADA'ya dön"), "en": ("Back", "Back to ADA"), "de": ("Zurück", "Zurück zu ADA"),
    "fr": ("Retour", "Retour à ADA"), "es": ("Atrás", "Volver a ADA"), "pt": ("Voltar", "Voltar ao ADA"),
    "it": ("Indietro", "Torna ad ADA"), "ru": ("Назад", "Вернуться в ADA"), "zh": ("返回", "回到 ADA"),
    "ja": ("戻る", "ADA に戻る"),
}

_BACK_JS = """(function(){
if (window.__adaBack) return; window.__adaBack = true;
var HOME = %(home)s, back = function(){ if (history.length > 1) history.back(); else location.href = HOME; };
var host = document.createElement('div');
host.style.cssText = 'all:initial;position:fixed;top:10px;left:10px;z-index:2147483647';
var root = host.attachShadow({mode:'closed'});
root.innerHTML = '<style>div{display:flex;gap:6px;font:600 13px -apple-system,Segoe UI,sans-serif}'
 + 'button{border:1px solid #d7d4cc;background:#fff;color:#1d1d1b;border-radius:999px;padding:7px 13px;cursor:pointer;'
 + 'box-shadow:0 2px 10px rgba(0,0,0,.18)}button:hover{background:#f1efe9}'
 + '@media(prefers-color-scheme:dark){button{background:#2a2926;color:#ecebe7;border-color:#45433e}button:hover{background:#34332f}}</style>'
 + '<div><button id="b"></button><button id="h"></button></div>';
root.getElementById('b').textContent = '\u2190 ' + %(back)s;
root.getElementById('h').textContent = %(home_label)s;
root.getElementById('b').onclick = back;
root.getElementById('h').onclick = function(){ location.href = HOME; };
(document.body || document.documentElement).appendChild(host);
window.addEventListener('keydown', function(e){ if ((e.altKey && e.key === 'ArrowLeft') || e.key === 'BrowserBack') { e.preventDefault(); back(); } }, true);
window.addEventListener('mouseup', function(e){ if (e.button === 3) { e.preventDefault(); back(); } }, true);
})();"""


def _needs_back_bar(current: str, base: str) -> bool:
    """ADA'nın kendi arayüzü (ana sayfa, giriş) dışındaki her sayfada geri çubuğu gösterilir."""
    if not current or current.startswith(("about:", "data:")):
        return False
    if not current.startswith(base):
        return True
    path = current[len(base):].split("?", 1)[0].split("#", 1)[0]
    return path not in ("", "/", "/login")


def _back_bar_js(base: str) -> str:
    import json

    from . import config

    back, home = _BACK_LABELS.get(config.ui_language(), _BACK_LABELS["en"])
    return _BACK_JS % {"home": json.dumps(base + "/"), "back": json.dumps(back), "home_label": json.dumps(home)}


def _hide_child_consoles(*, force: bool = False) -> bool:
    """Windows'ta konsolu olmayan ADA (pencereli exe) bir konsol programı başlatınca (PowerShell, git, python...)
    Windows her biri için siyah bir pencere açar. Bayrak verilmemiş her alt süreç CREATE_NO_WINDOW ile başlatılır:
    ADA'nın kendi çağrıları da, kütüphanelerinkiler de. Pencereli programlar (Not Defteri, tarayıcı) etkilenmez;
    kendi bayrağını veren çağrılara (ör. CREATE_NEW_CONSOLE) dokunulmaz. Konsoldan çalıştırılınca gerekmez."""
    if sys.platform != "win32":
        return False
    import ctypes
    import subprocess

    if not force and ctypes.windll.kernel32.GetConsoleWindow():
        return False
    if getattr(subprocess.Popen.__init__, "_ada_no_window", False):
        return True
    original = subprocess.Popen.__init__

    def __init__(self, *args, **kwargs):
        if len(args) < 14 and not kwargs.get("creationflags"):  # 14. konumsal parametre creationflags
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    __init__._ada_no_window = True
    subprocess.Popen.__init__ = __init__
    return True


def main() -> None:
    _keep_dll_dir_private()
    _hide_child_consoles()
    if _run_python_mode():
        return

    import multiprocessing

    multiprocessing.freeze_support()

    # Tek kopya: zaten çalışan bir ADA varsa onun penceresini öne getirip çık
    app_port = int(os.getenv("ADA_APP_PORT", "8765"))
    restarting = bool(os.environ.pop("ADA_RESTARTING", None))
    # "Yeniden başlat"ta eski kopya kapanana kadar beklenir; aksi hâlde çalışan bir ADA varsa ikincisi açılmaz
    if not _claim_single_instance(wait=20 if restarting else 0):
        _existing_instance(app_port)  # penceresini öne getir (sunucusu henüz açılmadıysa sessizce geçer)
        return
    if restarting:
        # "Yeniden başlat" ile açıldık: kapanmakta olan eski kopya portu bırakana kadar bekle
        # (yoksa rastgele bir porta düşülür ve tek kopya denetimi çalışmaz)
        for _ in range(60):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", app_port))
                    break
                except OSError:
                    time.sleep(0.25)
    elif _existing_instance(app_port):
        return

    # Uygulama yalnızca bu bilgisayardan erişilebilir; port sabit değilse boş bir port seçilir
    port = _free_port(app_port)
    os.environ["ADA_HOST"] = "127.0.0.1"
    os.environ["ADA_PORT"] = str(port)

    import uvicorn
    import webview
    from webview.menu import Menu, MenuAction, MenuSeparator

    from . import config
    from . import server as server_mod

    # Menüdeki Acil durdur / Devam et istekleri arayüz parolası açıkken de geçsin: bu sürece özel oturum
    import secrets

    own_session = secrets.token_urlsafe(32)
    server_mod.SESSIONS.add(own_session)

    server = uvicorn.Server(uvicorn.Config(server_mod.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="ada-server", daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    if not _wait_ready(url):
        print("ADA sunucusu başlatılamadı.", file=sys.stderr)
        sys.exit(1)

    if config.FROZEN and config.IS_MAC and "--hidden" not in sys.argv:
        threading.Thread(target=_first_run_permissions, args=(url,), daemon=True).start()

    def post(path: str) -> None:
        req = urllib.request.Request(url + path, method="POST", headers={"Cookie": f"ada_session={own_session}"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:  # noqa: BLE001
            print(f"{path}: {e}", file=sys.stderr)

    def open_folder(path) -> None:
        import subprocess

        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform.startswith("win"):
            os.startfile(str(path))  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(path)])

    quitting = threading.Event()
    window_ref: list = []

    def show_window() -> None:
        if window_ref:
            window_ref[0].restore()
            window_ref[0].show()

    def quit_app() -> None:
        quitting.set()
        if window_ref:
            window_ref[0].destroy()

    def on_closing():
        # Kırmızı kapat düğmesi ADA'yı durdurmaz: pencere Dock'a küçülür, hedefler/tetikleyiciler/
        # zamanlanmış görevler çalışmaya devam eder. Cmd+Q ve "ADA'dan çık" gerçekten kapatır.
        if quitting.is_set() or not config.KEEP_RUNNING or _from_quit_command():
            return True
        window_ref[0].minimize()
        return False

    # Windows/Linux'ta pencerenin üstündeki eski tip menü çubuğu yerine arayüzün kendi Kontrol menüsü
    # kullanılır (üst çubuktaki "Kontrol" düğmesi); macOS'ta sistem menü çubuğu da kalır.
    menu = None if not config.IS_MAC else [
        Menu(
            "Kontrol",  # "ADA" adı macOS'un uygulama menüsüyle çakışır
            [
                MenuAction("Pencereyi göster", show_window),
                MenuSeparator(),
                MenuAction("Acil durdur", lambda: post("/api/emergency-stop")),
                MenuAction("Devam et", lambda: post("/api/resume")),
                MenuSeparator(),
                MenuAction("Çıktılar klasörünü aç", lambda: open_folder(config.OUTPUTS_DIR)),
                MenuAction("Rutin klasörünü aç", lambda: open_folder(config.ROUTINES_DIR)),
                MenuAction("Veri klasörünü aç", lambda: open_folder(config.HOME_DIR)),
                MenuSeparator(),
                MenuAction("ADA'dan çık (arka plan işleri durur)", quit_app),
            ],
        )
    ]

    window = webview.create_window(
        "ADA",
        url,
        width=1320,
        height=880,
        min_size=(760, 560),
        confirm_close=not config.KEEP_RUNNING,
        text_select=True,
        minimized="--hidden" in sys.argv,  # oturum açılışında sessiz başlat
        localization={"global.quitConfirmation": "ADA kapatılsın mı? Çalışan görevler ve arka plan işleri durur."
                      if config.ui_language() == "tr" else
                      "Close ADA? Running tasks and background jobs will stop."},
    )
    window_ref.append(window)
    window.events.closing += on_closing

    def on_loaded():
        try:
            if _needs_back_bar(window.get_current_url() or "", url):
                window.evaluate_js(_back_bar_js(url))
        except Exception as e:  # noqa: BLE001 - çubuk eklenemese de pencere çalışmaya devam eder
            print(f"Geri çubuğu eklenemedi: {e}", file=sys.stderr)

    window.events.loaded += on_loaded
    # İkinci kopya /api/instance/show çağırınca pencereyi öne getir (pywebview çağrıları iş parçacığı
    # güvenlidir: restore/show içeride GUI iş parçacığına yönlendirilir)
    server_mod.SHOW_WINDOW_HOOK = show_window
    server_mod.QUIT_HOOK = quit_app
    webview.start(menu=menu or [],private_mode=False, storage_path=str(config.HOME_DIR / "webview"))
    server.should_exit = True
    thread.join(timeout=5)


if __name__ == "__main__":
    main()
