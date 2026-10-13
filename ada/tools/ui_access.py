"""Yerel uygulamaları erişilebilirlik (Accessibility) bilgisiyle kullanma — koordinat tahmini yok.

macOS her penceredeki düğme/alan/metinleri rol, ad, kimlik ve kesin konumlarıyla bildirir. ADA önce
`ui_elements` ile numaralı listeyi alır, sonra `ui_click` / `ui_type` ile numaraya göre işlem yapar.
Görsel konum belirlemesi zayıf modellerde (ör. ücretsiz Gemini lite) ekran görüntüsü tıklamasından
çok daha güvenilirdir; metin öğeleri (ör. hesap makinesi ekranı) doğrudan okunur.
"""

from __future__ import annotations

import re
import time

from . import ToolError, ToolResult, tool
from .system import _q

SEP = "\u241f"
ROLES = {
    "AXButton": "düğme", "AXTextField": "metin alanı", "AXTextArea": "metin alanı", "AXSearchField": "arama alanı",
    "AXCheckBox": "onay kutusu", "AXRadioButton": "seçenek", "AXPopUpButton": "açılır liste", "AXMenuButton": "menü düğmesi",
    "AXComboBox": "birleşik kutu", "AXLink": "bağlantı", "AXTab": "sekme", "AXStaticText": "metin", "AXSlider": "kaydırıcı",
    "AXCell": "hücre", "AXRow": "satır", "AXMenuItem": "menü öğesi",
}
# Pencere düğmeleri (kapat/küçült) listelenmez: yanlışlıkla pencere kapatılmasın
SKIP_IDS = {"_XCUI:CloseWindow", "_XCUI:MinimizeWindow", "_XCUI:FullScreenWindow"}
RISKY = re.compile(
    r"satın al|öde|ödeme|sipariş|gönder|send|submit|purchase|buy|pay|delete|sil|kaldır|remove|erase|transfer|onayla ve",
    re.IGNORECASE,
)

_last: dict[str, list[dict]] = {}  # uygulama -> son listelenen öğeler


def _script(app: str, limit: int) -> str:
    roles = "{" + ", ".join(f'"{r}"' for r in ROLES) + "}"
    return f'''
tell application "System Events"
  if not (exists process "{_q(app)}") then return "YOK"
  tell process "{_q(app)}"
    if (count of windows) = 0 then return "PENCERE_YOK"
    set out to ""
    set n to 0
    set els to entire contents of window 1
    repeat with e in els
      try
        set r to role of e
        if r is in {roles} then
          set lbl to ""
          repeat with a in {{"AXTitle", "AXDescription", "AXIdentifier", "AXHelp", "AXPlaceholderValue"}}
            try
              set v to value of attribute a of e
              if v is not missing value and (v as text) is not "" then
                set lbl to lbl & a & "=" & (v as text) & ";"
              end if
            end try
          end repeat
          set val to ""
          try
            set val to (value of e) as text
          end try
          set p to position of e
          set s to size of e
          set out to out & r & "{SEP}" & lbl & "{SEP}" & val & "{SEP}" & (item 1 of p) & "{SEP}" & (item 2 of p) & "{SEP}" & (item 1 of s) & "{SEP}" & (item 2 of s) & linefeed
          set n to n + 1
          if n ≥ {limit} then exit repeat
        end if
      end try
    end repeat
    return out
  end tell
end tell'''


def _label(attrs: str) -> tuple[str, str]:
    d = dict(kv.split("=", 1) for kv in attrs.split(";") if "=" in kv)
    ident = d.get("AXIdentifier", "")
    for key in ("AXTitle", "AXDescription", "AXPlaceholderValue", "AXHelp"):
        if d.get(key):
            return d[key][:80], ident
    return ident[:80], ident


def _native_collect(app: str, budget_s: float = 4.0, max_nodes: int = 4000) -> list[dict]:
    """macOS erişilebilirlik arayüzüyle doğrudan okuma (AppleScript'ten ~1000 kat hızlı).

    ImportError: pyobjc yok; RuntimeError: izin/süreç/pencere sorunu (mesaj kullanıcıya uygun).
    """
    import ApplicationServices as AS  # pyobjc-framework-applicationservices
    from AppKit import NSWorkspace

    if not AS.AXIsProcessTrusted():
        raise PermissionError("ADA'nın Erişilebilirlik izni yok (Sistem Ayarları → Gizlilik ve Güvenlik → Erişilebilirlik → ADA).")
    target = app.casefold()
    pid = None
    for ra in NSWorkspace.sharedWorkspace().runningApplications():
        exe = ra.executableURL().path().split("/")[-1] if ra.executableURL() else ""
        names = {str(ra.localizedName() or ""), exe, str(ra.bundleIdentifier() or "")}
        if target in {n.casefold() for n in names if n}:
            pid = ra.processIdentifier()
            break
    if pid is None:
        raise RuntimeError(f"'{app}' çalışmıyor. Önce open_item ile aç (süreç adı İngilizce olabilir: Calculator, Notes…).")
    app_el = AS.AXUIElementCreateApplication(pid)
    AS.AXUIElementSetMessagingTimeout(app_el, 1.5)

    def attr(el, name):
        err, v = AS.AXUIElementCopyAttributeValue(el, name, None)
        return None if err else v

    def geometry(el):
        pos, size = attr(el, "AXPosition"), attr(el, "AXSize")
        if pos is None or size is None:
            return None
        _, p = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
        _, sz = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
        return p.x, p.y, sz.width, sz.height

    def walk() -> list[dict]:
        win = attr(app_el, "AXFocusedWindow") or (attr(app_el, "AXWindows") or [None])[0]
        if win is None:
            raise RuntimeError(f"'{app}' açık ama penceresi yok.")
        out, stack, seen, t0 = [], [(win, 0)], 0, time.time()
        while stack and seen < max_nodes and time.time() - t0 < budget_s:
            el, depth = stack.pop()
            seen += 1
            role = attr(el, "AXRole")
            if role in ROLES:
                labels = {k: attr(el, k) for k in ("AXTitle", "AXDescription", "AXIdentifier", "AXHelp", "AXPlaceholderValue")}
                labels = {k: str(v) for k, v in labels.items() if isinstance(v, (str, int, float)) and str(v)}
                value = attr(el, "AXValue")
                value = str(value).replace("\u200e", "").strip() if isinstance(value, (str, int, float)) else ""
                geo = geometry(el)
                ident = labels.get("AXIdentifier", "")
                if geo and geo[2] >= 1 and geo[3] >= 1 and ident not in SKIP_IDS:
                    label = next((labels[k][:80] for k in ("AXTitle", "AXDescription", "AXPlaceholderValue", "AXHelp") if labels.get(k)), ident[:80])
                    x, y, w, h = geo
                    out.append({"role": role, "label": label, "id": ident, "value": value, "cx": x + w / 2, "cy": y + h / 2, "ref": el})
            kids = attr(el, "AXChildren") if depth < 40 else None
            if kids:
                stack.extend((k, depth + 1) for k in reversed(kids))  # görsel sırayı koru
        return out

    items = walk()
    if len(items) < 10:
        # Electron/Chromium uygulamaları (VS Code, Slack, Teams…) ağacı istenince açar
        for flag in ("AXManualAccessibility", "AXEnhancedUserInterface"):
            AS.AXUIElementSetAttributeValue(app_el, flag, True)
        time.sleep(0.6)
        more = walk()
        if len(more) > len(items):
            items = more
    return items


def _applescript_collect(app: str) -> list[dict]:
    from .mac_apps import run_osascript

    raw = run_osascript(_script(app, 400), timeout=90)
    if raw == "YOK":
        raise RuntimeError(f"'{app}' çalışmıyor. Önce open_item ile aç (süreç adı İngilizce olabilir: Calculator, Notes…).")
    if raw == "PENCERE_YOK":
        raise RuntimeError(f"'{app}' açık ama penceresi yok.")
    items = []
    for row in raw.splitlines():
        p = row.split(SEP)
        if len(p) != 7:
            continue
        role, attrs, value = p[0], p[1], "" if p[2] == "missing value" else p[2].replace("\u200e", "").strip()
        label, ident = _label(attrs)
        if ident in SKIP_IDS:
            continue
        x, y, w, h = (float(v) for v in p[3:7])
        if w < 1 or h < 1:
            continue
        items.append({"role": role, "label": label, "id": ident, "value": value, "cx": x + w / 2, "cy": y + h / 2})
    return items


@tool(
    "ui_elements",
    "Bir Mac uygulamasının ön penceresindeki düğme, alan, onay kutusu, bağlantı, hücre ve METİNLERİ numaralı liste "
    "olarak verir (ad, kimlik, değer). Yerel uygulamalarda (Hesap Makinesi, Sistem Ayarları, Notlar, Finder, "
    "Mail, Electron uygulamaları vb.) tıklamadan önce bunu kullan; sonra ui_click / ui_type ile numaraya göre "
    "işlem yap. Ekrandaki sonuçları 'metin' öğelerinden oku. Ekran görüntüsüyle koordinat tahmin etmekten çok "
    "daha güvenilirdir. Uzun listelerde filter ile daralt.",
    {
        "app": {"type": "string", "description": "Uygulama adı: 'Calculator', 'Notes', 'System Settings', 'Finder', 'Code'…"},
        "filter": {"type": "string", "description": "İsteğe bağlı: adında/değerinde geçen metin"},
    },
    ["app"],
)
def ui_elements(app: str, filter: str = "") -> ToolResult:
    from .mac_apps import ensure_running

    if app in ("Mail", "Calendar", "Reminders", "Notes", "Contacts"):
        ensure_running(app)
    try:
        items = _native_collect(app)
    except ImportError:
        items = None
    except PermissionError as e:
        raise ToolError(str(e))
    except RuntimeError as e:
        raise ToolError(str(e))
    if items is None:
        try:
            items = _applescript_collect(app)
        except RuntimeError as e:
            raise ToolError(str(e))

    needle = filter.casefold()
    if needle:
        items = [it for it in items if needle in f"{it['label']} {it['id']} {it['value']}".casefold()]
    _last[app] = items
    lines = []
    for i, it in enumerate(items, 1):
        kind = ROLES.get(it["role"], it["role"])
        extra = f' = "{it["value"][:60]}"' if it["value"] and it["role"] != "AXButton" else ""
        ident = f' (id: {it["id"]})' if it["id"] and it["id"] != it["label"] else ""
        lines.append(f"[{i}] {kind}: {it['label'] or '(adsız)'}{ident}{extra}")
        if len(lines) >= 200:
            lines.append("… (liste kısaltıldı; filter kullan)")
            break
    return ToolResult(f"{app} penceresi — {len(items)} öğe:\n" + ("\n".join(lines) or "(öğe yok)"))


def _element(app: str, index: int) -> dict:
    items = _last.get(app)
    if not items:
        raise ToolError(f"Önce ui_elements ile '{app}' öğelerini listele.")
    if not 1 <= index <= len(items):
        raise ToolError(f"Geçersiz numara {index} (1-{len(items)}).")
    return items[index - 1]


def _risk(args: dict) -> str | None:
    try:
        el = _element(args.get("app", ""), int(args.get("index", 0)))
    except (ToolError, ValueError, TypeError):
        return None
    label = f"{el['label']} {el['id']}"
    if RISKY.search(label):
        return f"Riskli olabilecek bir düğmeye tıklama: '{el['label'] or el['id']}'"
    return None


def _activate(app: str) -> None:
    from .mac_apps import run_osascript

    run_osascript(f'tell application "System Events" to set frontmost of process "{_q(app)}" to true', timeout=20)
    time.sleep(0.25)


@tool(
    "ui_click",
    "ui_elements listesindeki bir öğeye (numarasıyla) tıklar. Uygulamayı öne getirir ve öğenin kesin merkezine tıklar.",
    {"app": {"type": "string"}, "index": {"type": "integer"}, "double": {"type": "boolean"}},
    ["app", "index"],
    risk=_risk,
)
def ui_click(app: str, index: int, double: bool = False) -> str:
    from . import computer

    el = _element(app, index)
    if el.get("ref") is not None and not double:
        import ApplicationServices as AS

        if AS.AXUIElementPerformAction(el["ref"], "AXPress") == 0:
            return f"Basıldı: [{index}] {el['label'] or el['id']}"
    _activate(app)
    gui = computer._gui()
    gui.moveTo(el["cx"], el["cy"], duration=0.1)
    gui.click(clicks=2 if double else 1, interval=0.08)
    return f"Tıklandı: [{index}] {el['label'] or el['id']}"


@tool(
    "ui_type",
    "ui_elements listesindeki bir metin alanına (numarasıyla) tıklayıp yazar. clear=true önce alanı temizler.",
    {"app": {"type": "string"}, "index": {"type": "integer"}, "text": {"type": "string"}, "clear": {"type": "boolean"}, "enter": {"type": "boolean"}},
    ["app", "index", "text"],
)
def ui_type(app: str, index: int, text: str, clear: bool = False, enter: bool = False) -> str:
    from . import computer

    el = _element(app, index)
    _activate(app)
    gui = computer._gui()
    gui.click(el["cx"], el["cy"])
    if clear:
        gui.hotkey("command", "a")
        gui.press("delete")
    computer._type(text)
    if enter:
        gui.press("enter")
    return f"Yazıldı: [{index}] {el['label'] or el['id']}"
