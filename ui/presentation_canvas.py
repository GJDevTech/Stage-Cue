from __future__ import annotations

import base64
from io import BytesIO
import tkinter as tk
from tkinter import font as tkfont
from typing import Any

from PIL import Image, ImageOps, ImageTk

try:
    import cv2
except ImportError:  # Video backgrounds remain unavailable until requirements are installed.
    cv2 = None

from services.presentation_service import apply_text_case, calculate_text_box_layout


class PresentationCanvasRenderer:
    """Render one cue without automatic line wrapping."""

    def __init__(self, canvas: tk.Canvas):
        self.canvas = canvas
        self.state: dict[str, Any] = {}
        self.background_id = canvas.create_image(0, 0, anchor="nw", state="hidden")
        self.shadow_id = canvas.create_text(0, 0, text="")
        self.outline_ids = [canvas.create_text(0, 0, text="") for _ in range(8)]
        self.text_id = canvas.create_text(0, 0, text="")
        self.logo_id = canvas.create_image(0, 0, anchor="nw", state="hidden")
        self._logo_photo = None
        self._logo_source = ""
        self._background_source: tuple[str, str] = ("", "")
        self._background_image = None
        self._background_photo = None
        self._background_photo_key = None
        self._video_capture = None
        self._video_job = None
        self._video_delay_ms = 33
        canvas.bind("<Configure>", self.resize, add="+")

    def render(self, state: dict[str, Any]) -> None:
        self.state = state
        settings = state.get("settings", {})
        blackout = state.get("mode") == "BLACKOUT"
        self.canvas.configure(
            bg="#000000" if blackout else settings.get("backgroundColor", "#000000")
        )
        self._load_background(settings)
        text = (
            ""
            if blackout or state.get("textHidden")
            else apply_text_case(
                str(state.get("text", "")), settings.get("textCase", "preserve")
            )
        )
        common = {
            "text": text,
            "justify": settings.get("textHorizontalAlign", "center"),
            "width": 0,
        }
        self.canvas.itemconfigure(
            self.shadow_id,
            **common,
            fill=settings.get("shadowColor", "#000000"),
            state="normal" if text and settings.get("shadowEnabled") else "hidden",
        )
        for item in self.outline_ids:
            self.canvas.itemconfigure(
                item,
                **common,
                fill=settings.get("outlineColor", "#000000"),
                state="normal" if text and settings.get("outlineEnabled") else "hidden",
            )
        self.canvas.itemconfigure(
            self.text_id,
            **common,
            fill=settings.get("textColor", "#FFFFFF"),
            state="normal" if text else "hidden",
        )
        self._load_logo(settings.get("logoData", ""))
        self.resize()

    def resize(self, _event=None) -> None:
        if not self.state:
            return
        width = max(100, self.canvas.winfo_width())
        height = max(100, self.canvas.winfo_height())
        settings = self.state.get("settings", {})
        layout = calculate_text_box_layout(width, height, settings)
        self._resize_background(width, height, settings)
        text = str(self.canvas.itemcget(self.text_id, "text"))
        scale = min(width / 1920, height / 1080)
        requested_size = max(8, round(int(settings.get("fontSize", 54)) * scale))
        fitted_font = self._fit_font(
            text,
            settings.get("fontFamily", "Arial"),
            requested_size,
            max(10, layout.right - layout.left),
            max(10, layout.bottom - layout.top),
            bool(settings.get("bold")),
            bool(settings.get("italic")),
        )
        anchor = layout.anchor
        x, y = layout.text_x, layout.text_y
        outline = max(0, round(int(settings.get("outlineWidth", 0)) * scale))
        offsets = [
            (-outline, 0), (outline, 0), (0, -outline), (0, outline),
            (-outline, -outline), (-outline, outline), (outline, -outline), (outline, outline),
        ]
        for item, (dx, dy) in zip(self.outline_ids, offsets):
            self.canvas.coords(item, x + dx, y + dy)
            self.canvas.itemconfigure(item, font=fitted_font, anchor=anchor)
        self.canvas.coords(
            self.shadow_id,
            x + round(int(settings.get("shadowOffsetX", 0)) * scale),
            y + round(int(settings.get("shadowOffsetY", 0)) * scale),
        )
        self.canvas.itemconfigure(self.shadow_id, font=fitted_font, anchor=anchor)
        self.canvas.coords(self.text_id, x, y)
        self.canvas.itemconfigure(self.text_id, font=fitted_font, anchor=anchor)
        self._resize_logo(width, height, settings)

    def _load_background(self, settings: dict[str, Any]) -> None:
        kind = str(settings.get("backgroundType", "solid")).casefold()
        source = ""
        if kind == "image":
            source = str(settings.get("backgroundImageData", ""))
        elif kind == "video":
            source = str(settings.get("backgroundVideoPath", ""))
        else:
            kind = "solid"
        identity = (kind, source)
        if identity == self._background_source:
            return
        self._background_source = identity
        self._stop_video()
        self._background_image = None
        self._background_photo = None
        self._background_photo_key = None
        self.canvas.itemconfigure(self.background_id, state="hidden")
        if kind == "image" and source and "," in source:
            try:
                encoded = source.split(",", 1)[1]
                self._background_image = Image.open(
                    BytesIO(base64.b64decode(encoded))
                ).convert("RGB")
            except Exception:
                self._background_image = None
        elif kind == "video" and source and cv2 is not None:
            capture = cv2.VideoCapture(source)
            if capture.isOpened():
                self._video_capture = capture
                fps = float(capture.get(cv2.CAP_PROP_FPS) or 30)
                self._video_delay_ms = max(15, min(100, round(1000 / max(1, fps))))
                self._video_tick()
            else:
                capture.release()

    def _video_tick(self) -> None:
        if self._video_capture is None or cv2 is None:
            return
        ok, frame = self._video_capture.read()
        if not ok:
            self._video_capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._video_capture.read()
        if ok:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            self._background_image = Image.fromarray(frame)
            self._background_photo_key = None
            if self.state:
                self._resize_background(
                    max(100, self.canvas.winfo_width()),
                    max(100, self.canvas.winfo_height()),
                    self.state.get("settings", {}),
                )
        try:
            self._video_job = self.canvas.after(
                self._video_delay_ms, self._video_tick
            )
        except tk.TclError:
            self._stop_video()

    def _stop_video(self) -> None:
        if self._video_job is not None:
            try:
                self.canvas.after_cancel(self._video_job)
            except tk.TclError:
                pass
            self._video_job = None
        if self._video_capture is not None:
            self._video_capture.release()
            self._video_capture = None

    def _resize_background(self, width, height, settings) -> None:
        visible = (
            self.state.get("mode") != "BLACKOUT"
            and str(settings.get("backgroundType", "solid")).casefold()
            in {"image", "video"}
            and self._background_image is not None
        )
        if not visible:
            self.canvas.itemconfigure(self.background_id, state="hidden")
            return
        photo_key = (self._background_source, int(width), int(height))
        if (
            self._background_source[0] == "image"
            and self._background_photo is not None
            and self._background_photo_key == photo_key
        ):
            self.canvas.itemconfigure(
                self.background_id, image=self._background_photo, state="normal"
            )
            self.canvas.tag_lower(self.background_id)
            return
        fitted = ImageOps.fit(
            self._background_image,
            (max(1, int(width)), max(1, int(height))),
            method=(
                Image.Resampling.BILINEAR
                if self._background_source[0] == "video"
                else Image.Resampling.LANCZOS
            ),
        )
        self._background_photo = ImageTk.PhotoImage(fitted)
        self._background_photo_key = photo_key
        self.canvas.coords(self.background_id, 0, 0)
        self.canvas.itemconfigure(
            self.background_id, image=self._background_photo, state="normal"
        )
        self.canvas.tag_lower(self.background_id)

    @staticmethod
    def _fit_font(text, family, requested_size, box_width, box_height, bold, italic):
        lines = text.splitlines() or [""]
        low, high, best = 1, max(1, requested_size), 1
        while low <= high:
            size = (low + high) // 2
            candidate = tkfont.Font(
                family=family,
                size=size,
                weight="bold" if bold else "normal",
                slant="italic" if italic else "roman",
            )
            widest = max((candidate.measure(line) for line in lines), default=0)
            total_height = candidate.metrics("linespace") * max(1, len(lines))
            if widest <= box_width and total_height <= box_height:
                best = size
                low = size + 1
            else:
                high = size - 1
        return tkfont.Font(
            family=family,
            size=best,
            weight="bold" if bold else "normal",
            slant="italic" if italic else "roman",
        )

    def _load_logo(self, data_url: str) -> None:
        if data_url == self._logo_source:
            return
        self._logo_source = data_url
        self._logo_photo = None
        self._logo_image = None
        if not data_url or "," not in data_url:
            return
        try:
            encoded = data_url.split(",", 1)[1]
            image = Image.open(BytesIO(base64.b64decode(encoded))).convert("RGBA")
        except Exception:
            return
        self._logo_image = image

    def _resize_logo(self, canvas_width, canvas_height, settings):
        visible = (
            self.state.get("logoVisible")
            and self.state.get("mode") != "BLACKOUT"
            and getattr(self, "_logo_image", None) is not None
        )
        if not visible:
            self.canvas.itemconfigure(self.logo_id, state="hidden")
            return
        target_width = max(12, round(canvas_width * int(settings.get("logoWidth", 20)) / 100))
        ratio = self._logo_image.height / max(1, self._logo_image.width)
        target_height = max(1, round(target_width * ratio))
        max_height = max(12, round(canvas_height * (100 - int(settings.get("logoY", 5))) / 100))
        if target_height > max_height:
            target_height = max_height
            target_width = max(1, round(target_height / ratio))
        image = self._logo_image.resize((target_width, target_height), Image.Resampling.LANCZOS)
        self._logo_photo = ImageTk.PhotoImage(image)
        self.canvas.coords(
            self.logo_id,
            canvas_width * int(settings.get("logoX", 5)) / 100,
            canvas_height * int(settings.get("logoY", 5)) / 100,
        )
        self.canvas.itemconfigure(self.logo_id, image=self._logo_photo, state="normal")
        self.canvas.tag_raise(self.logo_id)
