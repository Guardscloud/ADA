"""Geri alma araçları: ADA'nın değiştirdiği dosyaların eski hallerine dönme."""

from __future__ import annotations

from .. import safety
from . import ToolError, tool


@tool(
    "list_backups",
    "ADA'nın değiştirmeden/üzerine yazmadan önce aldığı dosya yedeklerini listeler (en yeniler önce). "
    "Kullanıcı 'geri al', 'eski haline döndür' derse kullan.",
    {"query": {"type": "string", "description": "İsteğe bağlı: dosya adı veya yolunda aranacak metin"}},
)
def list_backups(query: str = "") -> str:
    q = query.casefold()
    items = [b for b in safety.list_backups(200) if not q or q in b["original"].casefold()]
    return "\n".join(f"[{b['id']}] {b['time']} — {b['original']}" for b in items[:50]) or "Yedek yok."


@tool(
    "restore_backup",
    "Bir yedeği dosyanın orijinal yerine geri yükler (şimdiki hali de yedeklenir, yani bu işlem de geri alınabilir).",
    {"backup_id": {"type": "string"}},
    ["backup_id"],
)
def restore_backup(backup_id: str) -> str:
    try:
        return safety.restore(backup_id)
    except ValueError as e:
        raise ToolError(str(e))
