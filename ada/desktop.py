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
import urllib.request


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
                if r.status in (200, 401):
                    return True
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


def main() -> None:
    if _run_python_mode():
        return

    import multiprocessing

    multiprocessing.freeze_support()

    # Tek kopya: zaten çalışan bir ADA varsa onun penceresini öne getirip çık
    app_port = int(os.getenv("ADA_APP_PORT", "8765"))
    if os.environ.pop("ADA_RESTARTING", None):
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
        req = urllib.request.Request(url + path, method="POST")
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
        localization={"global.quitConfirmation": "ADA kapatılsın mı? Çalışan görevler ve arka plan işleri durur."},
    )
    window_ref.append(window)
    window.events.closing += on_closing
    # İkinci kopya /api/instance/show çağırınca pencereyi öne getir (pywebview çağrıları iş parçacığı
    # güvenlidir: restore/show içeride GUI iş parçacığına yönlendirilir)
    server_mod.SHOW_WINDOW_HOOK = show_window
    server_mod.QUIT_HOOK = quit_app
    webview.start(menu=menu or [],private_mode=False, storage_path=str(config.HOME_DIR / "webview"))
    server.should_exit = True
    thread.join(timeout=5)


if __name__ == "__main__":
    main()
