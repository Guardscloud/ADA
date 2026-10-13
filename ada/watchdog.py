"""Gözcü: ADA açık olduğu sürece bilgisayarı ve ADA'nın kendi işlerini kesintisiz denetler.

İki katman vardır:
1. Ölçümler (her WATCH_SECONDS; yapay zekâ kullanmaz, ücretsizdir): disk doluluğu, bellek, uzun süre
   yüksek işlemci, pil, internet, Qwen yedek sunucusu, kalıcı terminal komutları, art arda başarısız
   rutinler. Yeni bir sorun çıkınca masaüstü bildirimi gider; ADA_WATCH_AI_FIX açıksa ADA sorunu arka
   planda inceler ve güvenli düzeltmeleri yapar (aynı sorun için 6 saatte bir, toplam saatte en fazla 3 iş).
2. Kontrol listesi (workspace/gozcu.md; her ADA_WATCH_AI_MINUTES dakikada): kullanıcının "şunlara sürekli
   göz kulak ol" maddeleri yapay zekâ ile denetlenir. Her şey yolundaysa o konuşma silinir (kenar çubuğu
   dolmasın); sorun bulunursa konuşma kalır ve bildirim gider.

gozcu.md genel dosya araçlarıyla değiştirilemez (bkz. tools.guard_write): bir web sayfasına gizlenmiş
talimat ADA'ya kendisine her saat çalışan bir iş yazdıramasın.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import threading
import time
from datetime import datetime, timedelta

from . import config

STATE_FILE = config.DATA_DIR / "watch.json"
CHECKLIST_FILE = config.WORKSPACE / "gozcu.md"

DISK_WARN, DISK_CRIT = 90.0, 97.0  # doluluk yüzdesi
MEM_WARN = 92.0
CPU_WARN = 95.0
CPU_SAMPLES, MEM_SAMPLES, NET_SAMPLES, QWEN_SAMPLES = 5, 3, 3, 3  # art arda kaç ölçümde sorun sayılır
BATTERY_WARN = 15
HISTORY_LIMIT = 100
AI_FIX_COOLDOWN = timedelta(hours=6)
AI_FIX_PER_HOUR = 3
AI_FIX_KINDS = {"disk", "mem", "cpu", "keeper", "routine", "qwen"}

CHECKLIST_TEMPLATE = """# Gözcü kontrol listesi

ADA açıkken bu listeyi düzenli aralıklarla (Ayarlar → Gözcü aralığı) kontrol eder.
Her şey yolundaysa sessiz kalır; bir sorun bulursa bildirim gönderir ve gerekiyorsa düzeltir.
Aşağıdaki örnek maddelerin başındaki "<!--" ve sonundaki "-->" satırlarını silerek etkinleştirin
ya da kendi maddelerinizi yazın. Liste boşken yapay zekâ kontrolü yapılmaz (ücretsiz ölçümler sürer).

<!--
- Masaüstündeki "Gelen" klasörüne yeni dosya düştüyse türüne göre Belgeler altındaki klasörlere taşı.
- D:\\projeler altındaki git depolarında 3 günden eski, gönderilmemiş (push edilmemiş) değişiklik var mı?
- https://ornek.com sitesi açılıyor mu? Açılmıyorsa bana bildir.
-->
"""

_lock = threading.RLock()
_TASKS: set[asyncio.Task] = set()


def _now() -> datetime:
    return datetime.now()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


# ---------------------------------------------------------------- kalıcı durum

def _load_state() -> dict:
    with _lock:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {"history": [], "fixes": {}, "checklist": {}}


def _save_state(state: dict) -> None:
    with _lock:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)


def _modify(fn) -> dict:
    with _lock:
        state = _load_state()
        fn(state)
        _save_state(state)
        return state


# ---------------------------------------------------------------- kontrol listesi

def checklist_text() -> str:
    try:
        return CHECKLIST_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


def ensure_checklist() -> None:
    if not CHECKLIST_FILE.exists():
        CHECKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        CHECKLIST_FILE.write_text(CHECKLIST_TEMPLATE, encoding="utf-8")


def checklist_items(text: str | None = None) -> str:
    """Kontrol listesinin etkin kısmı: HTML yorumları ve başlıklar/açıklamalar dışındaki maddeler."""
    text = re.sub(r"<!--.*?(-->|$)", "", text if text is not None else checklist_text(), flags=re.S)
    lines = [ln.rstrip() for ln in text.splitlines()]
    items = [ln for ln in lines if re.match(r"\s*([-*+]|\d+[.)])\s+\S", ln)]
    return "\n".join(items).strip()


def save_checklist(content: str) -> None:
    if len(content.encode("utf-8")) > 100_000:
        raise ValueError("Kontrol listesi çok uzun (en fazla 100 KB).")
    CHECKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKLIST_FILE.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------- ölçümler

def _disks() -> list[dict]:
    import psutil

    out, seen = [], set()
    for p in psutil.disk_partitions(all=False):
        if "cdrom" in p.opts or not p.fstype or p.mountpoint in seen:
            continue
        if not config.IS_WIN and not (p.mountpoint == "/" or p.mountpoint.startswith(("/Volumes/", "/home", "/mnt/", "/media/"))):
            continue
        seen.add(p.mountpoint)
        try:
            u = psutil.disk_usage(p.mountpoint)
        except OSError:
            continue
        out.append({"mount": p.mountpoint, "percent": round(u.percent, 1), "free_gb": round(u.free / 1e9, 1), "total_gb": round(u.total / 1e9, 1)})
    return out


def _top_processes(key: str, n: int = 3) -> list[str]:
    import psutil

    rows = []
    for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
        try:
            info = p.info
            value = info["cpu_percent"] if key == "cpu" else (info["memory_info"].rss / 1e9 if info["memory_info"] else 0)
            if info["name"] and info["name"] != "System Idle Process":
                rows.append((value or 0, info["name"], p.pid))
        except Exception:  # noqa: BLE001
            continue
    rows.sort(reverse=True)
    unit = "%" if key == "cpu" else " GB"
    return [f"{name} (pid {pid}, {value:.1f}{unit})" for value, name, pid in rows[:n]]


def _internet_ok() -> bool:
    host = "generativelanguage.googleapis.com" if config.PROVIDER == "gemini" else "api.anthropic.com"
    for target in ((host, 443), ("1.1.1.1", 443)):
        try:
            with socket.create_connection(target, timeout=4):
                return True
        except OSError:
            continue
    return False


def _qwen_local_ok() -> bool | None:
    """Yerel Qwen yedeği (Ollama vb.) yanıt veriyor mu? Yedek yerel değilse/kapalıysa None.
    ADA-AGI modunda: açık yerel motorların sunucusu yanıt veriyor mu?"""
    if config.PROVIDER == "adaagi":
        from . import engines

        return engines.local_servers_ok()
    if not (config.PROVIDER == "gemini" and config.QWEN_FALLBACK):
        return None
    base = config.QWEN_BASE_URL.rstrip("/")
    if not any(h in base for h in ("localhost", "127.0.0.1", "0.0.0.0")):
        return None
    import httpx

    try:
        return httpx.get(base + "/models", timeout=4).status_code < 500
    except httpx.HTTPError:
        return False


def measure() -> dict:
    """Engelleyici; thread içinde çağrılır."""
    import psutil

    vm = psutil.virtual_memory()
    battery = None
    try:
        b = psutil.sensors_battery()
        if b is not None:
            battery = {"percent": round(b.percent), "plugged": bool(b.power_plugged)}
    except Exception:  # noqa: BLE001
        pass
    return {
        "time": _iso(_now()),
        "cpu": psutil.cpu_percent(interval=None),
        "mem": round(vm.percent, 1),
        "mem_used_gb": round(vm.used / 1e9, 1),
        "mem_total_gb": round(vm.total / 1e9, 1),
        "disks": _disks(),
        "battery": battery,
        "internet": _internet_ok(),
        "qwen": _qwen_local_ok(),
        "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 1),
    }


# ---------------------------------------------------------------- gözcü

class Watchdog:
    def __init__(self, tick_seconds: float | None = None) -> None:
        self.tick_seconds = tick_seconds or config.WATCH_SECONDS
        self.metrics: dict = {}
        self.problems: dict[str, dict] = {}  # anahtar -> sorun
        self.streaks: dict[str, int] = {}
        self.fix_runs: dict[str, str] = {}  # sorun anahtarı -> inceleme konuşması (çalışırken)
        self.checklist_conv: str | None = None
        self.last_tick: datetime | None = None
        self.started = _now()

    # -- yardımcılar
    def _streak(self, key: str, bad: bool) -> int:
        self.streaks[key] = self.streaks.get(key, 0) + 1 if bad else 0
        return self.streaks[key]

    def evaluate(self, m: dict) -> dict[str, dict]:
        """Ölçümlerden güncel sorun listesini üretir (anahtar → {kind, level, title, detail})."""
        from . import routines, terminal

        found: dict[str, dict] = {}
        for d in m.get("disks", []):
            if d["percent"] >= DISK_WARN:
                found[f"disk:{d['mount']}"] = {
                    "kind": "disk", "level": "crit" if d["percent"] >= DISK_CRIT else "warn",
                    "title": f"{d['mount']} diski %{d['percent']:.0f} dolu",
                    "detail": f"Boş alan {d['free_gb']} GB / {d['total_gb']} GB.",
                }
        if self._streak("mem", m.get("mem", 0) >= MEM_WARN) >= MEM_SAMPLES:
            found["mem"] = {"kind": "mem", "level": "warn", "title": f"Bellek %{m['mem']:.0f} dolu",
                            "detail": "En çok bellek kullananlar: " + ", ".join(m.get("top_mem") or [])}
        if self._streak("cpu", m.get("cpu", 0) >= CPU_WARN) >= CPU_SAMPLES:
            found["cpu"] = {"kind": "cpu", "level": "warn", "title": f"İşlemci uzun süredir %{m['cpu']:.0f}",
                            "detail": "En çok işlemci kullananlar: " + ", ".join(m.get("top_cpu") or [])}
        b = m.get("battery")
        if b and not b["plugged"] and b["percent"] <= BATTERY_WARN:
            found["battery"] = {"kind": "battery", "level": "warn", "title": f"Pil %{b['percent']}", "detail": "Şarja takın."}
        if self._streak("net", m.get("internet") is False) >= NET_SAMPLES:
            found["net"] = {"kind": "net", "level": "crit", "title": "İnternet bağlantısı yok",
                            "detail": "Yapay zekâ sağlayıcısına ulaşılamıyor; görevler yerel yedekle (varsa) sürer."}
        if self._streak("qwen", m.get("qwen") is False) >= QWEN_SAMPLES:
            if config.PROVIDER == "adaagi":
                found["qwen"] = {"kind": "qwen", "level": "warn", "title": "Yerel model sunucusu yanıt vermiyor",
                                 "detail": "Ollama/LM Studio kapalı olabilir; ADA-AGI bulut motorlarıyla sürer ve gerekince Ollama'yı başlatır."}
            else:
                found["qwen"] = {"kind": "qwen", "level": "warn", "title": "Yerel Qwen yedeği yanıt vermiyor",
                                 "detail": f"{config.QWEN_BASE_URL} adresine ulaşılamıyor (Ollama kapalı olabilir)."}
        for k in terminal.KEEPER.status():
            if k.get("gave_up"):
                found[f"keeper:{k['id']}"] = {"kind": "keeper", "level": "crit",
                                              "title": f"Kalıcı komut çalışmıyor: {k['title']}", "detail": k["gave_up"]}
        try:
            for r in routines.load():
                if r.get("fail_streak", 0) >= 2 and r.get("enabled"):
                    found[f"routine:{r['id']}"] = {"kind": "routine", "level": "warn",
                                                   "title": f"Rutin art arda başarısız: {r['name']}",
                                                   "detail": (r.get("last_summary") or "")[:300]}
        except Exception:  # noqa: BLE001
            pass
        return found

    def status(self) -> dict:
        state = _load_state()
        cl = state.get("checklist", {})
        items = checklist_items()
        interval = config.WATCH_AI_MINUTES
        last = cl.get("last_run")
        nxt = None
        if config.WATCH and interval > 0 and items:
            nxt = _iso(datetime.fromisoformat(last) + timedelta(minutes=interval)) if last else _iso(self.started + timedelta(minutes=1))
        return {
            "enabled": config.WATCH,
            "tick_seconds": self.tick_seconds,
            "last_tick": _iso(self.last_tick),
            "metrics": self.metrics,
            "problems": sorted(self.problems.values(), key=lambda p: (p["level"] != "crit", p["since"])),
            "history": state.get("history", [])[:50],
            "ai_fix": config.WATCH_AI_FIX,
            "fixing": dict(self.fix_runs),
            "checklist": {
                "file": str(CHECKLIST_FILE), "items": items.count("\n") + 1 if items else 0,
                "interval_minutes": interval, "last_run": last, "last_status": cl.get("last_status"),
                "last_summary": cl.get("last_summary"), "last_conv": cl.get("last_conv"),
                "running": bool(self.checklist_conv), "next_run": nxt,
            },
        }

    # -- döngü
    async def loop(self) -> None:
        from . import maintenance

        await asyncio.to_thread(ensure_checklist)
        await asyncio.to_thread(maintenance.remove_builtin)  # eski sürümün eklediği bakım rutini kaldırılır
        while True:
            if config.WATCH:
                try:
                    await self.tick()
                except Exception as e:  # noqa: BLE001
                    print(f"[gözcü] hata: {type(e).__name__}: {e}")
            await asyncio.sleep(self.tick_seconds)

    async def tick(self, now: datetime | None = None) -> None:
        from . import safety

        now = now or _now()
        m = await asyncio.to_thread(measure)
        if m["cpu"] >= CPU_WARN:
            m["top_cpu"] = await asyncio.to_thread(_top_processes, "cpu")
        if m["mem"] >= MEM_WARN:
            m["top_mem"] = await asyncio.to_thread(_top_processes, "mem")
        self.metrics = m
        self.last_tick = now
        await self.apply(self.evaluate(m), now)
        if not safety.is_halted():
            await self.maybe_checklist(now)

    async def apply(self, found: dict[str, dict], now: datetime) -> None:
        """Yeni sorunları bildirir/kaydeder, düzelenleri kapatır."""
        from . import runtime, safety

        new = [k for k in found if k not in self.problems]
        gone = [k for k in self.problems if k not in found]
        for k in gone:
            p = self.problems.pop(k)
            self._history({"time": _iso(now), "key": k, "event": "çözüldü", "title": p["title"]})
        for k, p in found.items():
            if k in self.problems:
                self.problems[k].update(title=p["title"], detail=p["detail"], level=p["level"])
            else:
                self.problems[k] = {**p, "key": k, "since": _iso(now)}
        for k in new:
            p = self.problems[k]
            self._history({"time": _iso(now), "key": k, "event": "sorun", "title": p["title"], "detail": p["detail"], "level": p["level"]})
            runtime.notify(f"ADA Gözcü: {p['title']}", p["detail"][:160])
            safety.log("gözcü_sorun", {"key": k}, ok=False, result=f"{p['title']} — {p['detail']}")
            if config.WATCH_AI_FIX and p["kind"] in AI_FIX_KINDS and not safety.is_halted():
                await self.maybe_fix(k, p, now)

    def _history(self, entry: dict) -> None:
        _modify(lambda s: s.__setitem__("history", [entry, *s.get("history", [])][:HISTORY_LIMIT]))

    # -- yapay zekâ ile inceleme
    async def maybe_fix(self, key: str, p: dict, now: datetime) -> bool:
        from . import runtime

        state = _load_state()
        fixes: dict = state.get("fixes", {})
        last = fixes.get(key)
        if last and now - datetime.fromisoformat(last) < AI_FIX_COOLDOWN:
            return False
        recent = [t for t in fixes.values() if now - datetime.fromisoformat(t) < timedelta(hours=1)]
        if len(recent) >= AI_FIX_PER_HOUR or key in self.fix_runs:
            return False
        _modify(lambda s: s.setdefault("fixes", {}).__setitem__(key, _iso(now)))
        try:
            conv = await runtime.start_background(
                f"🛡 Gözcü: {p['title'][:45]}", self.fix_prompt(p), display_text=f"🛡 Gözcü sorunu inceliyor: {p['title']}",
                notify_done=True, max_minutes=15,
            )
        except RuntimeError as e:
            print(f"[gözcü] inceleme başlatılamadı: {e}")
            return False
        self.fix_runs[key] = conv.id
        task = runtime.RUNS[conv.id].task

        def done(_t, key=key):
            self.fix_runs.pop(key, None)

        task.add_done_callback(done)
        return True

    def snapshot_text(self) -> str:
        m = self.metrics or {}
        disks = ", ".join(f"{d['mount']} %{d['percent']:.0f} ({d['free_gb']} GB boş)" for d in m.get("disks", []))
        lines = [
            f"Zaman: {m.get('time')}",
            f"İşlemci: %{m.get('cpu')}  · Bellek: %{m.get('mem')} ({m.get('mem_used_gb')}/{m.get('mem_total_gb')} GB)",
            f"Diskler: {disks or '-'}",
            f"İnternet: {'var' if m.get('internet') else 'yok'}  · Açık kalma süresi: {m.get('uptime_h')} saat",
        ]
        if self.problems:
            lines.append("Açık sorunlar: " + "; ".join(p["title"] for p in self.problems.values()))
        return "\n".join(lines)

    def fix_prompt(self, p: dict) -> str:
        return (
            "[GÖZCÜ MODU] Gözcü bilgisayarda bir sorun tespit etti. Kullanıcı başında değil.\n\n"
            f"SORUN: {p['title']}\nAYRINTI: {p['detail']}\n\nSİSTEM DURUMU:\n{self.snapshot_text()}\n\n"
            "Yapılacaklar:\n"
            "1. Sorunun nedenini araçlarla araştır (ör. hangi klasör diski dolduruyor, hangi süreç takıldı, rutinin hatası ne).\n"
            "2. Yalnızca güvenli düzeltmeleri kendin yap: paket yöneticisi önbelleklerini kendi komutlarıyla temizlemek "
            "(ör. `uv cache clean`, `npm cache clean --force`), geçici klasörlerde (%TEMP%, /tmp) YALNIZCA 2 günden eski "
            "dosyaları silmek (yeni dosyalar çalışan programlara ait olabilir), kapanmış bir hizmeti (ör. Ollama) yeniden "
            "başlatmak gibi. "
            "Kullanıcının belgelerini, projelerini, fotoğraflarını SİLME; büyük kişisel dosyaları yalnızca raporla. "
            "Kullanıcının açık programlarını kapatma.\n"
            "3. Sonunda kısa bir rapor yaz: neden, ne yaptın, kullanıcının yapması gereken bir şey var mı."
        )

    async def maybe_checklist(self, now: datetime, force: bool = False) -> str | None:
        """Vakti geldiyse kontrol listesini yapay zekâya denetletir. Başlatılan konuşma kimliğini döner."""
        from . import runtime, store

        interval = config.WATCH_AI_MINUTES
        items = checklist_items()
        if self.checklist_conv or not items or (interval <= 0 and not force):
            return None
        last = _load_state().get("checklist", {}).get("last_run")
        if not force:
            due = datetime.fromisoformat(last) + timedelta(minutes=interval) if last else self.started + timedelta(minutes=1)
            if now < due:
                return None
        _modify(lambda s: s.setdefault("checklist", {}).update(last_run=_iso(now), last_status="running"))
        prompt = (
            "[GÖZCÜ MODU] Kullanıcının sürekli göz kulak olmanı istediği maddeleri şimdi denetle. Kullanıcı başında değil; "
            "soru sorma. Her maddeyi araçlarla gerçekten kontrol et; bir madde açıkça bir iş yapmanı istiyorsa (taşı, düzenle) yap. "
            "Geri alınamaz işlemleri (silme, e-posta gönderme, ödeme) yapma; gerekirse taslak bırak ve raporla.\n\n"
            f"KONTROL LİSTESİ (workspace/gozcu.md):\n{items}\n\nSİSTEM DURUMU:\n{self.snapshot_text()}\n\n"
            "Yanıtında her madde için tek satırlık sonuç yaz. Yanıtının SON satırı tam olarak 'DURUM: TAMAM' "
            "(hiçbir sorun yok, kullanıcının bilmesi gereken bir şey yok) ya da 'DURUM: SORUN' (kullanıcının bilmesi/yapması "
            "gereken bir şey var veya bir iş yaptın) olsun."
        )
        try:
            conv = await runtime.start_background(
                f"🛡 Gözcü kontrolü · {now:%d.%m %H:%M}", prompt, display_text="🛡 Gözcü kontrol listesini denetliyor",
                notify_done=False, max_minutes=20,
            )
        except RuntimeError as e:
            _modify(lambda s: s.setdefault("checklist", {}).update(last_status="error", last_summary=str(e)))
            return None
        self.checklist_conv = conv.id
        run = runtime.RUNS[conv.id]

        async def finish() -> None:
            try:
                await asyncio.wait({run.task})
                fresh = store.load(conv.id)
                text = (fresh.ui[-1].get("text", "") if fresh and fresh.ui else "").strip()
                errors = [e.get("message", "") for e in run.events if e.get("type") == "error"]
                last_line = text.splitlines()[-1].upper() if text else ""
                if errors and not text:
                    status, summary = "error", errors[-1]
                elif "DURUM: TAMAM" in last_line:
                    status, summary = "ok", "Her şey yolunda."
                else:
                    status = "issue"
                    body = "\n".join(text.splitlines()[:-1]) if "DURUM:" in last_line else text
                    summary = " ".join(body.split())[:400] or (errors[-1] if errors else "Sonuç alınamadı.")
                keep = status != "ok"
                if not keep:  # sorunsuz kontroller kenar çubuğunu doldurmasın
                    runtime.RUNS.pop(conv.id, None)
                    store.delete(conv.id)
                else:
                    runtime.notify("ADA Gözcü: kontrol listesinde dikkat gereken bir şey var", summary[:160])
                    self._history({"time": _iso(_now()), "key": "checklist", "event": "sorun", "title": "Kontrol listesi", "detail": summary, "conv_id": conv.id, "level": "warn"})
                _modify(lambda s: s.setdefault("checklist", {}).update(
                    last_status=status, last_summary=summary, last_conv=conv.id if keep else None))
            except Exception as e:  # noqa: BLE001
                print(f"[gözcü] kontrol sonucu kaydedilemedi: {type(e).__name__}: {e}")
            finally:
                self.checklist_conv = None

        task = asyncio.create_task(finish())
        _TASKS.add(task)  # referans tutulmazsa görev erken çöpe gidebilir
        task.add_done_callback(_TASKS.discard)
        return conv.id


WATCHDOG = Watchdog()
