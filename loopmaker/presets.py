"""Output presets: target sample rate, bit depth, file format and dither.

Presets live in a JSON file the user can edit (``presets.json`` in the
project root by default). If the file is missing or broken, the built-in
defaults below are used and the problem is reported, never fatal.

JSON layout::

    {
      "version": 1,
      "presets": {
        "Wwise": {"sample_rate": 48000, "bit_depth": "24", "format": "WAV",
                  "dither": "auto", "description": "..."},
        ...
      }
    }

``sample_rate`` / ``bit_depth`` may also be ``"source"`` (keep the input's).
``"Custom"`` is not stored: it is built from the user's own field values.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import soundfile as sf

from .paths import app_dir

CUSTOM = "Custom"
SOURCE = "source"

SAMPLE_RATES = (44100, 48000, 96000)
BIT_DEPTHS = ("16", "24", "32f")
DITHER_MODES = ("auto", "always", "never")

#: Default location of the user-editable preset file: the project root, or the
#: folder next to ``Loop Maker.exe`` in the standalone build.
DEFAULT_PRESETS_PATH = app_dir() / "presets.json"


def available_formats() -> tuple[str, ...]:
    """Output containers this machine's libsndfile can write."""
    formats = ["WAV", "FLAC"]
    if "OGG" in sf.available_formats() and "VORBIS" in sf.available_subtypes("OGG"):
        formats.append("OGG")
    return tuple(formats)


@dataclass(frozen=True)
class OutputSpec:
    """What the written file should look like. ``None`` means "same as source"."""

    sample_rate: int | None = None
    bit_depth: str | None = None   # "16", "24", "32f" or None
    file_format: str = "WAV"       # "WAV", "FLAC", "OGG"
    dither: str = "auto"           # see convert.should_dither

    def errors(self) -> list[str]:
        """Human-readable problems with this spec (empty list = valid)."""
        problems = []
        if self.sample_rate is not None and self.sample_rate not in SAMPLE_RATES:
            problems.append(f"sample rate must be one of {SAMPLE_RATES} or source, not {self.sample_rate}")
        if self.bit_depth is not None and self.bit_depth not in BIT_DEPTHS:
            problems.append(f"bit depth must be one of {BIT_DEPTHS} or source, not {self.bit_depth!r}")
        if self.file_format not in available_formats():
            problems.append(f"format must be one of {available_formats()}, not {self.file_format!r}")
        if self.file_format == "FLAC" and self.bit_depth == "32f":
            problems.append("FLAC cannot store 32-bit float; use WAV or 16/24-bit")
        if self.dither not in DITHER_MODES:
            problems.append(f"dither must be one of {DITHER_MODES}, not {self.dither!r}")
        return problems

    def describe(self) -> str:
        rate = f"{self.sample_rate / 1000:g} kHz" if self.sample_rate else "source rate"
        depth = {"16": "16-bit", "24": "24-bit", "32f": "32-bit float", None: "source bit depth"}[self.bit_depth]
        if self.file_format == "OGG":
            depth = "Vorbis"
        return f"{rate}, {depth} {self.file_format}, dither {self.dither}"


@dataclass(frozen=True)
class Preset:
    name: str
    spec: OutputSpec
    description: str = ""


#: Built-in defaults. Unity and Godot only specify 48 kHz WAV, so they keep the
#: source bit depth (documented in the README).
BUILTIN_PRESETS: dict[str, Preset] = {
    "Wwise": Preset("Wwise", OutputSpec(48000, "24", "WAV", "auto"), "48 kHz, 24-bit WAV"),
    "Unreal": Preset("Unreal", OutputSpec(48000, "16", "WAV", "always"), "48 kHz, 16-bit WAV, always dithered"),
    "Unity": Preset("Unity", OutputSpec(48000, None, "WAV", "auto"), "48 kHz WAV, source bit depth"),
    "Godot": Preset("Godot", OutputSpec(48000, None, "WAV", "auto"), "48 kHz WAV, source bit depth"),
}


def _spec_from_json(entry: dict) -> OutputSpec:
    """Build an OutputSpec from one JSON preset entry (raises on bad types)."""
    if not isinstance(entry, dict):
        raise ValueError("entry must be an object")
    rate = entry.get("sample_rate", SOURCE)
    depth = entry.get("bit_depth", SOURCE)
    spec = OutputSpec(
        sample_rate=None if rate == SOURCE else int(rate),
        bit_depth=None if depth == SOURCE else str(depth),
        file_format=str(entry.get("format", "WAV")).upper(),
        dither=str(entry.get("dither", "auto")),
    )
    problems = spec.errors()
    if problems:
        raise ValueError("; ".join(problems))
    return spec


def load_presets(path: Path | None = None) -> tuple[dict[str, Preset], list[str]]:
    """Load presets from ``path``; fall back to the built-ins on any problem.

    Returns ``(presets, warnings)``. A broken file -> all built-ins; a broken
    single entry -> that entry is skipped (and named in ``warnings``).
    """
    path = Path(path) if path is not None else DEFAULT_PRESETS_PATH
    if not path.is_file():
        return dict(BUILTIN_PRESETS), [f"preset file {path} not found; using built-in presets"]

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        entries = document["presets"]
        if not isinstance(entries, dict):
            raise ValueError("'presets' must be an object")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return dict(BUILTIN_PRESETS), [f"preset file {path} is invalid ({exc}); using built-in presets"]

    presets: dict[str, Preset] = {}
    warnings: list[str] = []
    for name, entry in entries.items():
        if name == CUSTOM:
            warnings.append(f"preset name {CUSTOM!r} is reserved; entry ignored")
            continue
        try:
            presets[name] = Preset(name, _spec_from_json(entry), str(entry.get("description", "")))
        except (ValueError, TypeError) as exc:
            warnings.append(f"preset {name!r} ignored: {exc}")
    if not presets:
        warnings.append("no valid presets in file; using built-in presets")
        return dict(BUILTIN_PRESETS), warnings
    return presets, warnings


def presets_to_json(presets: dict[str, Preset]) -> str:
    """Serialise presets in the documented JSON layout."""
    out = {}
    for name, preset in presets.items():
        spec = asdict(preset.spec)
        out[name] = {
            "sample_rate": spec["sample_rate"] or SOURCE,
            "bit_depth": spec["bit_depth"] or SOURCE,
            "format": spec["file_format"],
            "dither": spec["dither"],
            "description": preset.description,
        }
    return json.dumps({"version": 1, "presets": out}, indent=2) + "\n"
