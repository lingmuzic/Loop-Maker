"""Loudness measurement and normalisation (ITU-R BS.1770 / EBU R128 style).

Pure numpy/scipy, no file I/O. Used by the pipeline to bring every file of
a batch to the same integrated loudness, so ambience beds arrive in the game
engine / middleware already level-matched.

- Integrated loudness (LUFS) comes from ``pyloudnorm``, a well-tested
  BS.1770-4 implementation (K-weighting + 400 ms blocks + gating).
- True peak is estimated by 4x oversampling (BS.1770 Annex 2 approach), so
  inter-sample peaks that a DAC would produce are caught, not just sample
  peaks.
- Gain is a single static gain per file. If reaching the target would push
  the true peak over the ceiling, the gain is reduced instead of limiting or
  compressing: the sound is never altered, and the report says by how much
  the file fell short.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:  # Optional dependency: normalisation is unavailable without it.
    import pyloudnorm
    from scipy.signal import resample_poly
except ImportError:  # pragma: no cover - depends on the environment
    pyloudnorm = None

DEFAULT_TARGET_LUFS = -23.0     # EBU R128 programme reference; change per project
DEFAULT_PEAK_CEILING_DBTP = -1.0
TRUE_PEAK_OVERSAMPLING = 4

# BS.1770 channel order for pyloudnorm's weights: L, R, C, Ls, Rs.
# For 5.1 (L, R, C, LFE, Ls, Rs) the LFE channel is excluded from loudness.
_LFE_INDEX_IN_5_1 = 3


class LoudnessError(ValueError):
    """The file cannot be measured (too short, silent, unsupported layout...)."""


def _as_2d(audio: np.ndarray) -> np.ndarray:
    data = np.asarray(audio, dtype=np.float64)
    return data[:, np.newaxis] if data.ndim == 1 else data


def integrated_loudness(audio: np.ndarray, sample_rate: int) -> float:
    """Integrated loudness in LUFS. Raises :class:`LoudnessError` if not measurable."""
    if pyloudnorm is None:
        raise LoudnessError("loudness normalisation needs 'pyloudnorm' (pip install pyloudnorm)")

    data = _as_2d(audio)
    channels = data.shape[1]
    if channels == 6:
        data = np.delete(data, _LFE_INDEX_IN_5_1, axis=1)  # assume 5.1; LFE is not measured
    elif channels > 5:
        raise LoudnessError(f"{channels}-channel files are not supported for loudness (max 5, or 5.1)")

    try:
        loudness = pyloudnorm.Meter(sample_rate).integrated_loudness(data)
    except ValueError as exc:  # e.g. shorter than one 400 ms block
        raise LoudnessError(f"cannot measure loudness: {exc}") from exc
    if not np.isfinite(loudness):
        raise LoudnessError("file is silent (loudness is -inf)")
    return float(loudness)


def true_peak_db(audio: np.ndarray, chunk_frames: int = 1 << 16) -> float:
    """Estimated true peak in dBTP (4x oversampled), -inf for silence.

    Oversampling a long file in one go would need ~4x its memory (a 5 minute
    stereo file -> ~1 GB), so it is done in chunks. Each chunk is extended by
    a margin on both sides so the interpolation filter sees real neighbours,
    and only the chunk's own (central) part of the result is kept.
    """
    data = _as_2d(audio)
    n = data.shape[0]
    if n == 0:
        return float("-inf")

    up = TRUE_PEAK_OVERSAMPLING
    margin = 64  # > half the length of resample_poly's default filter (in input samples)
    peak = float(np.max(np.abs(data)))  # the true peak is never below the sample peak
    for start in range(0, n, chunk_frames):
        stop = min(start + chunk_frames, n)
        lo, hi = max(0, start - margin), min(n, stop + margin)
        upsampled = resample_poly(data[lo:hi], up, 1, axis=0)
        own = upsampled[(start - lo) * up:(stop - lo) * up]
        peak = max(peak, float(np.max(np.abs(own))))
    return 20.0 * np.log10(peak) if peak > 0 else float("-inf")


@dataclass(frozen=True)
class Normalization:
    """What normalising one file did."""

    measured_lufs: float
    target_lufs: float
    gain_db: float
    true_peak_after_dbtp: float
    limited_by_peak: bool   # True if the ceiling stopped us reaching the target

    @property
    def final_lufs(self) -> float:
        return self.measured_lufs + self.gain_db

    @property
    def shortfall_db(self) -> float:
        """How far below the target the file ended up (0 when the target was reached)."""
        return max(0.0, self.target_lufs - self.final_lufs) if self.limited_by_peak else 0.0

    def format(self) -> str:
        line = (
            f"  Loudness: {self.measured_lufs:.1f} -> {self.final_lufs:.1f} LUFS "
            f"(gain {self.gain_db:+.1f} dB, true peak {self.true_peak_after_dbtp:.1f} dBTP)"
        )
        if self.limited_by_peak:
            line += f"\n  NOTE: {self.shortfall_db:.1f} dB short of target, held back by the peak ceiling"
        return line


def normalize(
    audio: np.ndarray,
    sample_rate: int,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    peak_ceiling_dbtp: float = DEFAULT_PEAK_CEILING_DBTP,
) -> tuple[np.ndarray, Normalization]:
    """Apply one static gain so ``audio`` reaches ``target_lufs``.

    The gain is capped so the true peak stays at or below ``peak_ceiling_dbtp``.
    Returns the gained audio (same shape) and a :class:`Normalization` record.
    """
    measured = integrated_loudness(audio, sample_rate)
    peak_before = true_peak_db(audio)

    gain_db = target_lufs - measured
    limited = False
    headroom_db = peak_ceiling_dbtp - peak_before
    if gain_db > headroom_db:
        gain_db = headroom_db
        limited = True

    gained = np.asarray(audio, dtype=np.float64) * 10.0 ** (gain_db / 20.0)
    return gained, Normalization(
        measured_lufs=measured,
        target_lufs=target_lufs,
        gain_db=gain_db,
        true_peak_after_dbtp=peak_before + gain_db,
        limited_by_peak=limited,
    )
