"""Audio file helpers built on soundfile (libsndfile).

Responsibilities:
- read any supported file as float64 samples, remembering its bit depth
- pick the output subtype (source bit depth, or the one a preset asks for)
- write WAV/FLAC/OGG safely (quantise + dither 16/24-bit, never wrap around)
- expand a mix of files and folders into a list of input files
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import soundfile as sf

from .convert import quantize

#: Extensions picked up when a folder is given as input.
SUPPORTED_EXTENSIONS = (".wav", ".flac", ".aif", ".aiff", ".ogg")

#: Fallback when the input encoding has no lossless WAV equivalent (e.g. Ogg Vorbis).
FALLBACK_SUBTYPE = "PCM_24"

# WAV can store these subtypes directly, so we keep them as-is.
_WAV_LOSSLESS_SUBTYPES = {"PCM_U8", "PCM_16", "PCM_24", "PCM_32", "FLOAT", "DOUBLE"}

# Subtypes from other containers that map cleanly onto a WAV subtype.
_SUBTYPE_MAP = {
    "PCM_S8": "PCM_U8",  # FLAC/AIFF signed 8-bit -> WAV unsigned 8-bit (same resolution)
}

# Integer PCM formats cannot represent values outside [-1, 1); libsndfile
# would wrap them around unless we clip first.
_INTEGER_SUBTYPES = {"PCM_U8", "PCM_S8", "PCM_16", "PCM_24", "PCM_32"}


@dataclass(frozen=True)
class AudioFile:
    """Decoded audio plus the format facts we want to preserve."""

    data: np.ndarray   # float64, (frames,) for mono or (frames, channels)
    sample_rate: int
    subtype: str       # e.g. "PCM_24", "FLOAT"

    @property
    def channels(self) -> int:
        return 1 if self.data.ndim == 1 else self.data.shape[1]


def read_audio(path: Path) -> AudioFile:
    """Read ``path`` as float64. Mono files come back 1-D."""
    info = sf.info(str(path))
    data, sample_rate = sf.read(str(path), dtype="float64", always_2d=False)
    return AudioFile(data=data, sample_rate=sample_rate, subtype=info.subtype)


#: File extension per output container.
EXTENSIONS = {"WAV": ".wav", "FLAC": ".flac", "OGG": ".ogg"}

_EXPLICIT_DEPTHS = {"16": "PCM_16", "24": "PCM_24", "32f": "FLOAT"}
_FLAC_SUBTYPES = {"PCM_S8", "PCM_16", "PCM_24"}


def wav_subtype_for(input_subtype: str) -> tuple[str, str | None]:
    """Choose the WAV subtype that keeps the input's bit depth.

    Returns ``(subtype, note)``; ``note`` explains any change of bit depth and
    is ``None`` when the input bit depth is preserved exactly.
    """
    if input_subtype in _WAV_LOSSLESS_SUBTYPES:
        return input_subtype, None
    if input_subtype in _SUBTYPE_MAP:
        return _SUBTYPE_MAP[input_subtype], None
    return FALLBACK_SUBTYPE, (
        f"input encoding {input_subtype} has no direct WAV equivalent; wrote {FALLBACK_SUBTYPE}"
    )


def resolve_subtype(bit_depth: str | None, file_format: str, source_subtype: str) -> tuple[str, str | None]:
    """Pick the soundfile subtype for an output. ``bit_depth=None`` keeps the source's.

    Returns ``(subtype, note)`` where ``note`` explains a forced change.
    """
    if file_format == "OGG":
        return "VORBIS", None  # lossy: bit depth does not apply
    if bit_depth is not None:
        return _EXPLICIT_DEPTHS[bit_depth], None
    if file_format == "FLAC":
        if source_subtype in _FLAC_SUBTYPES:
            return source_subtype, None
        if source_subtype == "PCM_U8":
            return "PCM_S8", None
        return FALLBACK_SUBTYPE, f"FLAC cannot store {source_subtype}; wrote {FALLBACK_SUBTYPE}"
    return wav_subtype_for(source_subtype)


def write_audio(
    path: Path,
    data: np.ndarray,
    sample_rate: int,
    subtype: str,
    file_format: str = "WAV",
    dither: bool = False,
    rng: np.random.Generator | None = None,
) -> list[str]:
    """Write ``data`` (float, nominally [-1, 1]) and return notes worth reporting.

    16/24-bit PCM is quantised here (with optional TPDF dither, see
    ``convert.quantize``), so rounding and clipping are explicit. Other
    integer and lossy formats are clipped to [-1, 1] before writing so
    nothing can wrap around; float formats are written unclipped.
    """
    notes: list[str] = []
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    peak_db = 20 * np.log10(peak) if peak > 0 else float("-inf")

    if subtype in ("PCM_16", "PCM_24"):
        bits = 16 if subtype == "PCM_16" else 24
        data, overs = quantize(data, bits, dither, rng)
        if overs:
            notes.append(
                f"peak reached {peak_db:+.2f} dBFS; {overs} samples clipped "
                f"(try the 'linear' curve for correlated material, or lower the input level)"
            )
    elif subtype in _INTEGER_SUBTYPES or subtype == "VORBIS":
        if peak > 1.0:
            notes.append(f"peak reached {peak_db:+.2f} dBFS; {int(np.count_nonzero(np.abs(data) > 1.0))} samples clipped")
            data = np.clip(data, -1.0, 1.0)
    elif peak > 1.0:
        notes.append(f"peak reached {peak_db:+.2f} dBFS (kept unclipped in float output)")

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), data, sample_rate, subtype=subtype, format=file_format)
    return notes


def collect_inputs(paths: Iterable[str | Path]) -> tuple[list[Path], list[str]]:
    """Expand files and folders into a sorted, de-duplicated list of audio files.

    Folders are scanned non-recursively for :data:`SUPPORTED_EXTENSIONS`.
    Returns ``(files, problems)`` where ``problems`` lists inputs that were
    skipped and why.
    """
    files: list[Path] = []
    problems: list[str] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        key = p.resolve()
        if key not in seen:
            seen.add(key)
            files.append(p)

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found = sorted(
                child for child in p.iterdir()
                if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS
            )
            if not found:
                problems.append(f"{p}: folder contains no supported audio files")
            for child in found:
                add(child)
        elif p.is_file():
            if p.suffix.lower() in SUPPORTED_EXTENSIONS:
                add(p)
            else:
                problems.append(f"{p}: unsupported file type (expected {', '.join(SUPPORTED_EXTENSIONS)})")
        else:
            problems.append(f"{p}: not found")

    return files, problems
