"""Bilgisayar üzerinde tam yetki: kabuk komutları, dosya sistemi, uygulama açma, AppleScript."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from contextvars import ContextVar
from pathlib import Path

from .. import config, safety, tor
from . import ToolError, ToolResult, downloadable, guard_write, resolve_path, tool

HOME = Path.home()

# Windows'ta komuttan önce konsol çıktısını UTF-8'e sabitler (Türkçe çıktı/dosya adları için)
_PS_UTF8_PREAMBLE = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$OutputEncoding=[System.Text.Encoding]::UTF8;"
)

# Tehlikeli komutlar (diski/sistemi yok edenler, bilgisayarı kapatanlar). Güvenli modda hiçbir koşulda
# çalıştırılmaz; "onayla her şey" modunda kullanıcı her seferinde onaylarsa çalıştırılır (bkz. config.safety_mode).
HARD_BLOCK = [
    # rm -r / rm -rf ile kök, ev dizini veya kullanıcı klasörünün tamamı
    r"\brm\s+(-\S+\s+)*(/|~|~/|\$HOME/?|/\*|~/\*|/Users/?|/Users/[^/\s]+/?)(\s|;|&|\||$)",
    r"\bmkfs\b",
    r"\bdd\b.*\bof=/dev/",
    r"\bdiskutil\s+(erase|partition|zero|secureErase|reformat)",
    r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;",
    r"\b(shutdown|reboot|halt)\b",
    r"\bcsrutil\b",
    r">\s*/dev/(disk|rdisk)",
    # Windows: kapatma/yeniden başlatma, disk biçimlendirme, sürücü kökünü silme, önyükleme ayarları
    r"\b(Stop-Computer|Restart-Computer)\b",
    r"\b(Format-Volume|Clear-Disk|Initialize-Disk|Remove-Partition)\b",
    r"\bdiskpart\b",
    r"\bbcdedit\b",
    r"(^|[\s;&|(])format(\.com)?\s+[a-z]:",
    r"\b(Remove-Item|rm|del|erase|rd|rmdir|ri)\b[^|;&]*\s['\"]?[a-z]:[\\/]*\*?['\"]?(\s|$)",
]

DANGER_BLOCKED = (
    "Bu komut bilgisayarı kapatabilir ya da sistemi/diski yok edebilir; güvenli modda ADA bunu çalıştırmaz. "
    "Kullanıcı Ayarlar → Genel → Güvenlik modu'ndan 'Onayla her şey'i seçerse onayıyla çalıştırılabilir; "
    "bunu kullanıcıya söyle, başka bir yolla aynı işi yapmaya çalışma."
)
# Kullanıcı bu çağrı için tehlikeli komutu onayladı mı? (agent._run_tool_inner ayarlar; araç iş parçacığına kopyalanır)
DANGER_APPROVED: ContextVar[bool] = ContextVar("DANGER_APPROVED", default=False)


def danger(command: str) -> bool:
    """Komut tehlikeli kalıplardan birine uyuyor mu?"""
    return any(re.search(pattern, command or "", re.IGNORECASE) for pattern in HARD_BLOCK)


def check_danger(command: str) -> None:
    """Tehlikeli komut onaylanmadıysa ToolError fırlatır (run_shell ve terminal araçları çağırır)."""
    if danger(command) and not DANGER_APPROVED.get():
        raise ToolError(DANGER_BLOCKED)

# Onay gerektiren kalıplar (smart modunda)
RISKY = [
    (r"\bsudo\b", "yönetici (sudo) yetkisi"),
    (r"\brm\s+-[a-zA-Z]*r", "özyinelemeli silme (rm -r)"),
    (r"\brm\s+.*\*", "joker karakterle silme"),
    (r"\b(kill|killall|pkill)\b", "süreç sonlandırma"),
    (r"git\s+push\b.*(--force|-f\b)", "git zorla push"),
    (r"git\s+(reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s*\.)", "git ile yerel değişiklikleri silme"),
    (r"\bchmod\b.*\s-R|\bchown\b", "toplu izin değişikliği"),
    (r"\b(launchctl|crontab|defaults\s+write|systemsetup|scutil|networksetup|pmset)\b", "sistem ayarı değişikliği"),
    (r"\bcurl\b.*\|\s*(ba|z)?sh\b|\bwget\b.*\|\s*(ba|z)?sh\b", "internetten indirilen betiği çalıştırma"),
    (r"\b(brew|pip|pip3|npm|uv)\s+(uninstall|remove|rm)\b", "paket kaldırma"),
    (r"\b(security|keychain)\b", "anahtar zinciri erişimi"),
    (r">\s*(/etc|/System|/Library|/usr)/", "sistem dizinine yazma"),
    (r"\b(osascript|open)\b.*\b(quit|delete|empty)\b", "uygulama kapatma/silme"),
    (r"\bmv\b.*\s(/|~)/?(\s|$)", "kök/ev dizinini taşıma"),
    (r"(~|\$HOME|/Users/[^/\s]+)/(Library|\.ssh|\.gnupg|\.aws|\.kube)\b", "kişisel/gizli klasörlere erişim"),
    (r"\b(find|du|grep\s+-r\w*|ls\s+-\w*R)\s+(/|~|\$HOME)/?(\s|$)", "tüm diski/ev dizinini tarama (yerine mdfind kullanılmalı)"),
]

_PROTECTED_DIRS = ("/System", "/Library", "/usr", "/bin", "/sbin", "/etc", "/private", "/Applications")

# Kişisel/gizli veri barındıran yerler: okumak bile onay ister (smart modda)
_SENSITIVE_PARTS = ("/Library/", "/.ssh", "/.gnupg", "/.aws", "/.config/gcloud", "/.kube", "/Keychains", "/Cookies", "/AppData/")


def sensitive_path(p: str) -> str | None:
    """Yol hassas bir yerdeyse gerekçe döner (ADA'nın kendi çalışma alanı hariç)."""
    try:
        path = resolve_path(p)
    except ToolError:
        return None
    if path.is_relative_to(config.WORKSPACE):
        return None
    # Windows'ta da "/" ayracıyla karşılaştırılır
    text = path.as_posix() + ("/" if path.is_dir() else "")
    if path.is_relative_to(HOME) and any(part in text for part in _SENSITIVE_PARTS):
        return f"Kişisel/gizli veri içerebilecek bir klasöre erişim: {path}"
    return None


def _read_risk(key: str):
    return lambda args: sensitive_path(args.get(key, "")) if isinstance(args, dict) else None


def _shell_risk(args: dict) -> str | None:
    cmd = args.get("command", "") if isinstance(args, dict) else ""
    for pattern, why in RISKY:
        if re.search(pattern, cmd, re.IGNORECASE):
            return f"Komut {why} içeriyor."
    return None


def _path_risk(key: str, what: str):
    def check(args: dict) -> str | None:
        p = args.get(key, "") if isinstance(args, dict) else ""
        try:
            path = resolve_path(p)
        except ToolError:
            return None
        if any(str(path).startswith(d) for d in _PROTECTED_DIRS):
            return f"Sistem dizininde {what}: {path}"
        if path in (HOME, HOME / "Desktop", HOME / "Documents", HOME / "Downloads", Path("/")):
            return f"Önemli bir klasörün kendisinde {what}: {path}"
        return None

    return check


_SHELL_DESC_WIN = (
    "Bilgisayarda Windows PowerShell 5.1 komutu çalıştırır (bash DEĞİL). Linux komutları yerine PowerShell kullan: "
    "ls -la → Get-ChildItem -Force; tail -n 50 f → Get-Content f -Tail 50; grep x f → Select-String x f; "
    "cat → Get-Content; rm -r → Remove-Item -Recurse; which → Get-Command; ps → Get-Process; "
    "dosya arama → Get-ChildItem -Path $HOME -Recurse -Filter '*ad*' -ErrorAction SilentlyContinue; "
    "uygulama/dosya açma → Start-Process; pano → Get-Clipboard/Set-Clipboard; komut zinciri '&&' yerine ';'. "
    "Kullanıcının tam yetkisiyle çalışır. Uzun süren komutlar için timeout verin; arka plan işleri için "
    "Start-Process -WindowStyle Hidden kullanın. (Linux tarzı bir komut PowerShell'de tanınmazsa Git Bash ile "
    "otomatik yeniden denenir.)"
)
_SHELL_DESC_UNIX = (
    "Bilgisayarda kabuk komutu çalıştırır (macOS/Linux: zsh/bash). Dosya aramak için "
    "`mdfind -name <ad>` veya `mdfind -onlyin ~/Documents <sorgu>` kullan (anlık Spotlight); tüm diski "
    "`find /` ile tarama. Diğer kullanımlar: program kurma, dosya işlemleri, git, "
    "uygulama başlatma (open -a), sistem bilgisi, clipboard (pbcopy/pbpaste) vb. "
    "Kullanıcının tam yetkisiyle çalışır. Uzun süren komutlar için timeout verin; "
    "arka planda çalışması gerekenleri 'nohup ... &' ile başlatın."
)
# PowerShell'in "Linux komutu yazdın" anlamına gelen hataları
_PS_UNIX_ERRORS = re.compile(r"is not recognized as the name of a cmdlet|A parameter cannot be found that matches parameter name|"
                             r"tanınmıyor|parametre adıyla eşleşen bir parametre bulunamıyor|The token '&&' is not a valid", re.I)
_UNIX_HINT = re.compile(r"(^|[;|&\s])(ls\s+-|tail\b|head\b|grep\b|sed\b|awk\b|find\s+[./~]|wc\b|touch\b|which\b|chmod\b|"
                        r"cat\s+.*\|\s*\w|rm\s+-|cp\s+-|mkdir\s+-p|export\s|du\s+-|df\s+-)|&&", re.I)


def _git_bash() -> str | None:
    """Git for Windows'un bash'i (WSL'in System32\\bash.exe'si değil)."""
    for base in (os.getenv("ProgramFiles", r"C:\Program Files"), os.getenv("ProgramFiles(x86)", ""), os.getenv("LOCALAPPDATA", "")):
        for rel in (r"Git\bin\bash.exe", r"Programs\Git\bin\bash.exe"):
            p = Path(base) / rel if base else None
            if p and p.is_file():
                return str(p)
    return None


if config.ENABLE_SHELL:

    @tool(
        "run_shell",
        _SHELL_DESC_WIN if sys.platform.startswith("win") else _SHELL_DESC_UNIX,
        {
            "command": {"type": "string"},
            "cwd": {"type": "string", "description": "Çalışma dizini (varsayılan: kullanıcının ev dizini)"},
            "timeout": {"type": "integer", "description": "Saniye (varsayılan 300)"},
        },
        ["command"],
        risk=_shell_risk,
    )
    def run_shell(command: str, cwd: str = "", timeout: int = 0) -> ToolResult:
        check_danger(command)
        workdir = resolve_path(cwd, must_exist=True) if cwd else HOME
        encoding = None
        if sys.platform.startswith("win"):
            # Çıktıyı UTF-8 olarak al: Türkçe karakterler (çıktıda ve dosya adlarında) bozulmasın.
            # Konsol kod sayfası cp1254 olsa bile hem PowerShell'in hem de yerel programların
            # çıktısı UTF-8 kodlanır; biz de utf-8 çözeriz.
            argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_UTF8_PREAMBLE + command]
            encoding = "utf-8"
        else:
            shell = "/bin/zsh" if Path("/bin/zsh").exists() else "/bin/bash"
            argv = [shell, "-lc", command]
        try:
            proc = subprocess.run(
                argv,
                cwd=workdir,
                capture_output=True,
                text=True,
                encoding=encoding,
                errors="replace",
                timeout=timeout or config.SHELL_TIMEOUT,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired:
            return ToolResult(f"Zaman aşımı ({timeout or config.SHELL_TIMEOUT}s). Uzun işler için nohup/& kullanın.", is_error=True)
        note = ""
        if (sys.platform.startswith("win") and proc.returncode != 0 and _PS_UNIX_ERRORS.search(proc.stderr or "")
                and _UNIX_HINT.search(command)):
            bash = _git_bash()
            if bash:
                # Model Linux komutu yazmış: aynı komutu Git Bash'te çalıştır (kendi kendini düzeltme)
                try:
                    proc = subprocess.run([bash, "-lc", command], cwd=workdir, capture_output=True, text=True, encoding="utf-8",
                                          errors="replace", timeout=timeout or config.SHELL_TIMEOUT, env=os.environ.copy())
                    note = "[Not: komut PowerShell'de tanınmadı (Linux sözdizimi); ADA onu Git Bash ile çalıştırdı. Bundan sonra PowerShell komutları kullan.]\n"
                except subprocess.TimeoutExpired:
                    return ToolResult(f"Zaman aşımı ({timeout or config.SHELL_TIMEOUT}s).", is_error=True)
        out = note + f"çıkış kodu: {proc.returncode}\n"
        if proc.stdout:
            out += f"--- stdout ---\n{_clip(proc.stdout)}\n"
        if proc.stderr:
            out += f"--- stderr ---\n{_clip(proc.stderr)}\n"
        return ToolResult(out, is_error=proc.returncode != 0)

    @tool(
        "read_file",
        "Bilgisayardaki herhangi bir metin dosyasını okur (kod, config, log, txt...). "
        "Ofis belgeleri (pdf/docx/xlsx/pptx) için read_document kullanın.",
        {
            "path": {"type": "string", "description": "Mutlak yol, ~ veya workspace'e göre göreli yol"},
            "offset": {"type": "integer", "description": "Başlangıç satırı (1'den başlar)"},
            "limit": {"type": "integer", "description": "Kaç satır (varsayılan 400)"},
        },
        ["path"],
        risk=_read_risk("path"),
    )
    def read_file(path: str, offset: int = 1, limit: int = 400) -> str:
        p = resolve_path(path, must_exist=True)
        if p.is_dir():
            raise ToolError(f"Bu bir klasör: {p}. list_directory kullanın.")
        if p.stat().st_size > 20_000_000:
            raise ToolError("Dosya 20 MB'tan büyük; run_shell ile head/grep kullanın.")
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as e:
            raise ToolError(f"Okunamadı: {e}")
        start = max(offset, 1) - 1
        chunk = lines[start : start + max(limit, 1)]
        numbered = "\n".join(f"{start + i + 1:5d}| {ln}" for i, ln in enumerate(chunk))
        footer = f"\n\n[toplam {len(lines)} satır]" + (
            f" — devamı için offset={start + len(chunk) + 1}" if start + len(chunk) < len(lines) else ""
        )
        return (numbered or "(boş dosya)") + footer

    @tool(
        "write_file",
        "Bilgisayarda herhangi bir yola metin dosyası yazar (yoksa oluşturur, varsa üzerine yazar; "
        "klasörleri gerekirse oluşturur). Var olan dosyayı değiştirmeden önce okuyun.",
        {"path": {"type": "string"}, "content": {"type": "string"}},
        ["path", "content"],
        risk=_path_risk("path", "dosya yazma"),
    )
    def write_file(path: str, content: str) -> ToolResult:
        p = guard_write(resolve_path(path))
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        bid = safety.backup(p) if existed else None
        p.write_text(content, encoding="utf-8")
        note = f" — eski hali yedeklendi ({bid})" if bid else ""
        return ToolResult(f"{'Güncellendi' if existed else 'Oluşturuldu'}: {p} ({len(content):,} karakter){note}", files=downloadable([p]))

    @tool(
        "edit_file",
        "Metin dosyasında tam eşleşen bir parçayı değiştirir. old_text dosyada tam olarak "
        "bir kez geçmeli (girintiler dahil). Tümünü değiştirmek için replace_all=true.",
        {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
            "replace_all": {"type": "boolean"},
        },
        ["path", "old_text", "new_text"],
        risk=_path_risk("path", "dosya düzenleme"),
    )
    def edit_file(path: str, old_text: str, new_text: str, replace_all: bool = False) -> ToolResult:
        p = guard_write(resolve_path(path, must_exist=True))
        text = p.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count == 0:
            raise ToolError("old_text dosyada bulunamadı. Dosyayı okuyup tam metni kopyalayın.")
        if count > 1 and not replace_all:
            raise ToolError(f"old_text {count} kez geçiyor; daha uzun/benzersiz bir parça verin veya replace_all=true.")
        bid = safety.backup(p)
        p.write_text(text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1), encoding="utf-8")
        note = f" — eski hali yedeklendi ({bid})" if bid else ""
        return ToolResult(f"Düzenlendi: {p} ({count if replace_all else 1} değişiklik){note}", files=downloadable([p]))

    @tool(
        "list_directory",
        "Bilgisayardaki bir klasörün içeriğini listeler (ad, tür, boyut, değiştirilme tarihi).",
        {
            "path": {"type": "string", "description": "Mutlak yol veya ~ (varsayılan: ev dizini)"},
            "recursive": {"type": "boolean", "description": "Alt klasörleri de listele (en fazla 500 kayıt)"},
        },
        risk=_read_risk("path"),
    )
    def list_directory(path: str = "~", recursive: bool = False) -> str:
        p = resolve_path(path, must_exist=True)
        if not p.is_dir():
            raise ToolError(f"Klasör değil: {p}")
        from datetime import datetime

        entries = sorted(p.rglob("*") if recursive else p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        rows = []
        for e in entries:
            if e.name.startswith(".") and not recursive:
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            kind = "klasör" if e.is_dir() else "dosya"
            size = f"{st.st_size:,}" if e.is_file() else ""
            when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            rows.append(f"{kind:6} {size:>12} {when}  {e.relative_to(p) if recursive else e.name}")
            if len(rows) >= 500:
                rows.append("... (kısaltıldı)")
                break
        return f"{p}\n" + ("\n".join(rows) or "(boş)")

    @tool(
        "delete_path",
        "Dosya veya klasörü Çöp Sepeti'ne taşır (geri alınabilir). Kalıcı silme için run_shell ile rm kullanılır.",
        {"path": {"type": "string"}},
        ["path"],
        risk=lambda a: f"Silinecek: {a.get('path')}",
    )
    def delete_path(path: str) -> str:
        p = guard_write(resolve_path(path, must_exist=True))
        if p in (HOME, Path("/")) or any(str(p).startswith(d) for d in _PROTECTED_DIRS):
            raise ToolError("Bu yol korunuyor.")
        if config.IS_MAC:
            run_osascript(f'tell application "Finder" to delete POSIX file "{_q(str(p))}"')
            return f"Çöp Sepeti'ne taşındı: {p}"
        if config.IS_WIN:
            _recycle_windows(p)
            return f"Geri Dönüşüm Kutusu'na taşındı: {p}"
        trash = HOME / ".local/share/Trash/files"
        trash.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), trash / p.name)
        return f"Çöp'e taşındı: {p}"

    @tool(
        "open_item",
        "Uygulama, dosya veya URL açar (macOS 'open'). Siteler Tor ayarı açıksa Tor üzerinden açılır. Örn: app='Safari', path='~/Desktop/rapor.docx', "
        "url='https://...'. Dosyayı belirli uygulamayla açmak için path+app birlikte verilebilir.",
        {"app": {"type": "string"}, "path": {"type": "string"}, "url": {"type": "string"}},
    )
    def open_item(app: str = "", path: str = "", url: str = "") -> str:
        if not (app or path or url):
            raise ToolError("app, path veya url gerekli.")
        if url and not path and url.lower().startswith(("http://", "https://")) and tor.enabled():
            try:  # siteler kullanıcının bağlantısından değil Tor'dan açılır (Ayarlar'dan kapatılabilir)
                return tor.open_url(url)
            except tor.TorError as e:
                raise ToolError(str(e))
        if not config.IS_MAC:
            target = url or str(resolve_path(path, must_exist=True))
            if sys.platform.startswith("win"):
                os.startfile(target)  # noqa: S606 - Windows'ta varsayılan uygulamayla açar
            else:
                subprocess.Popen(["xdg-open", target])
            return f"Açıldı: {target}"
        cmd = ["open"]
        if app:
            cmd += ["-a", app]
        if path:
            cmd.append(str(resolve_path(path, must_exist=True)))
        elif url:
            cmd.append(url)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise ToolError(proc.stderr.strip() or "Açılamadı")
        return "Açıldı: " + " ".join(cmd[1:])

    if config.IS_MAC:

        @tool(
            "run_applescript",
            "macOS'ta AppleScript çalıştırır: uygulamaları yönetme (Safari, Finder, Notes, "
            "Reminders, Müzik, Sistem Ayarları, Mail, Calendar...), pencere/menü kontrolü, "
            "bildirim gösterme (display notification), sistem olayları. Sonucu döndürür.",
            {"script": {"type": "string"}, "timeout": {"type": "integer"}},
            ["script"],
            risk=lambda a: (
                "Betik silme/kapatma/gönderme içeriyor."
                if re.search(r"\b(delete|empty the trash|send|quit|shut down|restart|log out|erase)\b", a.get("script", ""), re.I)
                else None
            ),
        )
        def run_applescript(script: str, timeout: int = 120) -> str:
            return run_osascript(script, timeout=timeout) or "(çıktı yok)"

        @tool(
            "speak",
            "Metni Mac'in hoparlöründen Türkçe sesle okur (kullanıcı ekrana bakmıyorken kısa bir haber vermek için; "
            "uzun metinleri okuma, 1-3 cümle yeterli).",
            {"text": {"type": "string"}, "voice": {"type": "string", "description": "Varsayılan: Yelda (Türkçe)"}},
            ["text"],
        )
        def speak(text: str, voice: str = "Yelda") -> str:
            text = text.strip()[:600]
            if not text:
                raise ToolError("Okunacak metin boş.")
            proc = subprocess.run(["say", "-v", voice, text], capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                subprocess.run(["say", text], timeout=120)  # ses yoksa varsayılan sesle
            return "Sesli okundu."

        @tool(
            "notify",
            "macOS bildirimi gösterir (kullanıcıya bir işin bittiğini haber vermek için).",
            {"title": {"type": "string"}, "message": {"type": "string"}},
            ["title", "message"],
        )
        def notify(title: str, message: str) -> str:
            run_osascript(f'display notification "{_q(message)}" with title "{_q(title)}"')
            return "Bildirim gösterildi."

    elif config.IS_WIN:

        @tool(
            "notify",
            "Windows masaüstü bildirimi gösterir (kullanıcıya bir işin bittiğini haber vermek için).",
            {"title": {"type": "string"}, "message": {"type": "string"}},
            ["title", "message"],
        )
        def notify(title: str, message: str) -> str:
            from .. import notify as _notify

            _notify.notify(title, message)
            return "Bildirim gösterildi."


def _recycle_windows(path: Path) -> None:
    """Dosya/klasörü gerçek Geri Dönüşüm Kutusu'na taşır (SHFileOperationW, geri alınabilir)."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 0x0003
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040
    FOF_NOERRORUI = 0x0400
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    # Kaynak liste çift null ile sonlandırılır (ctypes zaten bir null ekler)
    op.pFrom = str(path) + "\0"
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0:
        raise ToolError(f"Geri Dönüşüm Kutusu'na taşınamadı (SHFileOperation kodu {rc}).")


def _clip(text: str, limit: int = 30_000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 2] + f"\n[... {len(text) - limit:,} karakter atlandı ...]\n" + text[-limit // 2 :]


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _target_app(script: str) -> str:
    m = re.search(r'application "([^"]+)"', script)
    return m.group(1) if m else "uygulama"


def run_osascript(script: str, timeout: int = 120) -> str:
    try:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolError(f"AppleScript {timeout}s içinde bitmedi.")
    if proc.returncode != 0:
        err = proc.stderr.strip()
        app = _target_app(script)
        if "-1743" in err:
            # Otomasyon izni reddedilmiş: model tekrar denemesin, kullanıcıya ne yapacağını söylesin
            raise ToolError(
                f"ADA'nın {app} uygulamasını kullanma izni yok. Kullanıcıya söyle: Sistem Ayarları → Gizlilik ve Güvenlik → "
                f"Otomasyon → ADA altında '{app}' seçeneğini açsın (veya ADA Ayarlar'daki 'macOS izinlerini iste' düğmesini kullansın). "
                f"Bu arada aynı işlemi başka yoldan tekrar deneme. (-1743)"
            )
        if "-1712" in err:
            raise ToolError(
                f"{app} yanıt vermedi (-1712). Büyük olasılıkla ekranda macOS izin penceresi kullanıcının onayını bekliyor "
                f"ya da {app} meşgul. Kullanıcıya izin penceresini onaylamasını söyle; bir süre sonra bir kez daha dene."
            )
        raise ToolError(f"AppleScript hatası: {err}")
    return proc.stdout.strip()
