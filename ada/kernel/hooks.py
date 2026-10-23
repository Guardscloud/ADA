"""Kernel kancaları: araç çağrısından önce/sonra ve sistem talimatı."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Hooks:
    before: list[Callable[..., Any]] = field(default_factory=list)
    after: list[Callable[..., Any]] = field(default_factory=list)
    system: list[Callable[[], Any]] = field(default_factory=list)

    def clear(self) -> None:
        self.before.clear()
        self.after.clear()
        self.system.clear()


HOOKS = Hooks()


def before_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """fn(name, args): dict döndürürse girdi onunla değişir; str döndürürse çağrı engellenir."""
    HOOKS.before.append(fn)
    return fn


def after_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """fn(name, args, result): ToolResult döndürürse sonucun yerine geçer."""
    HOOKS.after.append(fn)
    return fn


def system(fn: Callable[[], Any]) -> Callable[[], Any]:
    """fn() -> str: sistem talimatının bağlam bölümüne eklenir (her turda yeniden çağrılır)."""
    HOOKS.system.append(fn)
    return fn


def _report(where: str, fn: Callable, e: Exception) -> None:
    try:
        from .. import safety

        safety.log("kernel_kanca_hatası", {"kanca": where, "fonksiyon": getattr(fn, "__qualname__", str(fn))}, ok=False, result=f"{type(e).__name__}: {e}")
    except Exception:  # noqa: BLE001
        pass


async def _call(fn: Callable, *args):
    res = fn(*args)
    if inspect.isawaitable(res):
        res = await res
    return res


async def run_before(name: str, args: Any) -> tuple[Any, str | None]:
    """(yeni girdi, engelleme nedeni) döner."""
    for fn in list(HOOKS.before):
        try:
            res = await _call(fn, name, args)
        except Exception as e:  # noqa: BLE001
            _report("before_tool", fn, e)
            continue
        if isinstance(res, str) and res:
            return args, res
        if isinstance(res, dict):
            args = res
    return args, None


async def run_after(name: str, args: Any, result):
    from ..tools import ToolResult

    for fn in list(HOOKS.after):
        try:
            res = await _call(fn, name, args, result)
        except Exception as e:  # noqa: BLE001
            _report("after_tool", fn, e)
            continue
        if isinstance(res, ToolResult):
            result = res
    return result


def system_text() -> str:
    parts = []
    for fn in list(HOOKS.system):
        try:
            t = fn()
        except Exception as e:  # noqa: BLE001
            _report("system", fn, e)
            continue
        if isinstance(t, str) and t.strip():
            parts.append(t.strip())
    return "\n\n".join(parts)
