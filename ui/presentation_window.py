from __future__ import annotations

import tkinter as tk

from screeninfo import get_monitors

from ui.presentation_canvas import PresentationCanvasRenderer


class PresentationWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.withdraw()
        self.configure(bg="black")
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.canvas = tk.Canvas(self, bg="black", bd=0, highlightthickness=0)
        self.canvas.place(relx=0.5, rely=0.5, anchor="center")
        self.renderer = PresentationCanvasRenderer(self.canvas)
        self._opened = False
        self.bind("<Configure>", self._layout_canvas, add="+")

    def open_on_output_display(self) -> bool:
        try:
            monitors = list(get_monitors())
        except Exception:
            monitors = []
        primary = next(
            (monitor for monitor in monitors if getattr(monitor, "is_primary", False)),
            monitors[0] if monitors else None,
        )
        secondary = next((monitor for monitor in monitors if monitor is not primary), None)
        if secondary is None:
            self.overrideredirect(False)
            self.title("Stage Cue Live View")
            width, height = 960, 540
            screen_x = primary.x if primary else 0
            screen_y = primary.y if primary else 0
            screen_width = primary.width if primary else self.winfo_screenwidth()
            screen_height = primary.height if primary else self.winfo_screenheight()
            x = screen_x + max(0, (screen_width - width) // 2)
            y = screen_y + max(0, (screen_height - height) // 2)
        else:
            self.overrideredirect(True)
            width, height, x, y = secondary.width, secondary.height, secondary.x, secondary.y
        x_offset = f"+{x}" if x >= 0 else str(x)
        y_offset = f"+{y}" if y >= 0 else str(y)
        self.geometry(f"{width}x{height}{x_offset}{y_offset}")
        self.deiconify()
        self.lift()
        self._opened = True
        return secondary is not None

    def _layout_canvas(self, _event=None) -> None:
        available_width = max(160, self.winfo_width())
        available_height = max(90, self.winfo_height())
        if available_width / available_height > 16 / 9:
            height = available_height
            width = round(height * 16 / 9)
        else:
            width = available_width
            height = round(width * 9 / 16)
        self.canvas.configure(width=width, height=height)
        self.renderer.resize()

    def present(self, cue):
        if not self._opened or not self.winfo_viewable():
            self.open_on_output_display()
        self.renderer.render(cue)
        # Paint Live View before the Render cue is serialized and queued.
        self.canvas.update_idletasks()
