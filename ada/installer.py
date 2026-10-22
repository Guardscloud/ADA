"""Kurulum merkezi: sohbetteki `/d` komutunun arka ucu.

  /d            → program kurma penceresi (hazır katalog + winget araması), seçilenler sırayla sessiz kurulur
  /d <ad>       → pencere o adla aranmış olarak açılır
  /d update     → her şey güncellenir: winget ile güncellemesi olan tüm programlar, sonra ADA'nın kendisi

Paket yöneticisi Windows'ta winget'tir (Windows 10/11'de "Uygulama Yükleyici" ile gelir). Kurulumlar
kullanıcının yetkisiyle, pencere açmadan çalışır; yönetici izni gerektiren programlarda Windows'un kendi
UAC sorusu çıkar. Aynı anda tek iş çalışır (winget eşzamanlı kurulumda kilitlenebilir). Her iş adım adım
kaydedilir; biten her paket için sonuç, varsa hata açıklaması ve "bundan sonra ne yapmalı" yönlendirmesi
arayüze döner. Kurulan/güncellenen her paket güvenlik kaydına (audit) yazılır.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import config, safety

WINGET_STORE_URL = "ms-windows-store://pdp/?productid=9NBLGGH4NNS1"  # "Uygulama Yükleyici" (winget)
WINGET_WEB_URL = "https://learn.microsoft.com/windows/package-manager/winget/"
COMMON = ["--accept-source-agreements", "--disable-interactivity"]
PKG_FLAGS = ["--source", "winget", "--accept-package-agreements", "--silent", *COMMON]
SEARCH_TIMEOUT = 90
INSTALL_TIMEOUT = 45 * 60  # büyük paketler (Visual Studio, Docker) uzun sürebilir
MAX_LOG = 400
_ID_RE = re.compile(r"^[\w][\w.+\-]{1,120}$")

# ---------------------------------------------------------------- hazır katalog
# cat: kategori · note: kurulumdan sonra kullanıcıya gösterilecek yönlendirme
CATALOG: list[dict] = [
    {"id": "Google.Chrome", "name": "Google Chrome", "cat": "Tarayıcı"},
    {"id": "Mozilla.Firefox", "name": "Firefox", "cat": "Tarayıcı"},
    {"id": "Brave.Brave", "name": "Brave", "cat": "Tarayıcı"},
    {"id": "Microsoft.VisualStudioCode", "name": "Visual Studio Code", "cat": "Geliştirici",
     "note": "Yeni açılan terminallerde 'code' komutu çalışır."},
    {"id": "Git.Git", "name": "Git", "cat": "Geliştirici",
     "note": "'git' komutu yeni açılan terminallerde çalışır; ilk iş: git config --global user.name \"Adınız\"."},
    {"id": "GitHub.cli", "name": "GitHub CLI", "cat": "Geliştirici",
     "note": "Hesabı bağlamak için terminalde: gh auth login"},
    {"id": "Python.Python.3.12", "name": "Python 3.12", "cat": "Geliştirici",
     "note": "PATH güncellendi: 'python' ve 'pip' için yeni bir terminal açın."},
    {"id": "OpenJS.NodeJS.LTS", "name": "Node.js LTS", "cat": "Geliştirici",
     "note": "'node' ve 'npm' için yeni bir terminal açın."},
    {"id": "Docker.DockerDesktop", "name": "Docker Desktop", "cat": "Geliştirici",
     "note": "WSL 2 gerekir; kurulumdan sonra bilgisayarı yeniden başlatın ve Docker Desktop'ı bir kez açın."},
    {"id": "Microsoft.WindowsTerminal", "name": "Windows Terminal", "cat": "Geliştirici"},
    {"id": "Microsoft.PowerShell", "name": "PowerShell 7", "cat": "Geliştirici",
     "note": "Yeni kabuk 'pwsh' adıyla açılır; Windows PowerShell 5.1 yerinde kalır."},
    {"id": "Ollama.Ollama", "name": "Ollama", "cat": "Yapay zekâ",
     "note": "Model indirin (ör. terminalde: ollama pull qwen3:8b), sonra ADA → Ayarlar → Yapay zekâ → Tara."},
    {"id": "ElementLabs.LMStudio", "name": "LM Studio", "cat": "Yapay zekâ",
     "note": "LM Studio'da bir model indirip yerel sunucuyu başlatın; ADA onu Ayarlar → Yapay zekâ → Tara ile bulur."},
    {"id": "Discord.Discord", "name": "Discord", "cat": "İletişim"},
    {"id": "Zoom.Zoom", "name": "Zoom", "cat": "İletişim"},
    {"id": "SlackTechnologies.Slack", "name": "Slack", "cat": "İletişim"},
    {"id": "Telegram.TelegramDesktop", "name": "Telegram", "cat": "İletişim"},
    {"id": "VideoLAN.VLC", "name": "VLC", "cat": "Medya"},
    {"id": "Spotify.Spotify", "name": "Spotify", "cat": "Medya",
     "note": "Spotify yönetici olarak kurulamaz; hata alırsanız ADA'yı yönetici olmadan açıp tekrar deneyin."},
    {"id": "OBSProject.OBSStudio", "name": "OBS Studio", "cat": "Medya"},
    {"id": "Audacity.Audacity", "name": "Audacity", "cat": "Medya"},
    {"id": "Valve.Steam", "name": "Steam", "cat": "Medya"},
    {"id": "7zip.7zip", "name": "7-Zip", "cat": "Araçlar"},
    {"id": "Notepad++.Notepad++", "name": "Notepad++", "cat": "Araçlar"},
    {"id": "Microsoft.PowerToys", "name": "PowerToys", "cat": "Araçlar"},
    {"id": "voidtools.Everything", "name": "Everything (dosya arama)", "cat": "Araçlar"},
    {"id": "ShareX.ShareX", "name": "ShareX", "cat": "Araçlar"},
    {"id": "TheDocumentFoundation.LibreOffice", "name": "LibreOffice", "cat": "Ofis"},
    {"id": "Adobe.Acrobat.Reader.64-bit", "name": "Adobe Acrobat Reader", "cat": "Ofis"},
    {"id": "Notion.Notion", "name": "Notion", "cat": "Ofis"},
]
_NOTES = {c["id"].lower(): c.get("note", "") for c in CATALOG}

# winget çıkış kodları (HRESULT, 0x8A15xxxx) → (durum, açıklama). durum: ok | current | reboot | fail
EXIT_CODES: dict[int, tuple[str, str]] = {
    0x8A15002B: ("current", "Zaten kurulu ve güncel."),
    0x8A15010D: ("current", "Zaten kurulu."),
    0x8A150109: ("reboot", "Kuruldu; tamamlanması için bilgisayarı yeniden başlatın."),
    0x8A15010A: ("fail", "Kurulumdan önce bilgisayarın yeniden başlatılması gerekiyor."),
    0x8A15010B: ("reboot", "Kurulum bilgisayarı yeniden başlatıyor."),
    0x8A150014: ("fail", "Bu kimlikle paket bulunamadı. Adı arama kutusundan arayıp listeden seçin."),
    0x8A150101: ("fail", "Program şu an açık. Kapatıp tekrar deneyin."),
    0x8A150102: ("fail", "Başka bir kurulum sürüyor. Bitince tekrar deneyin."),
    0x8A150103: ("fail", "Programın bir dosyası kullanımda. İlgili programı kapatıp tekrar deneyin."),
    0x8A150104: ("fail", "Gerekli bir bileşen eksik."),
    0x8A150105: ("fail", "Diskte yer yok. Yer açıp tekrar deneyin."),
    0x8A150106: ("fail", "Bellek yetersiz. Bazı programları kapatıp tekrar deneyin."),
    0x8A150107: ("fail", "İnternet bağlantısı yok."),
    0x8A150108: ("fail", "Kurulum hata verdi; programın kendi sitesinden kurmayı deneyin."),
    0x8A15010C: ("fail", "Kurulum iptal edildi (Windows'un izin sorusuna 'Hayır' denmiş olabilir)."),
    0x8A15010E: ("current", "Daha yeni bir sürüm zaten kurulu."),
    0x8A15010F: ("fail", "Kurumsal ilke bu kurulumu engelliyor."),
    1223: ("fail", "Windows'un izin sorusuna 'Hayır' dendi; tekrar deneyip 'Evet'e basın."),
    1602: ("fail", "Kurulum kullanıcı tarafından iptal edildi."),
    3010: ("reboot", "Kuruldu; tamamlanması için bilgisayarı yeniden başlatın."),
    1641: ("reboot", "Kuruldu; kurulum yeniden başlatma başlattı."),
}
STATE_LABEL = {"ok": "Kuruldu", "updated": "Güncellendi", "current": "Zaten güncel", "reboot": "Yeniden başlatma gerekli",
               "fail": "Başarısız", "skipped": "Atlandı"}


class InstallerError(Exception):
    """Kullanıcıya gösterilebilir hata."""


# ---------------------------------------------------------------- winget

def winget_path() -> str | None:
    if not config.IS_WIN:
        return None
    found = shutil.which("winget")
    if found:
        return found
    alias = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "winget.exe"
    return str(alias) if alias.exists() else None


def info() -> dict:
    """Paket yöneticisi durumu ve yoksa kurulum yönlendirmesi."""
    path = winget_path()
    if path:
        return {"available": True, "manager": "winget", "path": path, "guide": []}
    if config.IS_WIN:
        guide = [
            "Program kurmak için Windows'un paket yöneticisi winget gerekir; Windows 10 (1809+) ve 11'de ücretsizdir.",
            "Aşağıdaki 'Microsoft Store'u aç' düğmesiyle 'Uygulama Yükleyici' (App Installer) sayfasını açıp Yükle/Güncelle'ye basın.",
            "Kurulum bitince bu pencereyi kapatıp yeniden /d yazın.",
        ]
        return {"available": False, "manager": None, "guide": guide, "store_url": WINGET_STORE_URL, "help_url": WINGET_WEB_URL}
    return {"available": False, "manager": None, "store_url": "", "help_url": "https://brew.sh",
            "guide": ["/d şimdilik yalnızca Windows'ta (winget ile) çalışır. macOS'ta programları Homebrew ile kurabilirsiniz: https://brew.sh"]}


def _clean(text: str) -> list[str]:
    """winget çıktısındaki dönen çubukları ve ilerleme satırlarını ayıklar."""
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.split("\r")[-1].rstrip() if "\r" in raw else raw.rstrip()
        if not line.strip() or (len(line.strip()) <= 3 and re.fullmatch(r"[\s\-\\|/]*", line)) or re.search(r"[█▒]", line):
            continue
        lines.append(line)
    return lines


def parse_table(text: str, first_only: bool = False) -> list[dict]:
    """winget'in sütunlu tablolarını (search/upgrade/list) satır sözlüklerine çevirir.

    Başlık satırının hemen altında tire satırı vardır; sütun sınırları başlıktaki kelimelerin
    konumundan alınır (dil ne olursa olsun: Name/Ad, Id/Kimlik...). Sütun anahtarları sıraya göre:
    name, id, version, then available/match, source. Birden çok tablo (ör. "açıkça hedeflenmesi
    gereken paketler") sırayla okunur.
    """
    lines = _clean(text)
    rows: list[dict] = []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and re.fullmatch(r"-{10,}", lines[i + 1].strip()):
            header = lines[i]
            found = list(re.finditer(r"\S+", header))
            starts = [m.start() for m in found]
            words = [m.group(0).lower() for m in found]
            keys = ["name", "id", "version"]
            for w in words[3:]:
                keys.append("source" if w in ("source", "kaynak", "quelle") else
                            "match" if w in ("match", "eşleşme", "eslesme") else "available")
            i += 2
            while i < len(lines):
                line = lines[i]
                if i + 1 < len(lines) and re.fullmatch(r"-{10,}", lines[i + 1].strip()):
                    break  # yeni tablo başlıyor
                if len(line) < (starts[1] if len(starts) > 1 else 1) or re.match(r"^\d+ \S", line):
                    i += 1
                    continue  # "12 upgrades available." gibi özet satırları
                row = {}
                for n, s in enumerate(starts):
                    e = starts[n + 1] if n + 1 < len(starts) else None
                    row[keys[n] if n < len(keys) else f"c{n}"] = line[s:e].strip()
                if row.get("id") and " " not in row["id"] and row.get("name"):
                    rows.append(row)
                i += 1
            if first_only:
                return rows
            continue
        i += 1
    return rows


def _popen_kwargs() -> dict:
    kw: dict = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if config.IS_WIN:
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


async def _winget(args: list[str], timeout: float = SEARCH_TIMEOUT, on_line=None) -> tuple[int, str]:
    """winget'i pencere açmadan çalıştırır; (çıkış kodu 0..2^32, çıktı) döner. on_line her temiz satırda çağrılır."""
    exe = winget_path()
    if not exe:
        raise InstallerError("winget bulunamadı. /d penceresindeki yönlendirmeyle 'Uygulama Yükleyici'yi kurun.")
    proc = await asyncio.create_subprocess_exec(exe, *args, **_popen_kwargs())
    chunks: list[str] = []
    pending = ""

    async def pump():
        nonlocal pending
        while True:
            data = await proc.stdout.read(4096)
            if not data:
                break
            txt = data.decode("utf-8", errors="replace")
            chunks.append(txt)
            if on_line:
                pending += txt
                *done, pending = pending.split("\n")
                for ln in _clean("\n".join(done)):
                    on_line(ln)
        if on_line and pending:
            for ln in _clean(pending):
                on_line(ln)

    try:
        await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise InstallerError(f"winget {int(timeout)} saniyede yanıt vermedi.")
    except asyncio.CancelledError:
        proc.kill()
        raise
    return (proc.returncode or 0) & 0xFFFFFFFF, "".join(chunks)


def valid_id(pkg_id: str) -> bool:
    return bool(_ID_RE.match(pkg_id or ""))


async def search(query: str) -> list[dict]:
    q = (query or "").strip()
    if len(q) < 2:
        return []
    if len(q) > 80 or any(c in q for c in '"\r\n'):
        raise InstallerError("Arama metni geçersiz.")
    _code, out = await _winget(["search", "--query", q, "--source", "winget", *COMMON])
    rows = parse_table(out)[:40]
    return [{"id": r["id"], "name": r["name"], "version": r.get("version", ""), "note": _NOTES.get(r["id"].lower(), "")} for r in rows]


async def upgrades() -> list[dict]:
    _code, out = await _winget(["upgrade", "--source", "winget", *COMMON])
    # Yalnızca ilk tablo: ikinci tablodaki paketler (sabitlenmiş / açıkça hedeflenmesi gereken) winget'in
    # kendi "--all" davranışında olduğu gibi kendiliğinden güncellenmez.
    return [{"id": r["id"], "name": r["name"], "version": r.get("version", ""), "available": r.get("available", "")}
            for r in parse_table(out, first_only=True) if r.get("available")]


_installed_cache: tuple[float, set[str]] = (0.0, set())


async def installed_ids(max_age: float = 300) -> set[str]:
    """winget'in tanıdığı kurulu paket kimlikleri (küçük harf); 5 dk önbellekli."""
    global _installed_cache
    if time.monotonic() - _installed_cache[0] < max_age:
        return _installed_cache[1]
    try:
        _code, out = await _winget(["list", *COMMON], timeout=120)
        ids = {r["id"].lower() for r in parse_table(out)}
    except InstallerError:
        ids = set()
    _installed_cache = (time.monotonic(), ids)
    return ids


async def installed() -> list[dict]:
    """Kurulu programlar (winget list): ad, kimlik, sürüm, varsa yeni sürüm. managed=winget ile güncellenebilir.

    Güncellemesi olanlar başta, sonra ada göre sıralanır. Elle güncelleme penceresi ("update") bunu gösterir.
    """
    global _installed_cache
    _code, out = await _winget(["list", *COMMON], timeout=120)
    rows = parse_table(out)
    _installed_cache = (time.monotonic(), {r["id"].lower() for r in rows})
    seen, items = set(), []
    for r in rows:
        key = r["id"].lower()
        if key in seen:
            continue
        seen.add(key)
        src = r.get("source", "").lower()
        managed = src == "winget" and valid_id(r["id"]) and "…" not in r["id"]
        avail = r.get("available", "") if managed else ""
        items.append({"id": r["id"], "name": r["name"], "version": r.get("version", ""), "available": avail,
                      "source": r.get("source", ""), "managed": managed})
    items.sort(key=lambda x: (not x["available"], not x["managed"], x["name"].lower()))
    return items


async def catalog() -> list[dict]:
    have = await installed_ids() if winget_path() else set()
    return [{**c, "note": c.get("note", ""), "installed": c["id"].lower() in have} for c in CATALOG]


def explain(code: int, output: str, action: str) -> tuple[str, str]:
    """Çıkış kodunu (durum, açıklama) çiftine çevirir."""
    if code == 0:
        low = output.lower()
        if "no available upgrade" in low or "no newer package" in low or "already installed" in low:
            return "current", "Zaten kurulu ve güncel."
        return ("updated" if action == "upgrade" else "ok"), ""
    if code in EXIT_CODES:
        return EXIT_CODES[code]
    m = re.search(r"exit code:\s*(-?\d+)", output, re.I)
    if m and (int(m.group(1)) & 0xFFFFFFFF) in EXIT_CODES:
        return EXIT_CODES[int(m.group(1)) & 0xFFFFFFFF]
    tail = next((ln for ln in reversed(_clean(output)) if ln.strip()), "")
    return "fail", f"winget hata kodu 0x{code:08X}. {tail[:200]}".strip()


# ---------------------------------------------------------------- işler

class Job:
    def __init__(self, kind: str, items: list[dict]):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind  # install | update
        self.items = [{"id": it["id"], "name": it.get("name") or it["id"], "state": "waiting", "message": "",
                       "note": it.get("note") or _NOTES.get(it["id"].lower(), "")} for it in items]
        self.phase = "starting"  # starting | listing | working | self_update | done | cancelled | error
        self.current = ""
        self.log: list[str] = []
        self.error = ""
        self.self_update: dict = {}
        self.started = datetime.now().isoformat(timespec="seconds")
        self.finished = ""
        self.task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.phase not in ("done", "cancelled", "error")

    def add_log(self, line: str) -> None:
        self.log.append(line[:300])
        if len(self.log) > MAX_LOG:
            del self.log[: len(self.log) - MAX_LOG]

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for it in self.items:
            counts[it["state"]] = counts.get(it["state"], 0) + 1
        return counts

    def guide(self) -> list[str]:
        """Bittikten sonra kullanıcıya gösterilecek yönlendirmeler."""
        out: list[str] = []
        if any(it["state"] == "reboot" for it in self.items):
            out.append("Bazı programların tamamlanması için bilgisayarı yeniden başlatın.")
        for it in self.items:
            if it["state"] in ("ok", "updated", "reboot") and it["note"]:
                out.append(f"{it['name']}: {it['note']}")
        if any(it["state"] == "fail" for it in self.items):
            out.append("Başarısız olanları tekrar deneyebilir ya da ADA'ya sohbette 'X programını kur' diyerek başka yolla kurdurabilirsiniz.")
        if self.self_update.get("message"):
            out.append(self.self_update["message"])
        return out

    def public(self) -> dict:
        done = sum(1 for it in self.items if it["state"] not in ("waiting", "running"))
        return {"id": self.id, "kind": self.kind, "phase": self.phase, "running": self.running, "items": self.items,
                "current": self.current, "log": self.log[-80:], "error": self.error, "started": self.started,
                "finished": self.finished, "done": done, "total": len(self.items), "summary": self.summary(),
                "guide": self.guide() if not self.running else [], "self_update": self.self_update,
                "labels": STATE_LABEL}


JOB: Job | None = None


def current() -> dict | None:
    return JOB.public() if JOB else None


def _busy() -> None:
    if JOB and JOB.running:
        raise InstallerError("Başka bir kurulum/güncelleme sürüyor. Bitmesini bekleyin ya da durdurun.")
    if safety.is_halted():
        raise InstallerError("ADA acil durdurulmuş. Önce 'Devam et' deyin.")
    if not winget_path():
        raise InstallerError("winget bulunamadı. /d penceresindeki yönlendirmeyle 'Uygulama Yükleyici'yi kurun.")


async def _run_package(job: Job, it: dict, action: str) -> None:
    it["state"] = "running"
    job.current = it["name"]
    job.add_log(f"▶ {it['name']} ({it['id']})")
    args = [action, "--id", it["id"], "--exact", *PKG_FLAGS]
    try:
        code, out = await _winget(args, timeout=INSTALL_TIMEOUT, on_line=job.add_log)
        state, msg = explain(code, out, action)
    except InstallerError as e:
        state, msg = "fail", str(e)
    it["state"], it["message"] = state, msg
    job.add_log(f"■ {it['name']}: {STATE_LABEL.get(state, state)}{(' — ' + msg) if msg else ''}")
    safety.log("program_" + ("kur" if action == "install" else "güncelle"), {"id": it["id"], "kaynak": "/d"},
               ok=state != "fail", result=f"{state} {msg}"[:300])


async def _job_install(job: Job) -> None:
    job.phase = "working"
    for it in job.items:
        await _run_package(job, it, "install")


async def _job_update(job: Job, self_update) -> None:
    job.phase = "listing"
    job.current = "Güncellemeler aranıyor"
    job.add_log("Güncellemesi olan programlar aranıyor…")
    found = await upgrades()
    job.items = [{"id": u["id"], "name": u["name"], "state": "waiting",
                  "message": f"{u['version']} → {u['available']}", "note": _NOTES.get(u["id"].lower(), "")} for u in found]
    job.add_log(f"{len(found)} programın güncellemesi var." if found else "Tüm programlar güncel.")
    job.phase = "working"
    for it in job.items:
        ver = it["message"]
        await _run_package(job, it, "upgrade")
        if it["state"] == "updated":
            it["message"] = ver
    if self_update:
        job.phase = "self_update"
        job.current = "ADA"
        job.add_log("ADA'nın yeni sürümü denetleniyor…")
        job.self_update = await self_update()
        job.add_log(job.self_update.get("message", ""))


async def _wrap(job: Job, coro) -> None:
    global _installed_cache
    try:
        await coro
        job.phase = "done"
    except asyncio.CancelledError:
        job.phase = "cancelled"
        for it in job.items:
            if it["state"] in ("waiting", "running"):
                it["state"], it["message"] = "skipped", "Durduruldu."
    except InstallerError as e:
        job.phase, job.error = "error", str(e)
    except Exception as e:  # noqa: BLE001 - arayüze anlaşılır hata dönsün
        job.phase, job.error = "error", f"Beklenmeyen hata: {type(e).__name__}: {e}"
    finally:
        job.current = ""
        job.finished = datetime.now().isoformat(timespec="seconds")
        _installed_cache = (0.0, set())  # kurulu listesi değişti


def start_install(items: list[dict]) -> dict:
    global JOB
    _busy()
    clean, seen = [], set()
    for it in items or []:
        pid = str(it.get("id", "")).strip()
        if not valid_id(pid):
            raise InstallerError(f"Geçersiz paket kimliği: {pid[:60]}")
        if pid.lower() in seen:
            continue
        seen.add(pid.lower())
        clean.append({"id": pid, "name": str(it.get("name") or pid)[:120]})
    if not clean:
        raise InstallerError("Kurulacak program seçilmedi.")
    if len(clean) > 30:
        raise InstallerError("Tek seferde en fazla 30 program kurulabilir.")
    JOB = Job("install", clean)
    JOB.task = asyncio.create_task(_wrap(JOB, _job_install(JOB)))
    return JOB.public()


def start_update(self_update=None) -> dict:
    """Her şeyi günceller. self_update: ADA'nın kendini güncellemesini yöneten async çağrı (sunucu verir)."""
    global JOB
    _busy()
    JOB = Job("update", [])
    JOB.task = asyncio.create_task(_wrap(JOB, _job_update(JOB, self_update)))
    return JOB.public()


async def _job_upgrade_selected(job: Job, self_update) -> None:
    job.phase = "working"
    for it in job.items:
        ver = it["message"]
        await _run_package(job, it, "upgrade")
        if it["state"] == "updated":
            it["message"] = ver
    if self_update:
        job.phase = "self_update"
        job.current = "ADA"
        job.add_log("ADA'nın yeni sürümü denetleniyor…")
        job.self_update = await self_update()
        job.add_log(job.self_update.get("message", ""))


def start_upgrade(items: list[dict], self_update=None) -> dict:
    """Kullanıcının elle seçtiği programları günceller; self_update verilirse en sonda ADA da güncellenir."""
    global JOB
    _busy()
    clean, seen = [], set()
    for it in items or []:
        pid = str(it.get("id", "")).strip()
        if not valid_id(pid):
            raise InstallerError(f"Geçersiz paket kimliği: {pid[:60]}")
        if pid.lower() in seen:
            continue
        seen.add(pid.lower())
        ver = " → ".join(v for v in (str(it.get("version") or "")[:40], str(it.get("available") or "")[:40]) if v)
        clean.append({"id": pid, "name": str(it.get("name") or pid)[:120], "ver": ver})
    if not clean and not self_update:
        raise InstallerError("Güncellenecek program seçilmedi.")
    if len(clean) > 100:
        raise InstallerError("Tek seferde en fazla 100 program güncellenebilir.")
    JOB = Job("update", clean)
    for it, src in zip(JOB.items, clean):
        it["message"] = src["ver"]
    JOB.task = asyncio.create_task(_wrap(JOB, _job_upgrade_selected(JOB, self_update)))
    return JOB.public()


def cancel() -> bool:
    if JOB and JOB.running and JOB.task:
        JOB.task.cancel()
        return True
    return False


# Mesajın tamamı bu kelimelerden biriyse (dil fark etmeksizin) elle güncelleme penceresi açılır
UPDATE_WORDS = {
    "update", "updates", "upgrade", "/update", "/u",
    "güncelle", "guncelle", "güncelleme", "guncelleme", "güncellemeler", "güncelle!",
    "aktualisieren", "aktualisierung", "aktualisierungen",
    "actualizar", "actualiza", "actualización", "actualizacion", "actualizaciones",
    "mettre à jour", "mettre a jour", "mise à jour", "mise a jour", "mises à jour", "maj",
    "aggiorna", "aggiornare", "aggiornamento", "aggiornamenti",
    "atualizar", "atualiza", "atualização", "atualizacao", "atualizações",
    "обновить", "обнови", "обновление", "обновления",
    "更新", "升级", "升級", "アップデート", "更新する", "アップグレード",
    "bijwerken", "aktualizuj", "aktualizacja", "تحديث", "업데이트", "अपडेट",
}


def is_update_word(text: str) -> bool:
    t = re.sub(r"[\s.!?。！？]+$", "", (text or "").strip()).lower()
    return t in UPDATE_WORDS


def parse_command(text: str) -> dict | None:
    """Sohbet metni /d komutuysa {action, query} döner: action = install | update."""
    if is_update_word(text):
        return {"action": "update", "query": "", "manual": True}
    m = re.fullmatch(r"\s*/d(?:\s+(.*))?\s*", text or "", re.I | re.S)
    if not m:
        return None
    rest = (m.group(1) or "").strip()
    if rest.lower() in ("update", "upgrade", "güncelle", "guncelle", "u"):
        return {"action": "update", "query": ""}
    return {"action": "install", "query": rest[:80]}
