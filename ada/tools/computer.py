"""Ekran kontrolü: Anthropic `computer_toolset_20260801` üyelerinin yerel uygulaması.

Model, ona gönderdiğimiz ekran görüntüsünün piksel uzayında koordinat verir. Retina
ekranlarda görüntü fiziksel piksel (2x) olarak gelir; biz onu mantıksal boyuta ve ardından
SCREENSHOT_MAX_WIDTH'e ölçekleriz; koordinatları geri çevirirken aynı çarpanı kullanırız.
"""

from __future__ import annotations

import io
import os
import subprocess
import time
from contextvars import ContextVar

from .. import config
from . import ToolResult

TOOLSET_NAME = "computer"
# Gemini'nin Qwen yedeğine geçtiği turlarda koordinat biçimini o anki model belirler (None: sağlayıcıya göre)
COORDS_OVERRIDE: ContextVar[bool | None] = ContextVar("COORDS_OVERRIDE", default=None)
# ADA-AGI yönlendiricisinin seçtiği motorun koordinat biçimi ("pixel" | "norm1000")
ENGINE_COORDS: ContextVar[str | None] = ContextVar("ENGINE_COORDS", default=None)
MEMBERS = {
    "screenshot", "zoom", "left_click", "right_click", "middle_click", "double_click", "triple_click",
    "left_click_drag", "mouse_move", "left_mouse_down", "left_mouse_up", "cursor_position",
    "scroll", "type", "key", "hold_key", "wait",
}

# xdotool tarzı tuş adları -> pyautogui adları (platformdan bağımsız olanlar)
KEYMAP = {
    "return": "enter", "enter": "enter", "kp_enter": "enter", "escape": "esc", "esc": "esc",
    "backspace": "backspace", "delete": "delete", "tab": "tab", "space": "space",
    "ctrl": "ctrl", "control": "ctrl", "control_l": "ctrl",
    "shift": "shift", "shift_l": "shift", "page_down": "pagedown", "pagedown": "pagedown", "page_up": "pageup",
    "pageup": "pageup", "home": "home", "end": "end", "up": "up", "down": "down", "left": "left", "right": "right",
    "minus": "-", "plus": "+", "equal": "=", "comma": ",", "period": ".", "slash": "/", "semicolon": ";",
    "apostrophe": "'", "grave": "`", "bracketleft": "[", "bracketright": "]", "backslash": "\\",
    "caps_lock": "capslock", "insert": "insert", "print": "printscreen", "kp_add": "+", "kp_subtract": "-",
}
# Değiştirici tuşlar platforma göre farklı pyautogui adlarına eşlenir: macOS'ta Command/Option,
# Windows'ta Ctrl/Win/Alt. Böylece model 'cmd+s' derse Windows'ta Ctrl+S basılır.
if config.IS_MAC:
    KEYMAP.update({
        "super": "command", "super_l": "command", "cmd": "command", "command": "command",
        "win": "command", "meta": "command",
        "alt": "option", "alt_l": "option", "option": "option",
    })
else:
    KEYMAP.update({
        "cmd": "ctrl", "command": "ctrl",
        "win": "win", "super": "win", "super_l": "win", "meta": "win",
        "alt": "alt", "alt_l": "alt", "option": "alt",
    })

_state = {"factor": None, "logical": None}  # factor: model px -> mantıksal px


def _gui():
    import pyautogui

    pyautogui.FAILSAFE = True  # fare sol üst köşeye götürülürse durur
    pyautogui.PAUSE = 0.05
    return pyautogui


def _capture():
    """(PIL tam ekran görüntüsü, mantıksal boyut, cihaz piksel oranı)"""
    gui = _gui()
    logical_w, logical_h = gui.size()
    shot = gui.screenshot()
    ratio = shot.width / logical_w
    _state["logical"] = (logical_w, logical_h)
    return shot, (logical_w, logical_h), ratio


def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _screenshot() -> ToolResult:
    from PIL import Image

    shot, (lw, lh), _ = _capture()
    target_w = min(config.SCREENSHOT_MAX_WIDTH, lw)
    factor = lw / target_w
    target_h = round(lh / factor)
    _state["factor"] = factor
    img = shot.resize((target_w, target_h), Image.LANCZOS)
    note = (
        "koordinatları 0-1000 arası normalize ver: [x, y], x soldan sağa, y yukarıdan aşağıya (0,0 sol üst; 1000,1000 sağ alt)"
        if normalized() else f"koordinatlar bu görüntünün pikselleridir ({target_w}x{target_h})"
    )
    return ToolResult(f"Ekran görüntüsü {target_w}x{target_h} — {note}", image=_png(img))


def _factor() -> float:
    if _state["factor"] is None:
        _screenshot()
    return _state["factor"]


def normalized() -> bool:
    """Gemini modelleri görüntüdeki konumları 0-1000 arası normalize verir (piksel değil).
    Claude ve kendi sunucu modellerinde koordinatlar ekran görüntüsü pikselidir."""
    if ENGINE_COORDS.get() is not None:
        # ADA-AGI: o anki motorun koordinat biçimi her şeyden önce gelir (motorlar farklı aileden olabilir)
        return ENGINE_COORDS.get() == "norm1000"
    mode = os.getenv("ADA_SCREEN_COORDS", "auto").strip().lower()
    if mode in ("pixel", "norm1000"):
        return mode == "norm1000"
    if COORDS_OVERRIDE.get() is not None:
        return COORDS_OVERRIDE.get()
    return config.PROVIDER == "gemini"


def _logical_size() -> tuple[int, int]:
    if _state["logical"] is None:
        _screenshot()
    return _state["logical"]


def _to_logical(coord) -> tuple[float, float]:
    if not (isinstance(coord, (list, tuple)) and len(coord) == 2):
        raise ValueError("coordinate [x, y] biçiminde olmalı")
    if normalized():
        lw, lh = _logical_size()
        x, y = (min(max(float(v), 0.0), 1000.0) for v in coord)
        return x / 1000 * lw, y / 1000 * lh
    f = _factor()
    return float(coord[0]) * f, float(coord[1]) * f


def _from_logical(x: float, y: float) -> list[int]:
    if normalized():
        lw, lh = _logical_size()
        return [round(x / lw * 1000), round(y / lh * 1000)]
    f = _factor()
    return [round(x / f), round(y / f)]


def _keys(text: str) -> list[str]:
    keys = []
    for part in text.replace("-", "+").split("+") if len(text) > 1 else [text]:
        k = part.strip()
        if not k:
            continue
        keys.append(KEYMAP.get(k.lower(), k if len(k) > 1 else k.lower()))
    return keys


def _modifiers(text: str | None) -> list[str]:
    return _keys(text) if text else []


def _click(button: str, coord, mods, clicks: int = 1) -> ToolResult:
    gui = _gui()
    for m in mods:
        gui.keyDown(m)
    try:
        if coord is not None:
            x, y = _to_logical(coord)
            gui.moveTo(x, y, duration=0.15)
        gui.click(button=button, clicks=clicks, interval=0.08)
    finally:
        for m in reversed(mods):
            gui.keyUp(m)
    return ToolResult("OK")


def _type(text: str) -> ToolResult:
    """Metni birebir yazar. macOS'ta her zaman pano + Cmd+V kullanılır: tuş tabanlı yazma ABD klavye
    düzenini varsayar ve Türkçe Q'da '_' '.' gibi karakterleri bozar (ör. '.' → 'ç'). Windows'ta ise
    saf ASCII hızlıca yazılır; Türkçe/ASCII-dışı karakterler pyautogui.write tarafından sessizce
    düşürüldüğü için pano + Ctrl+V ile yazılır. Kullanıcının pano içeriği işlemden sonra geri yüklenir."""
    gui = _gui()
    if not config.IS_MAC:
        if text.isascii():
            gui.write(text, interval=0.01)
            return ToolResult("OK")
        return _paste_type(text, gui)
    previous = subprocess.run(["pbpaste"], capture_output=True).stdout
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    time.sleep(0.05)
    # pyautogui'nin Cmd+V'si Command tuşunu kaybedip yalnızca "v" yazabiliyor; macOS'un kendi yolu güvenilir
    paste = subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to keystroke "v" using command down'],
        capture_output=True,
    )
    if paste.returncode != 0:
        gui.hotkey("command", "v")
    time.sleep(0.3)  # yapıştırma tamamlansın
    subprocess.run(["pbcopy"], input=previous, check=False)
    return ToolResult("OK")


def _paste_type(text: str, gui) -> ToolResult:
    """Windows/Linux: metni pano üzerinden Ctrl+V ile yazar (ASCII-dışı karakterler için).
    Önceki pano içeriği sonradan geri yüklenir."""
    import pyperclip

    try:
        previous = pyperclip.paste()
    except Exception:  # noqa: BLE001 - pano okunamazsa geri yükleme yapmayız
        previous = None
    pyperclip.copy(text)
    time.sleep(0.05)
    gui.hotkey("ctrl", "v")
    time.sleep(0.3)  # yapıştırma tamamlansın
    if previous is not None:
        try:
            pyperclip.copy(previous)
        except Exception:  # noqa: BLE001
            pass
    return ToolResult("OK")


def _zoom(region) -> ToolResult:
    from PIL import Image

    if not (isinstance(region, (list, tuple)) and len(region) == 4):
        raise ValueError("region [x0, y0, x1, y1] biçiminde olmalı")
    shot, _, ratio = _capture()
    (lx0, ly0), (lx1, ly1) = _to_logical(region[:2]), _to_logical(region[2:])
    x0, y0, x1, y1 = [int(v * ratio) for v in (lx0, ly0, lx1, ly1)]
    x0, x1 = sorted((max(0, x0), min(shot.width, x1)))
    y0, y1 = sorted((max(0, y0), min(shot.height, y1)))
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise ValueError("region çok küçük")
    crop = shot.crop((x0, y0, x1, y1))
    max_w, max_h = config.SCREENSHOT_MAX_WIDTH, round(config.SCREENSHOT_MAX_WIDTH * 0.75)
    scale = min(max_w / crop.width, max_h / crop.height, 2.0)
    if scale != 1:
        crop = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))), Image.LANCZOS)
    return ToolResult(f"Yakınlaştırılmış bölge {region}", image=_png(crop))


def execute(name: str, inp: dict) -> ToolResult:
    """Tek bir toolset üyesini çalıştırır. Hata durumunda is_error=True döner."""
    if not config.ENABLE_COMPUTER:
        return ToolResult("Ekran kontrolü kapalı (ADA_ENABLE_COMPUTER=false).", is_error=True)
    inp = inp or {}
    try:
        gui = _gui()
        if name == "screenshot":
            return _screenshot()
        if name == "zoom":
            return _zoom(inp.get("region"))
        if name in ("left_click", "right_click", "middle_click", "double_click", "triple_click"):
            button = {"right_click": "right", "middle_click": "middle"}.get(name, "left")
            clicks = {"double_click": 2, "triple_click": 3}.get(name, 1)
            return _click(button, inp.get("coordinate"), _modifiers(inp.get("text")), clicks)
        if name == "left_click_drag":
            sx, sy = _to_logical(inp.get("start_coordinate"))
            ex, ey = _to_logical(inp.get("coordinate"))
            mods = _modifiers(inp.get("text"))
            for m in mods:
                gui.keyDown(m)
            try:
                gui.moveTo(sx, sy, duration=0.15)
                gui.mouseDown()
                gui.moveTo(ex, ey, duration=0.4)
                gui.mouseUp()
            finally:
                for m in reversed(mods):
                    gui.keyUp(m)
            return ToolResult("OK")
        if name == "mouse_move":
            x, y = _to_logical(inp.get("coordinate"))
            gui.moveTo(x, y, duration=0.15)
            return ToolResult("OK")
        if name == "left_mouse_down":
            gui.mouseDown()
            return ToolResult("OK")
        if name == "left_mouse_up":
            gui.mouseUp()
            return ToolResult("OK")
        if name == "cursor_position":
            x, y = gui.position()
            return ToolResult(str(_from_logical(x, y)))
        if name == "scroll":
            if inp.get("coordinate") is not None:
                x, y = _to_logical(inp["coordinate"])
                gui.moveTo(x, y, duration=0.1)
            amount = max(1, int(inp.get("scroll_amount", 3))) * 5
            direction = inp.get("scroll_direction", "down")
            mods = _modifiers(inp.get("text"))
            for m in mods:
                gui.keyDown(m)
            try:
                if direction in ("up", "down"):
                    gui.scroll(amount if direction == "up" else -amount)
                else:
                    gui.hscroll(amount if direction == "right" else -amount)
            finally:
                for m in reversed(mods):
                    gui.keyUp(m)
            return ToolResult("OK")
        if name == "type":
            return _type(str(inp.get("text", "")))
        if name == "key":
            keys = _keys(str(inp.get("text", "")))
            if not keys:
                raise ValueError("text gerekli")
            for _ in range(min(max(int(inp.get("repeat", 1)), 1), 100)):
                gui.hotkey(*keys) if len(keys) > 1 else gui.press(keys[0])
            return ToolResult("OK")
        if name == "hold_key":
            keys = _keys(str(inp.get("text", "")))
            duration = min(float(inp.get("duration", 1)), 300)
            for k in keys:
                gui.keyDown(k)
            time.sleep(duration)
            for k in reversed(keys):
                gui.keyUp(k)
            return ToolResult("OK")
        if name == "wait":
            time.sleep(min(float(inp.get("duration", 1)), 300))
            return ToolResult("OK")
        return ToolResult(f"Bilinmeyen ekran eylemi: {name}", is_error=True)
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ == "FailSafeException":
            return ToolResult("Kullanıcı fareyi ekranın köşesine götürerek ekran kontrolünü durdurdu.", is_error=True)
        return ToolResult(f"{type(e).__name__}: {e}", is_error=True)


_XY = {"type": "array", "items": {"type": "integer"}, "description": "[x, y] ekran görüntüsü pikseli"}
_MODS = {"type": "string", "description": "Basılı tutulacak değiştirici tuşlar, ör. 'shift', 'ctrl+shift' (macOS'ta 'cmd+shift')"}

# Anthropic dışı sağlayıcılar için aynı üyelerin fonksiyon tanımları (Claude toolset'i sunucuda tanımlıdır)
MEMBER_SCHEMAS: dict[str, tuple[str, dict, list[str]]] = {
    "screenshot": ("Ekranın tamamının görüntüsünü alır. Diğer ekran eylemlerinden önce ve sonra doğrulamak için kullanın.", {}, []),
    "zoom": ("Ekranın bir bölgesini yüksek çözünürlükte gösterir (küçük yazıları okumak için). Koordinatlar tam ekran görüntüsüne göredir.",
             {"region": {"type": "array", "items": {"type": "integer"}, "description": "[x0, y0, x1, y1]"}}, ["region"]),
    "left_click": ("Sol tık. coordinate verilmezse imlecin olduğu yere.", {"coordinate": _XY, "text": _MODS}, []),
    "right_click": ("Sağ tık.", {"coordinate": _XY, "text": _MODS}, []),
    "middle_click": ("Orta tık.", {"coordinate": _XY, "text": _MODS}, []),
    "double_click": ("Çift tık.", {"coordinate": _XY, "text": _MODS}, []),
    "triple_click": ("Üç kez tık (satır/paragraf seçmek için).", {"coordinate": _XY, "text": _MODS}, []),
    "left_click_drag": ("start_coordinate'tan coordinate'a sürükler.", {"start_coordinate": _XY, "coordinate": _XY, "text": _MODS}, ["start_coordinate", "coordinate"]),
    "mouse_move": ("Fareyi taşır (tıklamadan).", {"coordinate": _XY}, ["coordinate"]),
    "left_mouse_down": ("Sol fare tuşunu basılı tutar.", {}, []),
    "left_mouse_up": ("Sol fare tuşunu bırakır.", {}, []),
    "cursor_position": ("İmlecin [x, y] konumunu döner.", {}, []),
    "scroll": ("Kaydırır.", {"scroll_direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                             "scroll_amount": {"type": "integer"}, "coordinate": _XY, "text": _MODS}, ["scroll_direction", "scroll_amount"]),
    "type": ("Klavye odağına metin yazar (Türkçe karakter desteklenir).", {"text": {"type": "string"}}, ["text"]),
    "key": ("Tuş veya kombinasyon: 'Return', 'Tab', 'Escape', kaydet için 'ctrl+s' (macOS'ta 'cmd+s'). "
            "'cmd'/'command' Windows'ta otomatik olarak Ctrl'e, 'win'/'super' Windows tuşuna eşlenir.",
            {"text": {"type": "string"}, "repeat": {"type": "integer"}}, ["text"]),
    "hold_key": ("Tuşu belirtilen saniye basılı tutar.", {"text": {"type": "string"}, "duration": {"type": "number"}}, ["text", "duration"]),
    "wait": ("Belirtilen saniye bekler (uygulama yüklenirken).", {"duration": {"type": "number"}}, ["duration"]),
}


def function_declarations() -> list[dict]:
    """Ekran üyelerinin sağlayıcıdan bağımsız JSON şeması tanımları (koordinat birimi sağlayıcıya göre)."""
    unit = ("0-1000 arası normalize koordinat (0,0 sol üst; 1000,1000 sağ alt)" if normalized()
            else "ekran görüntüsü pikseli")

    def fix(props: dict) -> dict:
        out = {}
        for k, v in props.items():
            if isinstance(v, dict) and "ekran görüntüsü pikseli" in v.get("description", ""):
                v = {**v, "description": v["description"].replace("ekran görüntüsü pikseli", unit)}
            out[k] = v
        return out

    return [
        {"name": name, "description": "[Ekran] " + desc + (f" Koordinatlar: {unit}." if props else ""),
         "parameters": {"type": "object", "properties": fix(props), "required": req}}
        for name, (desc, props, req) in MEMBER_SCHEMAS.items()
    ]


def permissions_hint() -> str:
    return (
        "macOS'ta Sistem Ayarları > Gizlilik ve Güvenlik altında 'Ekran Kaydı' ve 'Erişilebilirlik' "
        "izinlerini ADA'yı çalıştıran terminale/Python'a verin."
    )
