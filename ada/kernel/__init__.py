"""ADA Open Kernel: ADA'yı kaynak koduna dokunmadan genişletmenin resmi yolu.

Eklenti = %APPDATA%\\ADA\\kernel\\plugins (macOS: ~/Library/Application Support/ADA/kernel/plugins) içindeki
herhangi bir .py dosyası. ADA açılırken alfabetik sırayla yüklenir; güncellemeler bu klasöre dokunmaz.
Bir eklenti hata verirse atlanır, ADA çalışmaya devam eder. ADA_KERNEL_PLUGINS=off tümünü kapatır;
ADA_KERNEL_PLUGINS=<klasör> başka bir klasör kullandırır.

Eklentinin yapabildikleri (ayrıntı: docs/KERNEL.md):

    from ada import kernel

    @kernel.tool("hello", "Selam verir", {"name": {"type": "string"}}, required=["name"])
    def hello(name: str) -> str:            # aynı adla kaydedilen araç yerleşik aracın yerine geçer
        return f"Selam {name}"

    @kernel.before_tool
    def guard(name, args):                  # dict → yeni girdi, str → çağrıyı engelle (modele hata döner)
        if name == "run_shell" and "format" in args.get("command", ""):
            return "Bu komut eklenti tarafından engellendi."

    @kernel.after_tool
    def tag(name, args, result):            # ToolResult döndürürse sonucun yerine geçer
        return None

    @kernel.system
    def extra() -> str:                     # sistem talimatına eklenecek metin
        return "Yanıtları her zaman kısa tut."

Kancalar senkron ya da async olabilir. Kanca hataları yutulur ve işlem günlüğüne yazılır; araç zinciri bozulmaz.
Güvenlik: eklentiler kullanıcının yetkisiyle çalışan normal Python kodudur; yalnızca güvendiğiniz dosyaları koyun.
"""

from __future__ import annotations

from .hooks import HOOKS, after_tool, before_tool, system
from .loader import PluginInfo, load_plugins, loaded, plugin_dir
from ..tools import REGISTRY, ToolError, ToolResult, tool

API_VERSION = 1

__all__ = [
    "API_VERSION", "HOOKS", "REGISTRY", "PluginInfo", "ToolError", "ToolResult",
    "after_tool", "before_tool", "load_plugins", "loaded", "plugin_dir", "system", "tool",
]
