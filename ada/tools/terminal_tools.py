"""Terminal araçları: ADA'nın arayüzdeki Terminal panelinde görünen uzun ömürlü/etkileşimli komutlar
çalıştırması (ör. `claude remote-control`, geliştirme sunucusu, soru soran kurulum programları).

Tek seferlik komutlar için run_shell daha uygundur; burada açılan oturumlar kapatılana kadar yaşar ve
kullanıcı aynı oturumu panelden izleyip yazabilir. Kalıcı komut (ADA açılınca başlayan, kapanınca yeniden
başlatılan) yalnızca kullanıcı sohbetten isterken kurulabilir; arka plan işleri kuramaz.
"""

from __future__ import annotations

import asyncio
import re

from .. import config, terminal
from . import ToolError, ToolResult, tool

if config.ENABLE_SHELL:
    from .system import HARD_BLOCK, _shell_risk

    def _check(command: str) -> None:
        for pattern in HARD_BLOCK:
            if re.search(pattern, command, re.IGNORECASE):
                raise ToolError("Bu komut sistemi/diski yok edebilir; ADA bunu hiçbir koşulda çalıştırmaz.")

    def _session(session_id: str) -> terminal.Session:
        s = terminal.get(session_id)
        if s is None:
            raise ToolError(f"Terminal oturumu bulunamadı: {session_id}. terminal_list ile bakın.")
        return s

    @tool(
        "terminal_list",
        "Terminal panelindeki oturumları (çalışıyor/bitti) ve kalıcı komutları listeler.",
        {},
    )
    async def terminal_list() -> ToolResult:
        sessions = terminal.list_sessions()
        saved = terminal.KEEPER.status()
        lines = ["Oturumlar:"]
        for s in sessions:
            state = "çalışıyor" if s["alive"] else f"bitti (kod {s['exit_code']})"
            lines.append(f"- {s['id']} · {s['title']} · {state} · komut: {s['command'] or '(kabuk)'}")
        if not sessions:
            lines.append("-")
        lines += ["", "Kalıcı komutlar:"]
        for k in saved:
            lines.append(
                f"- {k['id']} · {k['title']} · {k['command']} · {'çalışıyor' if k['alive'] else 'çalışmıyor'}"
                + (f" · {k['gave_up']}" if k.get("gave_up") else "")
            )
        if not saved:
            lines.append("-")
        return ToolResult("\n".join(lines))

    @tool(
        "terminal_open",
        "Terminal panelinde yeni bir oturum açar ve komutu orada çalıştırır (komut bitene ya da kapatılana kadar "
        "yaşar; kullanıcı panelden izleyip yazabilir). Uzun süre açık kalması gereken veya etkileşimli komutlar için "
        "kullan: `claude remote-control`, `npm run dev`, soru soran kurulumlar. Komut boşsa boş bir kabuk açılır. "
        "Birkaç saniye bekleyip ilk çıktıyı döndürür; sonrası için terminal_read.",
        {
            "command": {"type": "string", "description": "Tek satırlık komut (Windows: PowerShell, macOS: zsh)"},
            "title": {"type": "string"},
            "cwd": {"type": "string", "description": "Çalışma klasörü (varsayılan: ev dizini)"},
            "wait_seconds": {"type": "integer", "description": "İlk çıktı için bekleme (varsayılan 5, en fazla 30)"},
        },
        risk=_shell_risk,
    )
    async def terminal_open(command: str = "", title: str = "", cwd: str = "", wait_seconds: int = 5) -> ToolResult:
        _check(command)
        try:
            s = terminal.open_session(command, title, cwd, source="ada")
        except (ValueError, RuntimeError) as e:
            raise ToolError(str(e)) from None
        await asyncio.sleep(max(0, min(wait_seconds, 30)))
        state = "çalışıyor" if s.alive else f"bitti (çıkış kodu {s.exit_code})"
        return ToolResult(f"Oturum {s.id} açıldı ({state}). Kullanıcı Terminal panelinden görebilir.\n--- çıktı ---\n{s.tail(4000)}")

    @tool(
        "terminal_read",
        "Bir terminal oturumunun son çıktısını okur (renk kodları temizlenmiş). Bir şeyin yazılmasını bekliyorsan "
        "wait_seconds ver (en fazla 60).",
        {
            "session_id": {"type": "string"},
            "chars": {"type": "integer", "description": "Son kaç karakter (varsayılan 4000, en fazla 20000)"},
            "wait_seconds": {"type": "integer"},
        },
        ["session_id"],
    )
    async def terminal_read(session_id: str, chars: int = 4000, wait_seconds: int = 0) -> ToolResult:
        s = _session(session_id)
        await asyncio.sleep(max(0, min(wait_seconds, 60)))
        state = "çalışıyor" if s.alive else f"bitti (çıkış kodu {s.exit_code})"
        return ToolResult(f"Oturum {s.id} · {s.title} · {state}\n--- çıktı ---\n{s.tail(max(200, min(chars, 20000)))}")

    @tool(
        "terminal_send",
        "Çalışan bir terminal oturumuna yazı gönderir (soru yanıtlamak, komut yazmak). enter=false değilse sonuna "
        "Enter eklenir. Ctrl+C için text='^C'.",
        {
            "session_id": {"type": "string"},
            "text": {"type": "string"},
            "enter": {"type": "boolean"},
            "wait_seconds": {"type": "integer", "description": "Gönderdikten sonra çıktı için bekleme (varsayılan 2)"},
        },
        ["session_id", "text"],
        risk=lambda a: _shell_risk({"command": a.get("text", "")}) if isinstance(a, dict) else None,
    )
    async def terminal_send(session_id: str, text: str, enter: bool = True, wait_seconds: int = 2) -> ToolResult:
        s = _session(session_id)
        if not s.alive:
            raise ToolError("Bu oturumun süreci bitmiş; terminal_open ile yeni oturum açın.")
        if text == "^C":
            s.write("\x03")
        else:
            _check(text)
            s.write(text + ("\r" if enter else ""))
        await asyncio.sleep(max(0, min(wait_seconds, 60)))
        return ToolResult(f"Gönderildi.\n--- son çıktı ---\n{s.tail(3000)}")

    @tool(
        "terminal_close",
        "Bir terminal oturumunu ve altında çalışan programları kapatır.",
        {"session_id": {"type": "string"}},
        ["session_id"],
        risk=lambda a: "Terminal oturumu kapatılacak (içindeki program durur).",
    )
    async def terminal_close(session_id: str) -> ToolResult:
        _session(session_id)
        terminal.close_session(session_id)
        return ToolResult(f"Oturum {session_id} kapatıldı.")

    @tool(
        "terminal_keep",
        "Bir komutu KALICI yapar: ADA açılınca kendiliğinden başlar, kapanırsa yeniden başlatılır (ör. kullanıcı "
        "'claude remote-control hep açık kalsın' derse). Yalnızca kullanıcı sohbette açıkça istediğinde kullan.",
        {
            "command": {"type": "string"},
            "title": {"type": "string"},
            "cwd": {"type": "string"},
        },
        ["command"],
        risk=lambda a: f"Kalıcı komut kurulacak (ADA her açıldığında çalışır): {a.get('command')}",
    )
    async def terminal_keep(command: str, title: str = "", cwd: str = "") -> ToolResult:
        from ..runtime import UNATTENDED

        if UNATTENDED.get():
            raise ToolError("Kalıcı komutlar arka plan işlerinden kurulamaz; kullanıcı Terminal panelinden ekleyebilir.")
        _check(command)
        try:
            item = terminal.add_saved(command, title, cwd, autostart=True)
            s = terminal.start_saved(item["id"])
        except (ValueError, RuntimeError, LookupError) as e:
            raise ToolError(str(e)) from None
        from .. import safety

        safety.log("terminal_kalıcı_komut", {"command": command, "kaynak": "ada"}, ok=True, result=item["id"])
        return ToolResult(f"Kalıcı komut kuruldu ({item['id']}) ve oturum {s.id} başlatıldı. Terminal panelinden yönetilebilir.")
