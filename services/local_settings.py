"""Per-machine preferences that never leave this computer.

Church data lives in SQLite and is pushed to Atlas by the sync service.  The
values here deliberately do *not*: a projection laptop at 1366x768 and a 4K
office desktop should each keep their own scaling and theme even though both
sign in to the same church.  They are stored as a small JSON file beside the
local database so a database reset or a re-login leaves them untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir


SETTINGS_FILE_NAME = "local_settings.json"

THEME_CHOICES = ("system", "light", "dark")
SCALE_MINIMUM = 0.75
SCALE_MAXIMUM = 2.00

DEFAULTS: dict[str, Any] = {
    "theme": "light",
    # "auto" derives the factor from the monitor reported by Tk at startup.
    "ui_scale": "auto",
    "remember_window_geometry": True,
    "window_geometry": "",
}


def clamp_scale(value: float) -> float:
    """Keep a scale factor inside the supported range, snapped to 5% steps."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.0
    number = max(SCALE_MINIMUM, min(SCALE_MAXIMUM, number))
    return round(number * 20) / 20


class LocalSettings:
    """A tiny JSON-backed store for machine-specific appearance preferences."""

    def __init__(self, path: Path | str | None = None):
        if path is None:
            directory = Path(user_data_dir("Stage Cue", "GJDevTech"))
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / SETTINGS_FILE_NAME
        self.path = Path(path)
        self._values: dict[str, Any] = dict(DEFAULTS)
        self.reload()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def reload(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(raw, dict):
            self._values.update({key: raw[key] for key in raw if key in DEFAULTS})

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._values, indent=2, sort_keys=True), encoding="utf-8"
            )
        except OSError:
            # Appearance preferences are a convenience; a read-only profile or a
            # locked USB install must never stop the operator from presenting.
            pass

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value
        self.save()

    def update(self, **values: Any) -> None:
        self._values.update(values)
        self.save()

    def reset(self) -> None:
        self._values = dict(DEFAULTS)
        self.save()

    @property
    def theme(self) -> str:
        value = str(self._values.get("theme", "light")).lower()
        return value if value in THEME_CHOICES else "light"

    @theme.setter
    def theme(self, value: str) -> None:
        self.set("theme", value if value in THEME_CHOICES else "light")

    @property
    def ui_scale(self) -> str | float:
        value = self._values.get("ui_scale", "auto")
        if isinstance(value, str) and value.lower() == "auto":
            return "auto"
        return clamp_scale(value)

    @ui_scale.setter
    def ui_scale(self, value: str | float) -> None:
        if isinstance(value, str) and value.lower() == "auto":
            self.set("ui_scale", "auto")
        else:
            self.set("ui_scale", clamp_scale(value))

    @property
    def scale_is_automatic(self) -> bool:
        return self.ui_scale == "auto"

    @property
    def remember_window_geometry(self) -> bool:
        return bool(self._values.get("remember_window_geometry", True))

    @remember_window_geometry.setter
    def remember_window_geometry(self, value: bool) -> None:
        self.set("remember_window_geometry", bool(value))

    @property
    def window_geometry(self) -> str:
        return str(self._values.get("window_geometry", "") or "")

    @window_geometry.setter
    def window_geometry(self, value: str) -> None:
        self.set("window_geometry", str(value or ""))
