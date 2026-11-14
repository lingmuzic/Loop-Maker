"""Loop variations: several rotations of the same loop, starting at quiet spots.

A normal loop splits the file at its midpoint. A variation splits it
somewhere else (``core.make_loop(split_at=...)``) and applies the same swap +
crossfade. The result is the same circular material rotated: each variation
starts (and wraps) at a different moment of the recording.

Choosing split points
---------------------
1. Short-window RMS energy (50 ms windows, 10 ms hop) is computed on a
   mono fold of the file, then smoothed over 200 ms.
2. The loudest transients are fenced off: windows clearly louder than the
   rest (above the 95th percentile *and* 6 dB over the median) plus 300 ms
   either side are not allowed. A variation then never starts mid-hit.
3. Candidates must leave room for the crossfade on both sides of the split,
   keep away from the midpoint (that is the standard loop), and keep a
   minimum spacing from each other (default 12 % of the file length).
4. Local minima ("valleys") of the smoothed energy are tried quietest first;
   if spacing leaves too few of them, any allowed position is used, filled
   in from the left so the requested count always fits when the length
   check below passes.

Length check: the source must be longer than
``2 x crossfade + N x minimum spacing``, otherwise there is no room for N
clearly different start points.
"""

from __future__ import annotations

import numpy as np

VARIATION_COUNTS = (3, 4, 5)
DEFAULT_MIN_SPACING = 0.12        # fraction of the file length
DEFAULT_SHORT_WARNING_S = 15.0    # rule of thumb, configurable

RMS_WINDOW_S = 0.05
HOP_S = 0.01
SMOOTHING_S = 0.2
TRANSIENT_PERCENTILE = 95
TRANSIENT_MARGIN_DB = 6.0
TRANSIENT_GUARD_S = 0.3
MIDPOINT_EXCLUSION = 0.01         # fraction of the file length either side of the midpoint


class VariationError(ValueError):
    """The source cannot provide the requested variations."""


def minimum_length_s(crossfade_s: float, count: int, min_spacing: float, duration_s: float) -> float:
    """``2 x crossfade + N x spacing`` in seconds, for a file of ``duration_s``."""
    return 2 * crossfade_s + count * min_spacing * duration_s


def check_length(duration_s: float, crossfade_s: float, count: int, min_spacing: float) -> None:
    """Raise :class:`VariationError` with the numbers if the file is too short."""
    if count not in VARIATION_COUNTS:
        raise VariationError(f"variations must be one of {VARIATION_COUNTS}, not {count}")
    if not 0 < min_spacing < 1:
        raise VariationError(f"minimum spacing must be between 0 and 100 % of the file, not {min_spacing:.0%}")
    spacing_s = min_spacing * duration_s
    needed = minimum_length_s(crossfade_s, count, min_spacing, duration_s)
    if duration_s <= needed:
        raise VariationError(
            f"too short for {count} variations: needs more than 2 x {crossfade_s:g} s crossfade + "
            f"{count} x {spacing_s:.1f} s minimum spacing ({min_spacing:.0%} of the file) = {needed:.1f} s, "
            f"but the file is {duration_s:.1f} s"
        )


def max_variations(duration_s: float, crossfade_s: float, min_spacing: float) -> int:
    """Largest allowed variation count that passes :func:`check_length` (0 if none)."""
    for count in sorted(VARIATION_COUNTS, reverse=True):
        try:
            check_length(duration_s, crossfade_s, count, min_spacing)
            return count
        except VariationError:
            continue
    return 0


def short_source_warning(duration_s: float, threshold_s: float = DEFAULT_SHORT_WARNING_S) -> str | None:
    if duration_s < threshold_s:
        return (
            f"source is only {duration_s:.1f} s (< {threshold_s:g} s): variations may sound nearly identical"
        )
    return None


def energy_profile(audio: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(centres, rms_db)``: short-window RMS at every hop, in frames and dB."""
    data = np.asarray(audio, dtype=np.float64)
    power = np.square(data) if data.ndim == 1 else np.mean(np.square(data), axis=1)
    hop = max(1, int(round(HOP_S * sample_rate)))
    half = max(1, int(round(RMS_WINDOW_S * sample_rate)) // 2)

    centres = np.arange(0, len(power), hop)
    cumulative = np.concatenate([[0.0], np.cumsum(power)])
    lo = np.clip(centres - half, 0, len(power))
    hi = np.clip(centres + half, 0, len(power))
    mean_power = (cumulative[hi] - cumulative[lo]) / np.maximum(hi - lo, 1)
    return centres, 10 * np.log10(mean_power + 1e-20)


def _smooth(values: np.ndarray, width: int) -> np.ndarray:
    if width <= 1:
        return values
    kernel = np.ones(width) / width
    padded = np.pad(values, (width // 2, width - 1 - width // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def choose_split_points(
    audio: np.ndarray,
    sample_rate: int,
    crossfade_s: float,
    count: int,
    min_spacing: float = DEFAULT_MIN_SPACING,
) -> list[int]:
    """Pick ``count`` split frames for variations (sorted by position).

    Raises :class:`VariationError` if the file is too short (see module docs).
    """
    n_frames = len(audio)
    duration_s = n_frames / sample_rate
    check_length(duration_s, crossfade_s, count, min_spacing)

    xfade = int(round(crossfade_s * sample_rate))
    spacing = int(np.ceil(min_spacing * n_frames))
    mid = n_frames // 2
    mid_hole = min(int(MIDPOINT_EXCLUSION * n_frames), spacing // 2)

    centres, rms_db = energy_profile(audio, sample_rate)
    smooth_db = _smooth(rms_db, int(round(SMOOTHING_S / HOP_S)))

    # Fence off the loudest transients (only if they really stand out).
    loud = (rms_db > np.percentile(rms_db, TRANSIENT_PERCENTILE)) & (
        rms_db > np.median(rms_db) + TRANSIENT_MARGIN_DB
    )
    guard = int(round(TRANSIENT_GUARD_S / HOP_S))
    near_transient = np.convolve(loud.astype(int), np.ones(2 * guard + 1, dtype=int), mode="same") > 0

    valid = (centres >= xfade) & (centres <= n_frames - xfade) & (np.abs(centres - mid) >= mid_hole)
    quiet_ok = valid & ~near_transient

    # Valleys: local minima of the smoothed energy (within +/- 2 hops).
    neighbourhood = np.lib.stride_tricks.sliding_window_view(np.pad(smooth_db, 2, mode="edge"), 5)
    is_valley = smooth_db <= neighbourhood.min(axis=1)

    def far_enough(frame: int, chosen: list[int]) -> bool:
        return all(abs(frame - other) >= spacing for other in chosen)

    chosen: list[int] = []
    # 1) quiet valleys, quietest first
    for i in np.argsort(smooth_db):
        if len(chosen) == count:
            break
        if quiet_ok[i] and is_valley[i] and far_enough(int(centres[i]), chosen):
            chosen.append(int(centres[i]))
    # 2) any quiet, non-transient position, quietest first
    for i in np.argsort(smooth_db):
        if len(chosen) == count:
            break
        if quiet_ok[i] and far_enough(int(centres[i]), chosen):
            chosen.append(int(centres[i]))
    # 3) guaranteed fill: pack from the left over every valid position
    if len(chosen) < count:
        chosen = []
        for frame in centres[valid]:
            if far_enough(int(frame), chosen):
                chosen.append(int(frame))
            if len(chosen) == count:
                break
    if len(chosen) < count:  # only reachable right at the length limit
        raise VariationError(
            f"could only place {len(chosen)} of {count} split points {min_spacing:.0%} apart; "
            f"lower the minimum spacing or the crossfade"
        )
    return sorted(chosen)
