"""Core loop-making DSP. Pure numpy: no file I/O, no GUI.

The technique mirrors the manual DAW workflow for looping ambience:

    original:   [ A (first half) | B (second half) ]
    swapped:    [ B | A ]
                       ^ the old file end meets the old file start here
    crossfade:  overlap the last C samples of B with the first C samples of A

The new head is B[0] and the new tail is A[-1]. In the original recording
those two samples were neighbours (they sat on either side of the midpoint),
so when the output wraps around, the transition is naturally continuous.
The only "edit" in the file is the crossfade in the middle, where the old
start and end are blended together.

Because the two crossfaded regions overlap, the output is exactly C samples
shorter than the input.
"""

from __future__ import annotations

import numpy as np

#: Names accepted by :func:`make_loop` for the ``curve`` argument.
CURVES = ("equal_power", "linear")

DEFAULT_CURVE = "equal_power"
DEFAULT_SAFETY_FADE_S = 0.005  # 5 ms


def crossfade_gains(length: int, curve: str = DEFAULT_CURVE) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(fade_out, fade_in)`` gain ramps of ``length`` samples.

    * ``"equal_power"``: sin/cos ramps. ``fade_out**2 + fade_in**2 == 1`` at
      every sample, which keeps the *perceived* level constant when the two
      signals are uncorrelated (typical for ambience / noise beds).
    * ``"linear"``: straight ramps. ``fade_out + fade_in == 1`` at every
      sample, which keeps the level constant for *correlated* material (e.g.
      the same tone on both sides), but dips ~3 dB in the middle for
      uncorrelated material.

    The ramps are sampled at sample centres, ``t = (n + 0.5) / length``, so
    they are symmetric and never contain an exact 0 or 1 at the edges.
    """
    if length <= 0:
        raise ValueError("Crossfade length must be at least one sample.")

    t = (np.arange(length) + 0.5) / length  # 0 < t < 1

    if curve == "equal_power":
        fade_in = np.sin(t * np.pi / 2)
        fade_out = np.cos(t * np.pi / 2)
    elif curve == "linear":
        fade_in = t
        fade_out = 1.0 - t
    else:
        raise ValueError(f"Unknown curve {curve!r}. Choose one of: {', '.join(CURVES)}.")

    return fade_out, fade_in


def apply_safety_fades(audio: np.ndarray, fade_samples: int) -> np.ndarray:
    """Apply a short linear fade-in at the start and fade-out at the end.

    Works in place on a copy and returns it. ``fade_samples == 0`` is a no-op.
    """
    out = audio.copy()
    if fade_samples <= 0:
        return out

    # Never let the two fades overlap on very short files.
    n = min(fade_samples, len(out) // 2)
    if n == 0:
        return out

    ramp = np.linspace(0.0, 1.0, n, endpoint=False)
    # Reshape so the same ramp applies to every channel of 2-D audio.
    ramp = ramp.reshape((n,) + (1,) * (out.ndim - 1))
    out[:n] *= ramp
    out[-n:] *= ramp[::-1]
    return out


def make_loop(
    audio: np.ndarray,
    sample_rate: int,
    crossfade_s: float,
    curve: str = DEFAULT_CURVE,
    safety_fade_s: float = DEFAULT_SAFETY_FADE_S,
) -> np.ndarray:
    """Turn ``audio`` into a seamlessly loopable signal.

    Parameters
    ----------
    audio:
        Samples as a numpy array, shape ``(frames,)`` for mono or
        ``(frames, channels)`` for multichannel. Integer arrays are accepted
        and converted to float.
    sample_rate:
        Sample rate in Hz (used to convert seconds to samples).
    crossfade_s:
        Crossfade length in seconds. Must be > 0 and no longer than half of
        the file, because each half has to supply that many samples.
    curve:
        ``"equal_power"`` (default) or ``"linear"``. See :func:`crossfade_gains`.
    safety_fade_s:
        Length of a tiny fade at the very start and end of the output, to
        avoid clicks. ``0`` disables it.

    Returns
    -------
    numpy.ndarray
        Float64 array with the same number of dimensions/channels as the input
        and ``frames - crossfade_samples`` frames.

    Raises
    ------
    ValueError
        If any argument is out of range (with a message that says why).
    """
    audio = np.asarray(audio)
    if audio.ndim not in (1, 2):
        raise ValueError(f"Audio must be 1-D (mono) or 2-D (frames, channels); got shape {audio.shape}.")
    if sample_rate <= 0:
        raise ValueError(f"Sample rate must be positive; got {sample_rate}.")
    if crossfade_s <= 0:
        raise ValueError(f"Crossfade must be greater than 0 seconds; got {crossfade_s}.")
    if safety_fade_s < 0:
        raise ValueError(f"Safety fade cannot be negative; got {safety_fade_s}.")
    if curve not in CURVES:
        raise ValueError(f"Unknown curve {curve!r}. Choose one of: {', '.join(CURVES)}.")

    audio = audio.astype(np.float64, copy=False)
    n_frames = audio.shape[0]
    mid = n_frames // 2  # A = audio[:mid], B = audio[mid:] (B gets the extra frame if odd)

    xfade = int(round(crossfade_s * sample_rate))
    # Each half must provide `xfade` samples. A is the shorter half when the
    # length is odd, so it sets the limit.
    max_xfade = mid
    if xfade < 1:
        raise ValueError(f"Crossfade of {crossfade_s} s is shorter than one sample at {sample_rate} Hz.")
    if xfade > max_xfade:
        raise ValueError(
            f"Crossfade too long: {crossfade_s:.3f} s ({xfade} samples) but it can be at most half "
            f"the file length, {max_xfade / sample_rate:.3f} s ({max_xfade} samples)."
        )

    first_half = audio[:mid]   # A
    second_half = audio[mid:]  # B

    fade_out, fade_in = crossfade_gains(xfade, curve)
    if audio.ndim == 2:
        # Broadcast the 1-D gain ramps across all channels.
        fade_out = fade_out[:, np.newaxis]
        fade_in = fade_in[:, np.newaxis]

    # B's tail (the original file end) fades out while A's head (the original
    # file start) fades in.
    blended = second_half[-xfade:] * fade_out + first_half[:xfade] * fade_in

    looped = np.concatenate(
        [
            second_half[:-xfade],  # B without its tail
            blended,               # crossfade region (old end -> old start)
            first_half[xfade:],    # A without its head
        ],
        axis=0,
    )

    safety_samples = int(round(safety_fade_s * sample_rate))
    return apply_safety_fades(looped, safety_samples)
