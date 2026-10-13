"""Eski yerleşik "Sistem bakımı ve güvenlik denetimi" rutininin kaldırılması.

Önceki sürümler ilk açılışta workspace/rutinler/sistem-bakimi.md rutinini kendiliğinden ekliyordu. Bu rutin
artık yok: ADA hiçbir rutini kendisi eklemez. Eski sürümden güncellenen kurulumlarda ADA'nın eklediği rutin
bir kez silinir (dosyanın yedeği alınır; raporları outputs/rutinler altında kalır).
"""

from __future__ import annotations

from . import config

FLAG = config.DATA_DIR / "bakim_rutini_eklendi"  # eski sürüm rutini eklediğinde bırakıyordu
FILENAME = "sistem-bakimi.md"


def remove_builtin() -> bool:
    """ADA'nın eklediği bakım rutinini siler. Bir şey silindiyse True."""
    if not FLAG.exists():
        return False  # bu kurulumda ADA rutin eklemedi (ya da zaten kaldırıldı)
    from . import routines

    removed = False
    try:
        wanted = (config.ROUTINES_DIR / FILENAME).relative_to(config.WORKSPACE).as_posix()
        for r in routines.load():
            if r.get("file") == wanted:
                removed = routines.delete(r["id"]) or removed
        leftover = config.ROUTINES_DIR / FILENAME  # kayıt oluşmadan kalmış dosya
        if leftover.exists():
            leftover.unlink()
            removed = True
    except Exception as e:  # noqa: BLE001 - silinemese de ADA çalışmaya devam etmeli; sonraki açılışta yine denenir
        print(f"[bakım] eski rutin kaldırılamadı: {type(e).__name__}: {e}")
        return removed
    FLAG.unlink(missing_ok=True)
    return removed
