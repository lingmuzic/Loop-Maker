"""Where things live, both when run from source and as a PyInstaller-built .exe.

* ``app_dir()``      folder the user sees: the project root from source, or
                     the folder containing ``Loop Maker.exe`` when frozen.
                     User-editable files (presets.json) and the default
                     output folder live here.
* ``resource_path()`` read-only files bundled inside the app (e.g. the icon).
                     When frozen these are unpacked under ``sys._MEIPASS``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    return Path(sys.executable).resolve().parent if is_frozen() else _PROJECT_ROOT


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", _PROJECT_ROOT)) if is_frozen() else _PROJECT_ROOT
    return base / relative
