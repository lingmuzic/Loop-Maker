"""Format conversion DSP: sample-rate conversion and dithered quantisation.

Pure numpy/scipy, no file I/O, so every step can be tested on arrays.

Resampling
    Polyphase resampling (``scipy.signal.resample_poly``) with a Kaiser
    window (beta 9, roughly 90 dB stop-band attenuation), using the exact
    rational ratio between the two rates (e.g. 44100 -> 48000 is 160/147).
    The pipeline resamples the *source* before looping. The filter's edge
    transients then land on the original file start/end, which end up inside
    the crossfade at near-zero gain, so the new loop point stays clean.

Dither
    TPDF (triangular) dither of +/-1 LSB: the sum of two independent uniform
    variables, the standard choice for reducing word length. It turns
    truncation distortion into a constant, signal-independent noise floor
    (~-96 dBFS for 16-bit, which is inaudible under ambience but avoids
    "grainy" fade tails). We quantise ourselves instead of letting libsndfile
    do it, so rounding, dither and clipping are explicit and testable.
"""

from __future__ import annotations

from math import gcd

import numpy as np
from scipy.signal import resample_poly

RESAMPLE_WINDOW = ("kaiser", 9.0)

# Effective resolution of source encodings, used to decide whether writing a
# given bit depth is a *reduction* (which calls for dither).
_SOURCE_BITS = {
    "PCM_U8": 8, "PCM_S8": 8,
    "PCM_16": 16,
    "PCM_24": 24,
    "PCM_32": 32,
    "FLOAT": 32, "DOUBLE": 64,
}
#: Assumed resolution for decoded lossy/unknown sources (e.g. Ogg Vorbis): high.
UNKNOWN_SOURCE_BITS = 32


def resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Resample ``audio`` (``(frames,)`` or ``(frames, channels)``) to ``target_rate``.

    Returns the input unchanged when the rates match. The output has
    ``ceil(frames * target_rate / source_rate)`` frames.
    """
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError(f"Sample rates must be positive; got {source_rate} -> {target_rate}.")
    if source_rate == target_rate:
        return audio
    divisor = gcd(int(source_rate), int(target_rate))
    up, down = target_rate // divisor, source_rate // divisor
    return resample_poly(np.asarray(audio, dtype=np.float64), up, down, axis=0, window=RESAMPLE_WINDOW)


def source_bits(subtype: str) -> int:
    """Effective bit depth of a soundfile subtype (high for float/lossy)."""
    return _SOURCE_BITS.get(subtype, UNKNOWN_SOURCE_BITS)


def should_dither(mode: str, source_subtype: str, target_bits: int) -> bool:
    """Decide whether to dither when writing ``target_bits`` integer PCM.

    ``mode``:
    * ``"auto"``   - only when the bit depth is reduced (e.g. 24 -> 16, float -> 24)
    * ``"always"`` - every integer output (also e.g. 16 -> 16 after processing)
    * ``"never"``  - plain rounding
    """
    if mode == "always":
        return True
    if mode == "never":
        return False
    if mode == "auto":
        return source_bits(source_subtype) > target_bits
    raise ValueError(f"Unknown dither mode {mode!r}; use auto, always or never.")


def quantize(
    audio: np.ndarray, bits: int, dither: bool, rng: np.random.Generator | None = None
) -> tuple[np.ndarray, int]:
    """Convert float audio in [-1, 1) to signed integers of ``bits`` bits.

    Returns ``(integers, overs)`` where ``overs`` counts samples that were
    beyond full scale *before* dither (real overloads worth reporting). The
    result is always clipped to the legal range, so nothing ever wraps around.

    The integers are scaled for soundfile: int16 for 16-bit, and int32 with
    the 24-bit value in the top bytes for 24-bit (how libsndfile reads int32
    input for a PCM_24 file).
    """
    if bits not in (16, 24):
        raise ValueError(f"Integer quantisation supports 16 or 24 bits; got {bits}.")
    data = np.asarray(audio, dtype=np.float64)
    full_scale = 2 ** (bits - 1)
    overs = int(np.count_nonzero(np.abs(data) > 1.0))

    scaled = data * full_scale
    if dither:
        rng = rng if rng is not None else np.random.default_rng()
        # TPDF: difference of two uniform [0, 1) variables -> triangular on (-1, 1) LSB.
        scaled = scaled + rng.random(data.shape) - rng.random(data.shape)

    quantised = np.clip(np.round(scaled), -full_scale, full_scale - 1)
    if bits == 16:
        return quantised.astype(np.int16), overs
    return (quantised.astype(np.int32) << 8), overs  # 24-bit value in the top 3 bytes
