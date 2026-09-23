"""Colour palettes, scaling helpers, and shared widgets.

Two things in this module are global on purpose.

``PALETTE`` is a plain dict that every page imports by reference and is
*mutated in place* by :func:`set_theme`, so switching to dark mode does not
require every module to re-import anything.  Widgets bake their colours in at
construction time, so the application rebuilds its pages after a change rather
than trying to recolour a live widget tree.

The scale factor works the same way.  Tk is pinned to a fixed 96 DPI baseline
in :func:`apply_to_root` so a point size means the same number of pixels on
every machine; :func:`px` and :func:`ui_font` then apply the operator's chosen
factor to padding and text alike.  Without the pin, a high-DPI screen grows the
fonts but not the paddings, which is exactly what makes panels clip.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Iterable, Mapping

from PIL import Image, ImageDraw, ImageTk


# ----------------------------------------------------------------------
# Palettes
# ----------------------------------------------------------------------

LIGHT_PALETTE: dict[str, str] = {
    # Surfaces
    "canvas": "#E2E8F0",
    "surface": "#FFFFFF",
    "surface_subtle": "#F8FAFC",
    "surface_muted": "#F1F5F9",
    "rail": "#E9EFF6",
    "sash": "#B8C5D5",
    "matte": "#111827",
    # Chrome
    "navy": "#07111F",
    "navy_text": "#E5E7EB",
    "navy_muted": "#9CA3AF",
    "toolbar": "#111827",
    "toolbar_border": "#1E293B",
    "toolbar_label": "#94A3B8",
    "statusbar": "#0B1220",
    "statusbar_text": "#D1D5DB",
    "statusbar_accent": "#6BDEB6",
    # Lines and text
    "border": "#CFD9E6",
    "border_strong": "#CBD5E1",
    "divider": "#E2E8F0",
    "text": "#0F172A",
    "text_inverse": "#FFFFFF",
    "muted": "#64748B",
    "subtle_text": "#374151",
    # Inputs
    "input_bg": "#FFFFFF",
    "input_fg": "#0F172A",
    "editor_bg": "#FFFFFF",
    "editor_fg": "#0F172A",
    "select_bg": "#DBEAFE",
    "select_fg": "#1E3A8A",
    "list_bg": "#FFFFFF",
    # Accents
    "primary": "#2563EB",
    "primary_hover": "#1D4ED8",
    "primary_soft": "#EFF6FF",
    "primary_soft_text": "#1E40AF",
    "success": "#059669",
    "success_hover": "#047857",
    "danger": "#DC2626",
    "danger_hover": "#B91C1C",
    "purple": "#7C3AED",
    "purple_hover": "#6D28D9",
    "purple_soft": "#F5F3FF",
    "purple_soft_text": "#5B21B6",
    "neutral": "#475569",
    "neutral_hover": "#334155",
    "warning": "#D97706",
    "teal": "#0F766E",
    # Status badges
    "badge_idle_bg": "#EEF2F7",
    "badge_idle_fg": "#475569",
    "badge_live_bg": "#DCFCE7",
    "badge_live_fg": "#166534",
    "badge_frozen_bg": "#FEF3C7",
    "badge_frozen_fg": "#92400E",
    # Misc
    "tooltip_bg": "#111827",
    "tooltip_fg": "#F8FAFC",
    "disabled_fg": "#CBD5E1",
    "heading_subtitle": "#DCEBFA",
}

DARK_PALETTE: dict[str, str] = {
    # Surfaces
    "canvas": "#0B1220",
    "surface": "#161E2E",
    "surface_subtle": "#1B2436",
    "surface_muted": "#1F293C",
    "rail": "#1B2436",
    "sash": "#2A3446",
    "matte": "#05090F",
    # Chrome
    "navy": "#05090F",
    "navy_text": "#E6EDF7",
    "navy_muted": "#8FA0B8",
    "toolbar": "#0E1626",
    "toolbar_border": "#243049",
    "toolbar_label": "#8FA0B8",
    "statusbar": "#05090F",
    "statusbar_text": "#C7D2E1",
    "statusbar_accent": "#34D399",
    # Lines and text
    "border": "#2A3446",
    "border_strong": "#38455C",
    "divider": "#243049",
    "text": "#E6EDF7",
    "text_inverse": "#FFFFFF",
    "muted": "#94A3B8",
    "subtle_text": "#AFBDD0",
    # Inputs
    "input_bg": "#0F172A",
    "input_fg": "#E6EDF7",
    "editor_bg": "#0F172A",
    "editor_fg": "#E6EDF7",
    "select_bg": "#1E3A5F",
    "select_fg": "#DBEAFE",
    "list_bg": "#0F172A",
    # Accents
    "primary": "#3B82F6",
    "primary_hover": "#2563EB",
    "primary_soft": "#16243D",
    "primary_soft_text": "#93C5FD",
    "success": "#10B981",
    "success_hover": "#059669",
    "danger": "#EF4444",
    "danger_hover": "#DC2626",
    "purple": "#8B5CF6",
    "purple_hover": "#7C3AED",
    "purple_soft": "#221B3A",
    "purple_soft_text": "#C4B5FD",
    "neutral": "#4B5A70",
    "neutral_hover": "#5D6E86",
    "warning": "#F59E0B",
    "teal": "#14B8A6",
    # Status badges
    "badge_idle_bg": "#1F293C",
    "badge_idle_fg": "#AFBDD0",
    "badge_live_bg": "#0C3A2A",
    "badge_live_fg": "#6EE7B7",
    "badge_frozen_bg": "#42320C",
    "badge_frozen_fg": "#FCD34D",
    # Misc
    "tooltip_bg": "#E2E8F0",
    "tooltip_fg": "#0F172A",
    "disabled_fg": "#5D6E86",
    "heading_subtitle": "#E0E7FF",
}

PALETTE: dict[str, str] = dict(LIGHT_PALETTE)


# ----------------------------------------------------------------------
# Scale and font state
# ----------------------------------------------------------------------

UI_FONT_CANDIDATES = (
    "Segoe UI",
    "Inter",
    "Ubuntu",
    "Noto Sans",
    "DejaVu Sans",
    "Helvetica",
)
MONO_FONT_CANDIDATES = (
    "Cascadia Mono",
    "Consolas",
    "JetBrains Mono",
    "Ubuntu Mono",
    "DejaVu Sans Mono",
    "Courier New",
)

SCALE_MINIMUM = 0.75
SCALE_MAXIMUM = 2.00

_state: dict[str, object] = {
    "theme": "light",
    "scale": 1.0,
    "ui_family": "Segoe UI",
    "mono_family": "Courier New",
    # The monitor's real DPI, read once before apply_to_root() pins Tk's
    # scaling. After that pin winfo_fpixels("1i") always answers 96, so
    # re-reading it later would make auto-detection drift down to 100%.
    "native_dpi": None,
}


def current_theme() -> str:
    return str(_state["theme"])


def current_scale() -> float:
    return float(_state["scale"])


def is_dark() -> bool:
    return current_theme() == "dark"


def set_theme(name: str) -> str:
    """Swap the palette contents in place so existing imports stay valid."""

    resolved = "dark" if str(name).lower() == "dark" else "light"
    source = DARK_PALETTE if resolved == "dark" else LIGHT_PALETTE
    PALETTE.clear()
    PALETTE.update(source)
    _state["theme"] = resolved
    return resolved


def clamp_scale(value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 1.0
    number = max(SCALE_MINIMUM, min(SCALE_MAXIMUM, number))
    return round(number * 20) / 20


def set_scale(value: float) -> float:
    _state["scale"] = clamp_scale(value)
    return current_scale()


def px(value: float) -> int:
    """Scale a pixel measurement, never collapsing a visible gap to zero."""

    scaled = round(value * current_scale())
    if value > 0:
        return max(1, int(scaled))
    return int(scaled)


def pad(*values: float):
    """Scale a padx/pady argument, accepting either a scalar or a pair."""

    scaled = tuple(px(value) for value in values)
    return scaled[0] if len(scaled) == 1 else scaled


def font_size(points: float) -> int:
    return max(6, int(round(points * current_scale())))


def ui_font(points: float, weight: str | None = None) -> tuple:
    family = str(_state["ui_family"])
    if weight:
        return (family, font_size(points), weight)
    return (family, font_size(points))


def mono_font(points: float, weight: str | None = None) -> tuple:
    family = str(_state["mono_family"])
    if weight:
        return (family, font_size(points), weight)
    return (family, font_size(points))


def icon_size(points: float) -> int:
    return max(10, int(round(points * current_scale())))


def _first_available(root: tk.Misc, candidates: Iterable[str], fallback: str) -> str:
    try:
        installed = {name.casefold() for name in tkfont.families(root)}
    except tk.TclError:
        return fallback
    for name in candidates:
        if name.casefold() in installed:
            return name
    return fallback


def capture_native_dpi(root: tk.Misc) -> float:
    """Record the monitor's true DPI, before Tk's scaling gets pinned."""

    cached = _state.get("native_dpi")
    if cached is None:
        try:
            cached = float(root.winfo_fpixels("1i"))
        except (tk.TclError, ValueError):
            cached = 96.0
        if cached <= 1:
            cached = 96.0
        _state["native_dpi"] = cached
    return float(cached)


def resolve_font_families(root: tk.Misc) -> None:
    # Runs before apply_to_root(), which is the only safe moment to read DPI.
    capture_native_dpi(root)
    _state["ui_family"] = _first_available(root, UI_FONT_CANDIDATES, "Helvetica")
    _state["mono_family"] = _first_available(root, MONO_FONT_CANDIDATES, "Courier")


def recommended_scale(root: tk.Misc) -> float:
    """Derive a sensible factor from the monitor Tk is currently reporting."""

    dots_per_inch = capture_native_dpi(root)
    factor = dots_per_inch / 96.0

    try:
        screen_height = int(root.winfo_screenheight())
        screen_width = int(root.winfo_screenwidth())
    except tk.TclError:
        screen_height = screen_width = 0

    # Tk under-reports DPI on a lot of Linux setups, so fall back to raw pixels
    # when it claims a plain 96 DPI on an obviously high-resolution panel.
    if abs(factor - 1.0) < 0.02 and screen_height:
        if screen_height >= 2000:
            factor = 1.50
        elif screen_height >= 1400:
            factor = 1.25
        elif screen_height >= 1150:
            factor = 1.10

    # The workspace needs roughly 760x1180 logical pixels before panels have to
    # start giving up content, so shrink rather than clip on small panels.
    if screen_height:
        factor = min(factor, screen_height / 760)
    if screen_width:
        factor = min(factor, screen_width / 1180)

    return clamp_scale(factor)


def scale_label(value: float) -> str:
    return f"{int(round(value * 100))}%"


# ----------------------------------------------------------------------
# Root configuration
# ----------------------------------------------------------------------

def apply_to_root(root: tk.Misc) -> None:
    """Pin Tk's DPI baseline, then restyle named fonts and every ttk widget."""

    from tkinter import ttk

    try:
        # 96 DPI over 72 points per inch. Pinning this makes a point size mean
        # the same pixel height on every machine, so our factor is the only
        # thing that changes size.
        root.tk.call("tk", "scaling", 96.0 / 72.0)
    except tk.TclError:
        pass

    family = str(_state["ui_family"])
    base = font_size(10)
    for name, size in (
        ("TkDefaultFont", base),
        ("TkTextFont", base),
        ("TkMenuFont", base),
        ("TkHeadingFont", font_size(10)),
        ("TkTooltipFont", font_size(9)),
        ("TkIconFont", base),
        ("TkSmallCaptionFont", font_size(9)),
    ):
        try:
            tkfont.nametofont(name, root=root).configure(family=family, size=size)
        except tk.TclError:
            pass
    try:
        tkfont.nametofont("TkFixedFont", root=root).configure(
            family=str(_state["mono_family"]), size=base
        )
    except tk.TclError:
        pass

    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    style.configure(".", background=PALETTE["surface"], foreground=PALETTE["text"])
    style.configure(
        "TNotebook",
        background=PALETTE["surface_muted"],
        borderwidth=0,
        tabmargins=(0, px(4), 0, 0),
    )
    style.configure(
        "TNotebook.Tab",
        background=PALETTE["surface_muted"],
        foreground=PALETTE["muted"],
        padding=(px(14), px(7)),
        borderwidth=0,
        font=ui_font(9, "bold"),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", PALETTE["surface"]), ("active", PALETTE["primary_soft"])],
        foreground=[
            ("selected", PALETTE["primary"]),
            ("active", PALETTE["primary_soft_text"]),
        ],
    )
    style.configure(
        "TCombobox",
        fieldbackground=PALETTE["input_bg"],
        background=PALETTE["surface_subtle"],
        foreground=PALETTE["input_fg"],
        arrowcolor=PALETTE["muted"],
        bordercolor=PALETTE["border_strong"],
        lightcolor=PALETTE["border_strong"],
        darkcolor=PALETTE["border_strong"],
        padding=px(4),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", PALETTE["input_bg"])],
        foreground=[("readonly", PALETTE["input_fg"]), ("disabled", PALETTE["disabled_fg"])],
        selectbackground=[("readonly", PALETTE["input_bg"])],
        selectforeground=[("readonly", PALETTE["input_fg"])],
    )
    for option, value in (
        ("*TCombobox*Listbox.background", PALETTE["input_bg"]),
        ("*TCombobox*Listbox.foreground", PALETTE["input_fg"]),
        ("*TCombobox*Listbox.selectBackground", PALETTE["select_bg"]),
        ("*TCombobox*Listbox.selectForeground", PALETTE["select_fg"]),
        # Fallbacks for classic Tk widgets that do not set their own colours.
        # Explicit widget options always win, so this only fills in the gaps —
        # listboxes, entries, and checkbuttons inside dialogs, and Tk's own
        # message boxes on platforms where they are built from Tk widgets.
        ("*Listbox.background", PALETTE["list_bg"]),
        ("*Listbox.foreground", PALETTE["text"]),
        ("*Listbox.selectBackground", PALETTE["select_bg"]),
        ("*Listbox.selectForeground", PALETTE["select_fg"]),
        ("*Listbox.highlightBackground", PALETTE["border"]),
        ("*Entry.background", PALETTE["input_bg"]),
        ("*Entry.foreground", PALETTE["input_fg"]),
        ("*Entry.insertBackground", PALETTE["primary"]),
        ("*Entry.selectBackground", PALETTE["select_bg"]),
        ("*Entry.selectForeground", PALETTE["select_fg"]),
        ("*Text.background", PALETTE["editor_bg"]),
        ("*Text.foreground", PALETTE["editor_fg"]),
        ("*Text.insertBackground", PALETTE["primary"]),
        ("*Text.selectBackground", PALETTE["select_bg"]),
        ("*Text.selectForeground", PALETTE["select_fg"]),
        ("*Label.foreground", PALETTE["text"]),
        ("*Labelframe.foreground", PALETTE["text"]),
        ("*Checkbutton.foreground", PALETTE["text"]),
        ("*Checkbutton.selectColor", PALETTE["input_bg"]),
        ("*Checkbutton.activeBackground", PALETTE["surface"]),
        ("*Checkbutton.activeForeground", PALETTE["text"]),
        ("*Radiobutton.foreground", PALETTE["text"]),
        ("*Radiobutton.selectColor", PALETTE["input_bg"]),
        ("*Radiobutton.activeBackground", PALETTE["surface"]),
        ("*Radiobutton.activeForeground", PALETTE["text"]),
        ("*Menu.background", PALETTE["surface"]),
        ("*Menu.foreground", PALETTE["text"]),
        ("*Menu.activeBackground", PALETTE["select_bg"]),
        ("*Menu.activeForeground", PALETTE["select_fg"]),
        ("*Toplevel.background", PALETTE["surface"]),
        ("*Dialog.msg.background", PALETTE["surface"]),
        ("*Dialog.msg.foreground", PALETTE["text"]),
    ):
        try:
            root.option_add(option, value)
        except tk.TclError:
            pass
    style.configure(
        "TSpinbox",
        fieldbackground=PALETTE["input_bg"],
        foreground=PALETTE["input_fg"],
        arrowcolor=PALETTE["muted"],
        bordercolor=PALETTE["border_strong"],
        padding=px(3),
    )
    style.configure(
        "TScale",
        background=PALETTE["surface"],
        troughcolor=PALETTE["surface_muted"],
    )
    for orientation in ("Vertical", "Horizontal"):
        style.configure(
            f"{orientation}.TScrollbar",
            background=PALETTE["border_strong"],
            troughcolor=PALETTE["surface_muted"],
            bordercolor=PALETTE["surface_muted"],
            arrowcolor=PALETTE["muted"],
        )
        style.map(
            f"{orientation}.TScrollbar",
            background=[("active", PALETTE["muted"])],
        )
    style.configure(
        "StageCue.Treeview",
        rowheight=px(28),
        font=ui_font(9),
        background=PALETTE["list_bg"],
        fieldbackground=PALETTE["list_bg"],
        foreground=PALETTE["text"],
        borderwidth=0,
    )
    style.map(
        "StageCue.Treeview",
        background=[("selected", PALETTE["select_bg"])],
        foreground=[("selected", PALETTE["select_fg"])],
    )
    style.configure("TFrame", background=PALETTE["surface"])
    style.configure("TLabel", background=PALETTE["surface"], foreground=PALETTE["text"])
    style.configure(
        "TLabelframe",
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
        bordercolor=PALETTE["border"],
    )
    style.configure(
        "TLabelframe.Label",
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
    )
    style.configure(
        "TCheckbutton",
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
    )
    style.map(
        "TCheckbutton",
        background=[("active", PALETTE["surface"])],
        foreground=[("disabled", PALETTE["disabled_fg"])],
    )
    style.configure(
        "TRadiobutton",
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
    )
    style.map("TRadiobutton", background=[("active", PALETTE["surface"])])


# ----------------------------------------------------------------------
# Window sizing
# ----------------------------------------------------------------------

def fit_toplevel(
    window: tk.Toplevel,
    desired_width: int,
    desired_height: int,
    minimum_width: int = 480,
    minimum_height: int = 360,
) -> tuple[int, int]:
    """Centre a dialog at the current scale without pushing it off-screen."""

    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    width = min(px(desired_width), max(px(420), screen_width - px(48)))
    height = min(px(desired_height), max(px(340), screen_height - px(96)))
    x = max(0, (screen_width - width) // 2)
    y = max(0, (screen_height - height) // 2 - px(8))
    window.geometry(f"{width}x{height}+{x}+{y}")
    window.minsize(min(px(minimum_width), width), min(px(minimum_height), height))
    return width, height


# ----------------------------------------------------------------------
# Icons
# ----------------------------------------------------------------------

def _icon_cache(master: tk.Misc) -> dict[tuple[str, int, str], ImageTk.PhotoImage]:
    root = master.winfo_toplevel()
    cache = getattr(root, "_stagecue_icons", None)
    if cache is None:
        cache = {}
        root._stagecue_icons = cache
    return cache


def create_icon(
    master: tk.Misc,
    name: str,
    size: int = 16,
    color: str | None = None,
) -> ImageTk.PhotoImage:
    """Render a small antialiased icon without platform-specific fonts."""

    color = color or "#FFFFFF"
    key = (name, size, color)
    cache = _icon_cache(master)
    if key in cache:
        return cache[key]

    scale = 4
    side = size * scale
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    stroke = max(4, scale * 2)
    c = color

    def line(points, width=stroke):
        draw.line([(x * scale, y * scale) for x, y in points], fill=c, width=width, joint="curve")

    def box(coords, width=stroke, radius=2):
        draw.rounded_rectangle(
            tuple(value * scale for value in coords),
            radius=radius * scale,
            outline=c,
            width=width,
        )

    if name == "play":
        draw.polygon(
            [(5 * scale, 3 * scale), (13 * scale, 8 * scale), (5 * scale, 13 * scale)],
            fill=c,
        )
    elif name == "freeze":
        line([(8, 2), (8, 14)])
        line([(3, 5), (13, 11)])
        line([(3, 11), (13, 5)])
    elif name == "hide":
        draw.ellipse((2 * scale, 5 * scale, 14 * scale, 11 * scale), outline=c, width=stroke)
        draw.ellipse((7 * scale, 7 * scale, 9 * scale, 9 * scale), fill=c)
        line([(2, 14), (14, 2)], width=stroke + 1)
    elif name in {"logo", "image"}:
        box((2, 3, 14, 13))
        draw.ellipse((4 * scale, 5 * scale, 6 * scale, 7 * scale), fill=c)
        line([(3, 12), (7, 8), (9, 10), (11, 7), (14, 11)], width=stroke - 2)
    elif name == "clear":
        box((3, 3, 13, 13), radius=3)
        line([(5, 5), (11, 11)])
        line([(11, 5), (5, 11)])
    elif name == "message":
        box((2, 2, 14, 11), radius=3)
        line([(6, 11), (5, 14), (9, 11)], width=stroke - 2)
        line([(5, 6), (11, 6)], width=stroke - 2)
    elif name == "church":
        line([(2, 7), (8, 2), (14, 7)])
        box((4, 7, 12, 14), radius=1)
        line([(8, 1), (8, 5)], width=stroke - 2)
        line([(6, 3), (10, 3)], width=stroke - 2)
    elif name == "users":
        draw.ellipse((3 * scale, 2 * scale, 8 * scale, 7 * scale), outline=c, width=stroke)
        draw.ellipse((9 * scale, 4 * scale, 13 * scale, 8 * scale), outline=c, width=stroke - 2)
        line([(2, 14), (3, 10), (8, 9), (11, 14)])
        line([(10, 10), (13, 10), (15, 14)], width=stroke - 2)
    elif name == "palette":
        draw.ellipse((2 * scale, 2 * scale, 14 * scale, 14 * scale), outline=c, width=stroke)
        for x, y in ((6, 5), (10, 5), (5, 9)):
            draw.ellipse(((x - 1) * scale, (y - 1) * scale, (x + 1) * scale, (y + 1) * scale), fill=c)
    elif name == "monitor":
        box((2, 2, 14, 11))
        line([(8, 11), (8, 14)], width=stroke - 2)
        line([(5, 14), (11, 14)], width=stroke - 2)
    elif name == "keyboard":
        box((1, 3, 15, 13))
        for x in (4, 7, 10, 13):
            line([(x, 6), (x + 1, 6)], width=stroke - 2)
        line([(4, 10), (12, 10)], width=stroke - 2)
    elif name == "logout":
        box((2, 2, 9, 14))
        line([(7, 8), (15, 8)])
        line([(12, 5), (15, 8), (12, 11)])
    elif name == "plus":
        draw.ellipse((2 * scale, 2 * scale, 14 * scale, 14 * scale), outline=c, width=stroke)
        line([(8, 5), (8, 11)])
        line([(5, 8), (11, 8)])
    elif name == "import":
        box((2, 2, 14, 14))
        line([(8, 3), (8, 10)])
        line([(5, 7), (8, 10), (11, 7)])
    elif name == "save":
        box((2, 2, 14, 14))
        box((5, 2, 11, 6), width=stroke - 2, radius=1)
        draw.ellipse((6 * scale, 9 * scale, 10 * scale, 13 * scale), outline=c, width=stroke - 2)
    elif name == "folder":
        draw.rounded_rectangle(
            (1 * scale, 5 * scale, 15 * scale, 14 * scale),
            radius=2 * scale,
            outline=c,
            width=stroke,
        )
        line([(2, 5), (4, 2), (8, 2), (10, 5)], width=stroke - 2)
    elif name == "trash":
        box((4, 5, 12, 14), radius=1)
        line([(3, 4), (13, 4)])
        line([(6, 2), (10, 2)], width=stroke - 2)
    elif name == "edit":
        line([(3, 13), (5, 9), (11, 3), (14, 6), (8, 12), (3, 13)])
    elif name == "refresh":
        draw.arc((2 * scale, 2 * scale, 14 * scale, 14 * scale), 35, 320, fill=c, width=stroke)
        draw.polygon([(12 * scale, 2 * scale), (15 * scale, 3 * scale), (13 * scale, 6 * scale)], fill=c)
    elif name == "account":
        draw.ellipse((5 * scale, 2 * scale, 11 * scale, 8 * scale), outline=c, width=stroke)
        draw.arc((2 * scale, 7 * scale, 14 * scale, 17 * scale), 195, 345, fill=c, width=stroke)
    elif name == "check":
        line([(2, 8), (6, 12), (14, 3)])
    elif name == "back":
        line([(13, 3), (5, 8), (13, 13)])
    elif name == "forward":
        line([(3, 3), (11, 8), (3, 13)])
    elif name == "arrow_up":
        line([(3, 10), (8, 5), (13, 10)])
    elif name == "arrow_down":
        line([(3, 6), (8, 11), (13, 6)])
    elif name == "align_top":
        line([(2, 3), (14, 3)])
        line([(8, 6), (8, 14)])
        line([(5, 9), (8, 6), (11, 9)])
    elif name == "align_bottom":
        line([(2, 13), (14, 13)])
        line([(8, 2), (8, 10)])
        line([(5, 7), (8, 10), (11, 7)])
    elif name == "align_left":
        line([(3, 2), (3, 14)])
        line([(6, 8), (14, 8)])
        line([(9, 5), (6, 8), (9, 11)])
    elif name == "align_right":
        line([(13, 2), (13, 14)])
        line([(2, 8), (10, 8)])
        line([(7, 5), (10, 8), (7, 11)])
    elif name == "align_center":
        line([(2, 8), (14, 8)])
        line([(8, 2), (8, 14)])
        draw.ellipse((6 * scale, 6 * scale, 10 * scale, 10 * scale), fill=c)
    elif name == "copy":
        box((2, 2, 11, 11), radius=2)
        box((5, 5, 14, 14), radius=2)
    elif name == "admin":
        draw.ellipse((5 * scale, 2 * scale, 11 * scale, 8 * scale), outline=c, width=stroke)
        line([(3, 14), (4, 10), (8, 9), (12, 10), (13, 14)])
        draw.polygon(
            [(11 * scale, 9 * scale), (15 * scale, 10 * scale), (14 * scale, 14 * scale), (11 * scale, 15 * scale)],
            outline=c,
        )
    elif name == "agenda":
        box((2, 3, 14, 14), radius=2)
        line([(2, 7), (14, 7)], width=stroke - 2)
        line([(5, 1), (5, 5)], width=stroke - 2)
        line([(11, 1), (11, 5)], width=stroke - 2)
        line([(5, 10), (7, 12), (11, 9)], width=stroke - 2)
    elif name == "music":
        line([(6, 3), (13, 1), (13, 11)], width=stroke)
        line([(6, 3), (6, 13)], width=stroke)
        line([(6, 6), (13, 4)], width=stroke - 2)
        draw.ellipse((2 * scale, 11 * scale, 7 * scale, 15 * scale), fill=c)
        draw.ellipse((9 * scale, 9 * scale, 14 * scale, 13 * scale), fill=c)
    elif name == "slides":
        box((1, 3, 11, 12), width=stroke - 2, radius=2)
        box((5, 5, 15, 14), radius=2)
        line([(8, 9), (12, 9)], width=stroke - 2)
        line([(8, 12), (11, 12)], width=stroke - 2)
    elif name == "search":
        draw.ellipse((2 * scale, 2 * scale, 11 * scale, 11 * scale), outline=c, width=stroke)
        line([(10, 10), (15, 15)], width=stroke)
    elif name == "sun":
        draw.ellipse((5 * scale, 5 * scale, 11 * scale, 11 * scale), outline=c, width=stroke)
        for start, end in (
            ((8, 1), (8, 3)),
            ((8, 13), (8, 15)),
            ((1, 8), (3, 8)),
            ((13, 8), (15, 8)),
            ((3, 3), (4, 4)),
            ((12, 12), (13, 13)),
            ((3, 13), (4, 12)),
            ((12, 4), (13, 3)),
        ):
            line([start, end], width=stroke - 2)
    elif name == "display":
        box((1, 3, 15, 12), radius=2)
        line([(5, 15), (11, 15)], width=stroke - 2)
        line([(8, 12), (8, 15)], width=stroke - 2)
        line([(4, 6), (7, 6)], width=stroke - 2)
        line([(4, 9), (10, 9)], width=stroke - 2)
    elif name == "zoom":
        draw.ellipse((2 * scale, 2 * scale, 11 * scale, 11 * scale), outline=c, width=stroke)
        line([(10, 10), (15, 15)], width=stroke)
        line([(4, 6), (9, 6)], width=stroke - 2)
        line([(6, 4), (6, 9)], width=stroke - 2)
    elif name.startswith("position_"):
        box((2, 2, 14, 14), width=stroke - 2, radius=2)
        positions = {
            "top_left": (5, 5),
            "top": (8, 5),
            "top_right": (11, 5),
            "left": (5, 8),
            "center": (8, 8),
            "right": (11, 8),
            "bottom_left": (5, 11),
            "bottom": (8, 11),
            "bottom_right": (11, 11),
        }
        x, y = positions.get(name.removeprefix("position_"), positions["center"])
        draw.ellipse(
            ((x - 1.5) * scale, (y - 1.5) * scale, (x + 1.5) * scale, (y + 1.5) * scale),
            fill=c,
        )
    else:
        draw.ellipse((3 * scale, 3 * scale, 13 * scale, 13 * scale), outline=c, width=stroke)

    icon = ImageTk.PhotoImage(
        image.resize((size, size), Image.Resampling.LANCZOS),
        master=master,
    )
    cache[key] = icon
    return icon


def infer_icon(text: str) -> str:
    """Choose a recognizable icon for an action label."""

    lowered = text.casefold()
    rules = (
        (("appearance", "theme", "dark mode"), "palette"),
        (("scaling", "zoom"), "zoom"),
        (("delete", "remove", "discard"), "trash"),
        (("clear", "reject", "cancel"), "clear"),
        (("unfreeze", "freeze"), "freeze"),
        (("hide text", "show text"), "hide"),
        (("custom message", "message", "send"), "message"),
        (("toggle admin", "admin"), "admin"),
        (("membership", "member", "request"), "users"),
        (("church",), "church"),
        (("style", "color", "palette"), "palette"),
        (("copy",), "copy"),
        (("save",), "save"),
        (("load", "choose", "browse"), "folder"),
        (("logo", "image"), "logo"),
        (("output", "live view", "stage view", "monitor", "display"), "monitor"),
        (("shortcut",), "keyboard"),
        (("log out", "logout"), "logout"),
        (("refresh", "reset"), "refresh"),
        (("publish", "import"), "import"),
        (("rename", "edit"), "edit"),
        (("accept", "register", "create account", "apply", "ok"), "check"),
        (("back", "previous"), "back"),
        (("next",), "forward"),
        (("stop presenting", "start presenting", "present", "show selected", "open", "add →"), "play"),
        (("up", "top"), "arrow_up"),
        (("down", "bottom"), "arrow_down"),
        (("new", "add", "book", "song"), "plus"),
    )
    for phrases, icon_name in rules:
        if any(phrase in lowered for phrase in phrases):
            return icon_name
    return "plus"


# ----------------------------------------------------------------------
# Shared widgets
# ----------------------------------------------------------------------

class HoverTooltip:
    """A delayed, keyboard-accessible tooltip for compact controls."""

    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 450):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id = None
        self._window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<FocusIn>", self._schedule, add="+")
        widget.bind("<FocusOut>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text
        if self._window is not None:
            self.hide()

    def _schedule(self, _event=None) -> None:
        self._cancel_pending()
        self._after_id = self.widget.after(self.delay_ms, self.show)

    def _cancel_pending(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def show(self) -> None:
        self._after_id = None
        if self._window is not None or not self.text or not self.widget.winfo_exists():
            return
        try:
            x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + px(8)
        except tk.TclError:
            return
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            window,
            text=self.text,
            bg=PALETTE["tooltip_bg"],
            fg=PALETTE["tooltip_fg"],
            font=ui_font(9),
            justify="left",
            padx=px(9),
            pady=px(6),
            relief="solid",
            borderwidth=1,
            wraplength=px(280),
        )
        label.pack()
        window.update_idletasks()
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        tooltip_width = window.winfo_reqwidth()
        tooltip_height = window.winfo_reqheight()
        safe_x = max(6, min(x - tooltip_width // 2, screen_width - tooltip_width - 6))
        safe_y = (
            self.widget.winfo_rooty() - tooltip_height - px(8)
            if y + tooltip_height > screen_height - 6
            else y
        )
        window.wm_geometry(f"+{safe_x}+{max(6, safe_y)}")
        self._window = window

    def hide(self, _event=None) -> None:
        self._cancel_pending()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


class IconButton(tk.Button):
    """A text-free button whose accessible action label lives in its tooltip."""

    def __init__(self, parent: tk.Misc, *, label: str, **kwargs):
        self._stagecue_label = label
        self._stagecue_base_bg = kwargs.get("bg", kwargs.get("background", PALETTE["neutral"]))
        self._stagecue_hover_bg = kwargs.get("activebackground", self._stagecue_base_bg)
        super().__init__(parent, text="", **kwargs)
        self._stagecue_tooltip = HoverTooltip(self, label)
        self.bind("<Enter>", self._apply_hover, add="+")
        self.bind("<Leave>", self._remove_hover, add="+")

    def _apply_hover(self, _event=None) -> None:
        if str(self.cget("state")) != "disabled":
            super().configure(bg=self._stagecue_hover_bg)

    def _remove_hover(self, _event=None) -> None:
        super().configure(bg=self._stagecue_base_bg)

    def configure(self, cnf: Mapping | None = None, **kwargs):
        if cnf:
            kwargs.update(dict(cnf))
        if "text" in kwargs:
            self._stagecue_label = str(kwargs.pop("text"))
            if hasattr(self, "_stagecue_tooltip"):
                self._stagecue_tooltip.set_text(self._stagecue_label)
        if "bg" in kwargs:
            self._stagecue_base_bg = kwargs["bg"]
        elif "background" in kwargs:
            self._stagecue_base_bg = kwargs["background"]
        if "activebackground" in kwargs:
            self._stagecue_hover_bg = kwargs["activebackground"]
        return super().configure(**kwargs)

    config = configure


def modern_button(
    parent: tk.Misc,
    text: str,
    command: Callable,
    color: str | None = None,
    hover_color: str | None = None,
    icon: str | None = None,
    compact: bool = False,
) -> tk.Button:
    color = color or PALETTE["neutral"]
    icon_name = icon or infer_icon(text)
    photo = create_icon(parent, icon_name, icon_size(15 if compact else 18))
    button = IconButton(
        parent,
        label=text,
        command=command,
        image=photo,
        bg=color,
        fg=PALETTE["text_inverse"],
        activebackground=hover_color or color,
        activeforeground=PALETTE["text_inverse"],
        disabledforeground=PALETTE["disabled_fg"],
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        padx=px(7) if compact else px(11),
        pady=px(4) if compact else px(8),
        cursor="hand2",
        takefocus=True,
    )
    button._stagecue_icon = photo
    return button


def style_entry(entry: tk.Entry) -> tk.Entry:
    entry.configure(
        relief="flat",
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=PALETTE["border"],
        highlightcolor=PALETTE["primary"],
        bg=PALETTE["input_bg"],
        fg=PALETTE["input_fg"],
        insertbackground=PALETTE["primary"],
        disabledbackground=PALETTE["surface_muted"],
        disabledforeground=PALETTE["disabled_fg"],
    )
    return entry


def style_text(widget: tk.Text) -> tk.Text:
    widget.configure(
        bg=PALETTE["editor_bg"],
        fg=PALETTE["editor_fg"],
        insertbackground=PALETTE["primary"],
        selectbackground=PALETTE["select_bg"],
        selectforeground=PALETTE["select_fg"],
        relief="flat",
        highlightthickness=1,
        highlightbackground=PALETTE["border_strong"],
        highlightcolor=PALETTE["primary"],
    )
    return widget
