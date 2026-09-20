"""Per-machine appearance settings: interface scaling and light/dark theme.

Everything chosen here is stored by :mod:`services.local_settings` and stays on
this computer, so the booth laptop and the office desktop can disagree about
scaling while sharing the same church library.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui.theme import (
    PALETTE,
    SCALE_MAXIMUM,
    SCALE_MINIMUM,
    clamp_scale,
    create_icon,
    current_scale,
    current_theme,
    fit_toplevel,
    icon_size,
    modern_button,
    px,
    recommended_scale,
    scale_label,
    ui_font,
)


PRESETS: tuple[tuple[str, float | str], ...] = (
    ("Automatic", "auto"),
    ("90%", 0.90),
    ("100%", 1.00),
    ("110%", 1.10),
    ("125%", 1.25),
    ("150%", 1.50),
    ("175%", 1.75),
)


class AppearanceDialog(tk.Toplevel):
    """Choose the interface scale and theme used on this computer only."""

    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.settings = controller.local_settings

        self.title("Display & Appearance — this computer only")
        self.configure(bg=PALETTE["surface"])
        self.transient(controller)
        fit_toplevel(self, 620, 640, 520, 520)
        self.resizable(True, True)

        self.detected_scale = recommended_scale(self)
        stored_scale = self.settings.ui_scale

        self.theme_var = tk.StringVar(value=current_theme())
        self.automatic_var = tk.BooleanVar(value=stored_scale == "auto")
        self.scale_var = tk.DoubleVar(
            value=self.detected_scale if stored_scale == "auto" else float(stored_scale)
        )
        self.remember_var = tk.BooleanVar(value=self.settings.remember_window_geometry)
        self.update_check_var = tk.BooleanVar(
            value=self.settings.automatically_check_for_updates
        )

        self._build()
        self._sync_scale_state()
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: self.destroy())

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = tk.Frame(self, bg=PALETTE["navy"], padx=px(18), pady=px(12))
        header.grid(row=0, column=0, sticky="ew")
        icon = create_icon(header, "display", icon_size(22), PALETTE["primary"])
        tk.Label(header, image=icon, bg=PALETTE["navy"]).pack(side="left", padx=(0, px(10)))
        header._icon = icon
        copy = tk.Frame(header, bg=PALETTE["navy"])
        copy.pack(side="left", fill="y")
        tk.Label(
            copy,
            text="Display & Appearance",
            font=ui_font(13, "bold"),
            bg=PALETTE["navy"],
            fg=PALETTE["navy_text"],
        ).pack(anchor="w")
        tk.Label(
            copy,
            text="Saved on this computer only — never synced to your church.",
            font=ui_font(8),
            bg=PALETTE["navy"],
            fg=PALETTE["navy_muted"],
        ).pack(anchor="w")

        body = tk.Frame(self, bg=PALETTE["surface"], padx=px(18), pady=px(14))
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=1)

        self._build_theme_section(body).grid(row=0, column=0, sticky="ew")
        self._build_scale_section(body).grid(row=1, column=0, sticky="ew", pady=(px(12), 0))
        self._build_preview_section(body).grid(row=2, column=0, sticky="nsew", pady=(px(12), 0))

        actions = tk.Frame(self, bg=PALETTE["surface_subtle"], padx=px(18), pady=px(10))
        actions.grid(row=2, column=0, sticky="ew")
        tk.Label(
            actions,
            text="Changes rebuild the workspace; unsaved song edits are kept.",
            bg=PALETTE["surface_subtle"],
            fg=PALETTE["muted"],
            font=ui_font(8),
        ).pack(side="left")
        modern_button(
            actions,
            "Apply",
            self._apply,
            color=PALETTE["success"],
            hover_color=PALETTE["success_hover"],
            icon="check",
            compact=True,
        ).pack(side="right")
        modern_button(
            actions,
            "Cancel",
            self.destroy,
            color=PALETTE["neutral"],
            hover_color=PALETTE["neutral_hover"],
            icon="clear",
            compact=True,
        ).pack(side="right", padx=(0, px(6)))
        modern_button(
            actions,
            "Reset to detected",
            self._reset,
            color=PALETTE["neutral"],
            hover_color=PALETTE["neutral_hover"],
            icon="refresh",
            compact=True,
        ).pack(side="right", padx=(0, px(6)))

    def _card(self, parent, title, subtitle):
        card = tk.Frame(
            parent,
            bg=PALETTE["surface"],
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
        )
        head = tk.Frame(card, bg=PALETTE["surface_subtle"], padx=px(12), pady=px(8))
        head.pack(fill="x")
        tk.Label(
            head,
            text=title,
            bg=PALETTE["surface_subtle"],
            fg=PALETTE["text"],
            font=ui_font(9, "bold"),
        ).pack(side="left")
        tk.Label(
            head,
            text=subtitle,
            bg=PALETTE["surface_subtle"],
            fg=PALETTE["muted"],
            font=ui_font(8),
        ).pack(side="right")
        return card

    def _build_theme_section(self, parent):
        card = self._card(parent, "THEME", "Applies to the control window only")
        body = tk.Frame(card, bg=PALETTE["surface"], padx=px(12), pady=px(10))
        body.pack(fill="x")
        for value, label, blurb in (
            ("light", "Light", "Best for bright rooms and daylight rehearsals."),
            ("dark", "Dark", "Easier on the eyes in a darkened auditorium."),
        ):
            row = tk.Frame(body, bg=PALETTE["surface"])
            row.pack(fill="x", pady=px(2))
            ttk.Radiobutton(
                row,
                text=label,
                value=value,
                variable=self.theme_var,
                command=self._sync_preview,
            ).pack(side="left")
            tk.Label(
                row,
                text=blurb,
                bg=PALETTE["surface"],
                fg=PALETTE["muted"],
                font=ui_font(8),
            ).pack(side="left", padx=(px(10), 0))
        tk.Label(
            body,
            text="Live View and Stage View keep their own colours from Live Style and Stage Style.",
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            font=ui_font(8),
            wraplength=px(520),
            justify="left",
        ).pack(anchor="w", pady=(px(6), 0))
        return card

    def _build_scale_section(self, parent):
        try:
            screen = f"{self.winfo_screenwidth()} x {self.winfo_screenheight()}"
        except tk.TclError:
            screen = "unknown"
        card = self._card(parent, "INTERFACE SCALING", f"This screen: {screen}")
        body = tk.Frame(card, bg=PALETTE["surface"], padx=px(12), pady=px(10))
        body.pack(fill="x")
        body.grid_columnconfigure(1, weight=1)

        ttk.Checkbutton(
            body,
            text=f"Match this screen automatically (detected {scale_label(self.detected_scale)})",
            variable=self.automatic_var,
            command=self._sync_scale_state,
        ).grid(row=0, column=0, columnspan=3, sticky="w")

        tk.Label(
            body,
            text="Scale",
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            font=ui_font(9),
        ).grid(row=1, column=0, sticky="w", pady=(px(8), 0))
        self.slider = ttk.Scale(
            body,
            from_=SCALE_MINIMUM,
            to=SCALE_MAXIMUM,
            orient="horizontal",
            variable=self.scale_var,
            command=lambda _value: self._on_slider(),
        )
        self.slider.grid(row=1, column=1, sticky="ew", padx=px(10), pady=(px(8), 0))
        self.scale_readout = tk.Label(
            body,
            text=scale_label(self.scale_var.get()),
            bg=PALETTE["surface"],
            fg=PALETTE["primary"],
            font=ui_font(11, "bold"),
            width=6,
            anchor="e",
        )
        self.scale_readout.grid(row=1, column=2, sticky="e", pady=(px(8), 0))

        presets = tk.Frame(body, bg=PALETTE["surface"])
        presets.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(px(8), 0))
        self.preset_buttons: list[tk.Button] = []
        for label, value in PRESETS:
            button = tk.Button(
                presets,
                text=label,
                command=lambda v=value: self._apply_preset(v),
                relief="flat",
                borderwidth=0,
                highlightthickness=1,
                highlightbackground=PALETTE["border"],
                bg=PALETTE["surface_subtle"],
                fg=PALETTE["text"],
                activebackground=PALETTE["primary_soft"],
                activeforeground=PALETTE["primary_soft_text"],
                disabledforeground=PALETTE["disabled_fg"],
                font=ui_font(8, "bold"),
                padx=px(9),
                pady=px(4),
                cursor="hand2",
            )
            button.pack(side="left", padx=(0, px(5)))
            self.preset_buttons.append(button)

        ttk.Checkbutton(
            body,
            text="Reopen on the same monitor and window size next time",
            variable=self.remember_var,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(px(10), 0))
        ttk.Checkbutton(
            body,
            text="Automatically check GitHub for Stage Cue updates",
            variable=self.update_check_var,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(px(6), 0))
        return card

    def _build_preview_section(self, parent):
        card = self._card(parent, "PREVIEW", "Approximate — exact sizes apply after Apply")
        self.preview_body = tk.Frame(card, bg=PALETTE["surface"], padx=px(12), pady=px(10))
        self.preview_body.pack(fill="both", expand=True)
        self.preview_widgets: list[tuple[tk.Widget, str]] = []

        self.preview_title = tk.Label(
            self.preview_body, text="SERVICE AGENDA", anchor="w", font=ui_font(9, "bold")
        )
        self.preview_title.pack(fill="x")
        self.preview_subtitle = tk.Label(
            self.preview_body,
            text="Saved service plan · 3 items",
            anchor="w",
            font=ui_font(8),
        )
        self.preview_subtitle.pack(fill="x")
        self.preview_body_text = tk.Label(
            self.preview_body,
            text="Amazing Grace — Verse 1\nHow Great Thou Art — Chorus",
            justify="left",
            anchor="w",
            font=ui_font(10),
        )
        self.preview_body_text.pack(fill="x", pady=(px(8), 0))
        self.preview_widgets = [
            (self.preview_body, "surface"),
            (self.preview_title, "text"),
            (self.preview_subtitle, "muted"),
            (self.preview_body_text, "text"),
        ]
        self._sync_preview()
        return card

    # ------------------------------------------------------------------
    # Behaviour
    # ------------------------------------------------------------------

    def _sync_scale_state(self) -> None:
        automatic = self.automatic_var.get()
        if automatic:
            self.scale_var.set(self.detected_scale)
        state = "disabled" if automatic else "normal"
        self.slider.state(["disabled"] if automatic else ["!disabled"])
        for button in self.preset_buttons[1:]:
            button.configure(state=state)
        self._on_slider()

    def _on_slider(self) -> None:
        value = clamp_scale(self.scale_var.get())
        self.scale_readout.configure(text=scale_label(value))
        self._sync_preview()

    def _apply_preset(self, value) -> None:
        if value == "auto":
            self.automatic_var.set(True)
            self._sync_scale_state()
            return
        self.automatic_var.set(False)
        self.scale_var.set(float(value))
        self._sync_scale_state()

    def _sync_preview(self) -> None:
        from ui.theme import DARK_PALETTE, LIGHT_PALETTE

        palette = DARK_PALETTE if self.theme_var.get() == "dark" else LIGHT_PALETTE
        factor = clamp_scale(self.scale_var.get())

        def sized(points: float, weight: str | None = None):
            size = max(6, int(round(points * factor)))
            family = ui_font(points)[0]
            return (family, size, weight) if weight else (family, size)

        self.preview_body.configure(bg=palette["surface"])
        self.preview_title.configure(
            bg=palette["surface"], fg=palette["text"], font=sized(9, "bold")
        )
        self.preview_subtitle.configure(
            bg=palette["surface"], fg=palette["muted"], font=sized(8)
        )
        self.preview_body_text.configure(
            bg=palette["surface"], fg=palette["text"], font=sized(10)
        )

    def _reset(self) -> None:
        self.theme_var.set("light")
        self.automatic_var.set(True)
        self.remember_var.set(True)
        self.update_check_var.set(True)
        self._sync_scale_state()
        self._sync_preview()

    def _apply(self) -> None:
        theme = self.theme_var.get()
        scale: str | float = "auto" if self.automatic_var.get() else clamp_scale(
            self.scale_var.get()
        )
        remember = bool(self.remember_var.get())
        automatic_updates = bool(self.update_check_var.get())
        self.settings.automatically_check_for_updates = automatic_updates
        unchanged = (
            theme == current_theme()
            and self.settings.ui_scale == scale
            and remember == self.settings.remember_window_geometry
        )
        self.destroy()
        if unchanged:
            return
        self.controller.apply_appearance(
            theme_name=theme, ui_scale=scale, remember_geometry=remember
        )
