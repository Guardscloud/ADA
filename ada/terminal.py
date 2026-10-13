"""Terminal: ADA penceresinde gerçek komut satırı oturumları (Windows: PowerShell, macOS/Linux: zsh/bash).

Oturumlar sunucuda yaşar: panel kapansa da süreç çalışmaya devam eder (ör. `claude remote-control`);
panel yeniden açılınca son çıktı (BUFFER_CHARS) yeniden gösterilir. Windows'ta ConPTY (pywinpty),
diğerlerinde pty kullanılır; böylece etkileşimli programlar (soru soran, renkli, tam ekran) çalışır.

Kalıcı komutlar (workspace/.ada/terminal.json) ADA açılınca kendiliğinden başlar ve kapanırsa yeniden
başlatılır (`Keeper`). Art arda çöken bir komut saatte en fazla MAX_RESTARTS_PER_HOUR kez başlatılır,
sonra bildirim verilip vazgeçilir (Gözcü bunu sorun olarak gösterir).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import config

FILE = config.DATA_DIR / "terminal.json"
BUFFER_CHARS = 200_000  # oturum başına saklanan son çıktı (yeniden bağlanınca gösterilir)
MAX_SESSIONS = 12
MAX_RESTARTS_PER_HOUR = 5
RESTART_DELAY_SECONDS = 10
KEEPER_TICK_SECONDS = 5
IS_WIN = sys.platform.startswith("win")

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]|\r")
_lock = threading.RLock()


def plain(text: str) -> str:
    """Terminal çıktısını düz metne çevirir (renk/imleç kodları atılır) — modele ve özetlere."""
    return _ANSI.sub("", text)


# ---------------------------------------------------------------- süreç arka uçları

def default_shell() -> list[str]:
    override = os.getenv("ADA_TERMINAL_SHELL", "").strip()
    if override:
        return override.split()
    if IS_WIN:
        return [shutil.which("pwsh") or "powershell.exe", "-NoLogo"]
    shell = os.getenv("SHELL") or ("/bin/zsh" if Path("/bin/zsh").exists() else "/bin/bash")
    return [shell, "-l"]


def command_argv(command: str) -> list[str]:
    """Tek bir komutu kullanıcının kabuğunda çalıştıran argv (komut bitince oturum da biter)."""
    shell = default_shell()
    if IS_WIN:
        return [*shell, "-Command", command]
    return [shell[0], "-lc", command]


def _child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI", "_MEIPASS"))}
    env["TERM"] = "xterm-256color"
    env.setdefault("COLORTERM", "truecolor")
    return env


class _WinPty:
    def __init__(self, argv: list[str], cwd: str, cols: int, rows: int) -> None:
        from winpty import PtyProcess

        self.proc = PtyProcess.spawn(argv, cwd=cwd, env=_child_env(), dimensions=(rows, cols))
        self.pid = self.proc.pid

    def read(self) -> str:
        return self.proc.read(65536)  # EOFError: süreç bitti

    def write(self, data: str) -> None:
        self.proc.write(data)

    def resize(self, cols: int, rows: int) -> None:
        self.proc.setwinsize(rows, cols)

    def alive(self) -> bool:
        return self.proc.isalive()

    def exit_code(self) -> int | None:
        return self.proc.exitstatus

    def kill(self) -> None:
        try:
            self.proc.terminate(force=True)
        except Exception:  # noqa: BLE001
            pass


class _PosixPty:
    def __init__(self, argv: list[str], cwd: str, cols: int, rows: int) -> None:
        import subprocess

        master, slave = os.openpty()
        self.fd = master
        self._set_size(cols, rows)
        self.proc = subprocess.Popen(
            argv, cwd=cwd, env=_child_env(), stdin=slave, stdout=slave, stderr=slave,
            start_new_session=True, close_fds=True,
        )
        os.close(slave)
        self.pid = self.proc.pid
        self._decoder = __import__("codecs").getincrementaldecoder("utf-8")("replace")

    def _set_size(self, cols: int, rows: int) -> None:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def read(self) -> str:
        try:
            data = os.read(self.fd, 65536)
        except OSError:
            data = b""
        if not data:
            raise EOFError
        return self._decoder.decode(data)

    def write(self, data: str) -> None:
        os.write(self.fd, data.encode())

    def resize(self, cols: int, rows: int) -> None:
        self._set_size(cols, rows)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def exit_code(self) -> int | None:
        return self.proc.poll()

    def kill(self) -> None:
        import signal

        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass


def _kill_tree(pid: int) -> None:
    """Kabuğun altında çalışan programları da kapatır (ör. kabuktan başlatılan claude)."""
    try:
        import psutil

        children = psutil.Process(pid).children(recursive=True)
    except Exception:  # noqa: BLE001 - süreç zaten bitmiş olabilir
        return
    for c in children:
        try:
            c.kill()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- oturumlar

class Session:
    def __init__(self, title: str, argv: list[str], cwd: str, cols: int, rows: int,
                 command: str = "", saved_id: str | None = None, source: str = "arayüz") -> None:
        self.id = uuid.uuid4().hex[:8]
        self.title = title
        self.command = command
        self.cwd = cwd
        self.saved_id = saved_id
        self.source = source  # "arayüz" | "ada" (yapay zekâ aracı) | "kalıcı"
        self.started = datetime.now().isoformat(timespec="seconds")
        self.ended: str | None = None
        self.exit_code: int | None = None
        self.cols, self.rows = cols, rows
        self._chunks: list[str] = []
        self._size = 0
        self.total = 0  # şimdiye kadar üretilen toplam karakter (okuma imleci için)
        self._subs: set[asyncio.Queue] = set()
        self._loop = asyncio.get_running_loop()
        self.proc = (_WinPty if IS_WIN else _PosixPty)(argv, cwd, cols, rows)
        self.alive = True
        threading.Thread(target=self._reader, name=f"terminal-{self.id}", daemon=True).start()

    # -- okuma (ayrı thread) → olay döngüsü
    def _reader(self) -> None:
        while True:
            try:
                data = self.proc.read()
            except (EOFError, OSError):
                break
            except Exception:  # noqa: BLE001
                break
            if data:
                try:
                    self._loop.call_soon_threadsafe(self._emit, data)
                except RuntimeError:  # olay döngüsü kapandı (sunucu duruyor)
                    self.kill()
                    return
        for _ in range(50):  # çıkış kodunun oturması için kısa bekleme
            if not self.proc.alive():
                break
            time.sleep(0.1)
        try:
            self._loop.call_soon_threadsafe(self._finish, self.proc.exit_code())
        except RuntimeError:
            pass

    def _emit(self, data: str) -> None:
        self._chunks.append(data)
        self._size += len(data)
        self.total += len(data)
        while self._size > BUFFER_CHARS and len(self._chunks) > 1:
            self._size -= len(self._chunks.pop(0))
        for q in list(self._subs):
            q.put_nowait({"t": "out", "d": data})

    def _finish(self, code: int | None) -> None:
        if not self.alive:
            return
        self.alive = False
        self.exit_code = code
        self.ended = datetime.now().isoformat(timespec="seconds")
        note = f"\r\n\x1b[90m[süreç bitti{'' if code is None else f', çıkış kodu {code}'}]\x1b[0m\r\n"
        self._emit(note)
        for q in list(self._subs):
            q.put_nowait({"t": "exit", "code": code})

    # -- arayüz
    def backlog(self) -> str:
        return "".join(self._chunks)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    @property
    def watchers(self) -> int:
        return len(self._subs)

    def write(self, data: str) -> None:
        if self.alive:
            self.proc.write(data)

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(20, min(int(cols), 500)), max(5, min(int(rows), 200))
        if self.alive and (cols, rows) != (self.cols, self.rows):
            self.cols, self.rows = cols, rows
            try:
                self.proc.resize(cols, rows)
            except Exception:  # noqa: BLE001
                pass

    def kill(self) -> None:
        if self.alive:
            _kill_tree(self.proc.pid)
            self.proc.kill()

    def tail(self, chars: int = 4000) -> str:
        return plain(self.backlog())[-chars:]

    def info(self) -> dict:
        return {
            "id": self.id, "title": self.title, "command": self.command, "cwd": self.cwd,
            "saved_id": self.saved_id, "source": self.source, "alive": self.alive,
            "started": self.started, "ended": self.ended, "exit_code": self.exit_code,
            "watchers": self.watchers,
        }


SESSIONS: dict[str, Session] = {}


def list_sessions() -> list[dict]:
    return [s.info() for s in SESSIONS.values()]


def get(session_id: str) -> Session | None:
    return SESSIONS.get(session_id)


def _resolve_cwd(cwd: str) -> str:
    if cwd:
        p = Path(cwd).expanduser()
        if not p.is_absolute():
            p = config.WORKSPACE / p
        if not p.is_dir():
            raise ValueError(f"Klasör bulunamadı: {cwd}")
        return str(p.resolve())
    return str(Path.home())


def open_session(command: str = "", title: str = "", cwd: str = "", cols: int = 120, rows: int = 32,
                 saved_id: str | None = None, source: str = "arayüz") -> Session:
    """Yeni oturum açar. `command` boşsa etkileşimli kabuk, doluysa o komut (bitince oturum biter).
    Olay döngüsü içinden çağrılmalı."""
    _prune()
    if sum(1 for s in SESSIONS.values() if s.alive) >= MAX_SESSIONS:
        raise RuntimeError(f"En fazla {MAX_SESSIONS} terminal oturumu açık olabilir; birini kapatın.")
    command = (command or "").strip()
    if "\n" in command:
        raise ValueError("Komut tek satır olmalı.")
    argv = command_argv(command) if command else default_shell()
    try:
        s = Session(
            title=(title or command or Path(argv[0]).stem).strip()[:60], argv=argv, cwd=_resolve_cwd(cwd),
            cols=max(20, min(int(cols), 500)), rows=max(5, min(int(rows), 200)),
            command=command, saved_id=saved_id, source=source,
        )
    except (ValueError, RuntimeError):
        raise
    except Exception as e:  # noqa: BLE001 - pty açılamadı (ör. pywinpty eksik)
        raise RuntimeError(f"Terminal açılamadı: {type(e).__name__}: {e}") from None
    SESSIONS[s.id] = s
    return s


def close_session(session_id: str) -> bool:
    s = SESSIONS.pop(session_id, None)
    if s is None:
        return False
    s.kill()
    return True


def kill_all(source: str | None = None) -> int:
    """Çalışan oturumları kapatır (`source` verilirse yalnızca o kaynaktan açılanları). Kapatılan sayısı."""
    n = 0
    for s in list(SESSIONS.values()):
        if s.alive and (source is None or s.source == source):
            s.kill()
            n += 1
    return n


def _prune() -> None:
    """Biteli 1 saatten uzun olan, kimsenin izlemediği oturumları listeden çıkarır."""
    now = datetime.now()
    for sid, s in list(SESSIONS.items()):
        if not s.alive and s.ended and not s.watchers and (now - datetime.fromisoformat(s.ended)).total_seconds() > 3600:
            SESSIONS.pop(sid, None)


# ---------------------------------------------------------------- kalıcı komutlar

def load_saved() -> list[dict]:
    with _lock:
        if FILE.exists():
            try:
                return json.loads(FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return []
        return []


def _save_saved(items: list[dict]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(FILE)


def add_saved(command: str, title: str = "", cwd: str = "", autostart: bool = True) -> dict:
    command = (command or "").strip()
    if not command or "\n" in command:
        raise ValueError("Tek satırlık bir komut gerekli.")
    if cwd:
        _resolve_cwd(cwd)
    item = {
        "id": uuid.uuid4().hex[:8], "title": (title or command).strip()[:60], "command": command,
        "cwd": cwd.strip(), "autostart": bool(autostart), "created": datetime.now().isoformat(timespec="seconds"),
    }
    with _lock:
        items = load_saved()
        items.append(item)
        _save_saved(items)
    return item


def update_saved(saved_id: str, **changes) -> dict | None:
    allowed = {k: v for k, v in changes.items() if k in ("title", "command", "cwd", "autostart") and v is not None}
    if "command" in allowed and (not str(allowed["command"]).strip() or "\n" in str(allowed["command"])):
        raise ValueError("Tek satırlık bir komut gerekli.")
    if allowed.get("cwd"):
        _resolve_cwd(allowed["cwd"])
    with _lock:
        items = load_saved()
        for x in items:
            if x["id"] == saved_id:
                x.update(allowed)
                _save_saved(items)
                KEEPER.reset(saved_id)
                return x
    return None


def remove_saved(saved_id: str) -> bool:
    with _lock:
        items = load_saved()
        kept = [x for x in items if x["id"] != saved_id]
        if len(kept) == len(items):
            return False
        _save_saved(kept)
    KEEPER.reset(saved_id)
    return True


def session_for(saved_id: str) -> Session | None:
    """Kalıcı komutun en son açılmış oturumu."""
    found = [s for s in SESSIONS.values() if s.saved_id == saved_id]
    return found[-1] if found else None


def start_saved(saved_id: str) -> Session:
    item = next((x for x in load_saved() if x["id"] == saved_id), None)
    if item is None:
        raise LookupError("Kalıcı komut bulunamadı.")
    current = session_for(saved_id)
    if current and current.alive:
        return current
    return open_session(item["command"], item["title"], item.get("cwd", ""), saved_id=saved_id, source="kalıcı")


class Keeper:
    """Kalıcı komutları açık tutar: ADA açılınca başlatır, kapanırsa yeniden başlatır."""

    def __init__(self) -> None:
        self.restarts: dict[str, list[float]] = {}  # saved_id -> başlatma zamanları (monotonic)
        self.gave_up: dict[str, str] = {}  # saved_id -> neden

    def reset(self, saved_id: str) -> None:
        self.restarts.pop(saved_id, None)
        self.gave_up.pop(saved_id, None)

    def status(self) -> list[dict]:
        out = []
        for item in load_saved():
            s = session_for(item["id"])
            out.append({
                **item,
                "session_id": s.id if s else None,
                "alive": bool(s and s.alive),
                "exit_code": s.exit_code if s else None,
                "gave_up": self.gave_up.get(item["id"]),
                "restarts_last_hour": len([t for t in self.restarts.get(item["id"], []) if time.monotonic() - t < 3600]),
            })
        return out

    async def loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001
                print(f"[terminal] hata: {type(e).__name__}: {e}")
            await asyncio.sleep(KEEPER_TICK_SECONDS)

    async def tick(self) -> None:
        from . import runtime, safety

        if safety.is_halted():
            return
        now = time.monotonic()
        for item in load_saved():
            sid = item["id"]
            if not item.get("autostart") or sid in self.gave_up:
                continue
            s = session_for(sid)
            if s and s.alive:
                continue
            if s and s.ended and (datetime.now() - datetime.fromisoformat(s.ended)).total_seconds() < RESTART_DELAY_SECONDS:
                continue
            recent = [t for t in self.restarts.get(sid, []) if now - t < 3600]
            if len(recent) >= MAX_RESTARTS_PER_HOUR:
                self.gave_up[sid] = f"Son bir saatte {len(recent)} kez kapandı; yeniden başlatma durduruldu."
                runtime.notify(f"ADA: '{item['title']}' sürekli kapanıyor", "Terminal panelinden çıktısına bakın.")
                continue
            try:
                start_saved(sid)
            except Exception as e:  # noqa: BLE001
                self.gave_up[sid] = f"Başlatılamadı: {e}"
                runtime.notify(f"ADA: '{item['title']}' başlatılamadı", str(e)[:160])
                continue
            self.restarts[sid] = [*recent, now]


KEEPER = Keeper()
