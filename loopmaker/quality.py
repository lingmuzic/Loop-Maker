"""Loop-point quality report.

When a looped file plays back-to-back, the last sample is immediately followed
by the first one. Two things make that wrap point audible:

1. **A sample jump** (a click): the waveform value changes abruptly from the
   last sample to the first. We measure ``|first - last|`` and compare it with
   how big a normal sample-to-sample step is in this file (the 99th percentile
   of ``|diff|``). A ratio <= 1 means the wrap is no bigger than an ordinary
   step inside the recording.
2. **A level jump**: the tail is noticeably louder/quieter than the head. We
   compare the RMS of the last N ms with the RMS of the first N ms, in dB.

The report is computed for the original file too (as if it were looped
untouched) so you can see the before/after difference.

These are simple heuristics, not a perceptual model, but they reliably catch
the problems a hand-made loop would have.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DEFAULT_WINDOW_MS = 50.0

# Thresholds used for the one-word verdict. Deliberately simple and
# documented in the README.
MAX_JUMP_RATIO = 1.0
MAX_RMS_DIFF_DB = 2.0

_EPS = 1e-12  # avoids log(0) / division by zero on digital silence


@dataclass(frozen=True)
class WrapMetrics:
    """Measurements of the wrap point (tail -> head) of one signal."""

    sample_jump: float      # max over channels of |first sample - last sample|
    typical_step: float     # 99th percentile of |sample-to-sample difference|
    rms_head_db: float      # RMS level of the first window, dBFS
    rms_tail_db: float      # RMS level of the last window, dBFS

    @property
    def jump_ratio(self) -> float:
        """Wrap jump relative to an ordinary step inside the file."""
        return self.sample_jump / max(self.typical_step, _EPS)

    @property
    def rms_diff_db(self) -> float:
        """Head level minus tail level, in dB."""
        return self.rms_head_db - self.rms_tail_db

    @property
    def is_smooth(self) -> bool:
        return self.jump_ratio <= MAX_JUMP_RATIO and abs(self.rms_diff_db) <= MAX_RMS_DIFF_DB


def _rms_db(block: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0
    return 20.0 * np.log10(max(rms, _EPS))


def measure_wrap(audio: np.ndarray, sample_rate: int, window_ms: float = DEFAULT_WINDOW_MS) -> WrapMetrics:
    """Measure the wrap point of ``audio`` (mono 1-D or ``(frames, channels)``)."""
    data = np.asarray(audio, dtype=np.float64)
    if data.ndim == 1:
        data = data[:, np.newaxis]
    if data.shape[0] < 2:
        raise ValueError("Need at least two samples to measure a loop point.")

    # Keep the window no longer than half the file so head and tail don't overlap.
    window = int(round(window_ms / 1000.0 * sample_rate))
    window = max(1, min(window, data.shape[0] // 2))

    sample_jump = float(np.max(np.abs(data[0] - data[-1])))
    typical_step = float(np.percentile(np.abs(np.diff(data, axis=0)), 99))

    return WrapMetrics(
        sample_jump=sample_jump,
        typical_step=typical_step,
        rms_head_db=_rms_db(data[:window]),
        rms_tail_db=_rms_db(data[-window:]),
    )


@dataclass(frozen=True)
class LoopQuality:
    """Before/after wrap measurements for one processed file."""

    before: WrapMetrics
    after: WrapMetrics
    window_ms: float

    def format(self) -> str:
        """Human-readable multi-line report (used by both CLI and GUI)."""

        def row(label: str, m: WrapMetrics) -> str:
            return (
                f"  {label:<9} jump {m.sample_jump:.5f} ({m.jump_ratio:5.2f}x typical step)   "
                f"RMS head/tail {m.rms_head_db:6.1f} / {m.rms_tail_db:6.1f} dBFS "
                f"(diff {m.rms_diff_db:+.2f} dB)"
            )

        verdict = "SMOOTH" if self.after.is_smooth else "CHECK BY EAR"
        return "\n".join(
            [
                f"  Loop-point check (window {self.window_ms:g} ms): {verdict}",
                row("original", self.before),
                row("loop", self.after),
            ]
        )


def compare(
    original: np.ndarray, looped: np.ndarray, sample_rate: int, window_ms: float = DEFAULT_WINDOW_MS
) -> LoopQuality:
    """Measure the wrap point of the original and the looped signal."""
    return LoopQuality(
        before=measure_wrap(original, sample_rate, window_ms),
        after=measure_wrap(looped, sample_rate, window_ms),
        window_ms=window_ms,
    )
