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

#: The two fade shapes that can actually be applied.
FADE_SHAPES = ("equal_power", "linear")

#: Names accepted by :func:`make_loop` for the ``curve`` argument.
#: ``"auto"`` picks one of :data:`FADE_SHAPES` per file, see :func:`choose_curve`.
CURVES = ("auto",) + FADE_SHAPES

DEFAULT_CURVE = "auto"
DEFAULT_SAFETY_FADE_S = 0.005  # 5 ms

#: Correlation above which "linear" beats "equal_power" (see choose_curve).
AUTO_CURVE_THRESHOLD = float(np.sqrt(2.0) - 1.0)  # ~0.414


def crossfade_gains(length: int, curve: str = "equal_power") -> tuple[np.ndarray, np.ndarray]:
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
        raise ValueError(f"Unknown fade shape {curve!r}. Choose one of: {', '.join(FADE_SHAPES)}.")

    return fade_out, fade_in


def crossfade_length(n_frames: int, sample_rate: int, crossfade_s: float) -> int:
    """Convert ``crossfade_s`` to samples and check it fits in the file.

    Each half must supply that many samples. The first half (A) is the
    shorter one when the length is odd, so it sets the limit.
    """
    if sample_rate <= 0:
        raise ValueError(f"Sample rate must be positive; got {sample_rate}.")
    if crossfade_s <= 0:
        raise ValueError(f"Crossfade must be greater than 0 seconds; got {crossfade_s}.")

    xfade = int(round(crossfade_s * sample_rate))
    max_xfade = n_frames // 2
    if xfade < 1:
        raise ValueError(f"Crossfade of {crossfade_s} s is shorter than one sample at {sample_rate} Hz.")
    if xfade > max_xfade:
        raise ValueError(
            f"Crossfade too long: {crossfade_s:.3f} s ({xfade} samples) but it can be at most half "
            f"the file length, {max_xfade / sample_rate:.3f} s ({max_xfade} samples)."
        )
    return xfade


def crossfade_correlation(audio: np.ndarray, sample_rate: int, crossfade_s: float) -> float:
    """Correlation of the two signals that will be crossfaded, in [-1, 1].

    The two signals are the original file's **end** (B's tail, fading out)
    and its **start** (A's head, fading in), sample-aligned exactly as
    :func:`make_loop` will overlap them.

    Why zero-lag correlation and not "is the material tonal?": the level in
    the middle of a crossfade depends on how the two signals *add up sample
    by sample*. Two hums that happen to be 90 degrees out of phase are tonal
    but uncorrelated at zero lag, and they really do behave like noise in the
    crossfade. So we measure the thing that actually decides the outcome.

    The correlation is weighted with a Hann window, because the two signals
    only interact where both gains are large (the middle of the crossfade);
    near the edges one of them is almost silent. All channels are pooled
    into one number so a multichannel file gets one consistent curve.
    """
    data = np.asarray(audio, dtype=np.float64)
    if data.ndim == 1:
        data = data[:, np.newaxis]
    xfade = crossfade_length(data.shape[0], sample_rate, crossfade_s)

    fading_out = data[-xfade:]   # original end   = tail of B
    fading_in = data[:xfade]     # original start = head of A

    weight = np.sin(np.pi * (np.arange(xfade) + 0.5) / xfade)[:, np.newaxis]  # Hann-shaped
    cross = np.sum(weight * fading_out * fading_in)
    energy = np.sqrt(np.sum(weight * fading_out**2) * np.sum(weight * fading_in**2))
    if energy == 0.0:
        return 0.0  # digital silence on one side: nothing to correlate
    return float(np.clip(cross / energy, -1.0, 1.0))


def choose_curve(correlation: float) -> str:
    """Pick the fade shape whose mid-crossfade level error is smallest.

    At the centre of the crossfade both gains are equal (g), so the power of
    the sum relative to either input is ``2 * g**2 * (1 + r)``:

    * equal power (g = 0.707):  ``1 + r``        -> 0 dB at r = 0, +3 dB at r = 1
    * linear      (g = 0.5):    ``(1 + r) / 2``  -> -3 dB at r = 0, 0 dB at r = 1

    The two errors are equally large (in dB) when ``(1 + r)**2 == 2``, i.e.
    ``r = sqrt(2) - 1 ~= 0.414``. Above that, linear is closer to constant
    level; below it (including negative r), equal power is.
    """
    return "linear" if correlation > AUTO_CURVE_THRESHOLD else "equal_power"


def resolve_curve(
    audio: np.ndarray, sample_rate: int, crossfade_s: float, curve: str = DEFAULT_CURVE
) -> tuple[str, float]:
    """Return ``(fade_shape, correlation)`` for ``curve`` (which may be ``"auto"``).

    The correlation is always measured so it can be reported, even when the
    user forced a specific shape.
    """
    if curve not in CURVES:
        raise ValueError(f"Unknown curve {curve!r}. Choose one of: {', '.join(CURVES)}.")
    correlation = crossfade_correlation(audio, sample_rate, crossfade_s)
    shape = choose_curve(correlation) if curve == "auto" else curve
    return shape, correlation


def apply_safety_fades(audio: np.ndarray, fade_samples: int) -> np.ndarray:
    """Apply a short linear fade-in at the start and fade-out at the end.

    Works on a copy and returns it. ``fade_samples == 0`` is a no-op.
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
    split_at: int | None = None,
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
        ``"auto"`` (default: chosen from the material, see :func:`choose_curve`),
        ``"equal_power"`` or ``"linear"``. See :func:`crossfade_gains`.
    safety_fade_s:
        Length of a tiny fade at the very start and end of the output, to
        avoid clicks. ``0`` disables it.
    split_at:
        Frame where A ends and B begins. ``None`` (default) splits at the
        midpoint. Any other split gives a *rotation* of the same loop: the
        crossfade blends the same original end/start, only the point where
        the output file begins (and wraps) moves. Used for loop variations.
        Both A and B must be at least as long as the crossfade.

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
    if safety_fade_s < 0:
        raise ValueError(f"Safety fade cannot be negative; got {safety_fade_s}.")

    audio = audio.astype(np.float64, copy=False)
    xfade = crossfade_length(audio.shape[0], sample_rate, crossfade_s)
    shape, _ = resolve_curve(audio, sample_rate, crossfade_s, curve)

    n_frames = audio.shape[0]
    # A = audio[:split], B = audio[split:]. At the midpoint B gets the extra
    # frame when the length is odd.
    split = n_frames // 2 if split_at is None else int(split_at)
    if split_at is not None and not (xfade <= split <= n_frames - xfade):
        raise ValueError(
            f"Split point {split / sample_rate:.3f} s leaves less than the {xfade / sample_rate:.3f} s "
            f"crossfade on one side; it must lie between {xfade / sample_rate:.3f} s and "
            f"{(n_frames - xfade) / sample_rate:.3f} s."
        )
    first_half = audio[:split]   # A
    second_half = audio[split:]  # B

    fade_out, fade_in = crossfade_gains(xfade, shape)
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
