"""Tests for automatic crossfade-curve selection."""

import numpy as np
import pytest

from loopmaker import core

SR = 48_000


def in_phase_tone(seconds=4.0, freq=220.0):
    """A steady tone whose end and start line up in phase for a 1 s crossfade.

    The crossfade overlaps samples [n - C, n) with [0, C). With n = 4 s and
    C = 1 s that is an offset of 3 s = 660 whole cycles of 220 Hz.
    """
    t = np.arange(int(seconds * SR)) / SR
    return 0.5 * np.sin(2 * np.pi * freq * t)


def noise(seconds=4.0, channels=None, seed=0):
    rng = np.random.default_rng(seed)
    shape = (int(seconds * SR),) if channels is None else (int(seconds * SR), channels)
    return rng.standard_normal(shape) * 0.1


def mid_crossfade_level_db(audio, crossfade_s, curve):
    """Level of the crossfade centre relative to the untouched material, in dB."""
    y = core.make_loop(audio, SR, crossfade_s, curve=curve, safety_fade_s=0)
    xfade = int(crossfade_s * SR)
    b_len = len(audio) - len(audio) // 2
    centre = b_len - xfade // 2
    half = int(0.05 * SR)  # 100 ms window around the centre
    rms = lambda x: np.sqrt(np.mean(np.square(x)))  # noqa: E731
    return 20 * np.log10(rms(y[centre - half:centre + half]) / rms(audio))


def test_correlated_tone_gets_linear():
    curve, r = core.resolve_curve(in_phase_tone(), SR, 1.0, "auto")
    assert r > 0.99
    assert curve == "linear"


def test_uncorrelated_noise_gets_equal_power():
    curve, r = core.resolve_curve(noise(), SR, 1.0, "auto")
    assert abs(r) < 0.1
    assert curve == "equal_power"


def test_tone_90_degrees_out_of_phase_counts_as_uncorrelated():
    # Tonal, but its end and start are in quadrature: at zero lag it adds
    # like noise, so equal power is the right call.
    tone = in_phase_tone(freq=220.0 + 1 / 12)  # +1/12 Hz over 3 s = a quarter-cycle drift
    curve, r = core.resolve_curve(tone, SR, 1.0, "auto")
    assert abs(r) < 0.1
    assert curve == "equal_power"


@pytest.mark.parametrize("make_audio", [in_phase_tone, noise], ids=["tone", "noise"])
def test_auto_keeps_crossfade_level_flat(make_audio):
    """The point of the feature: no dip or bump in the middle of the crossfade."""
    audio = make_audio()
    auto_db = mid_crossfade_level_db(audio, 1.0, "auto")
    assert abs(auto_db) < 0.5

    # And the wrong choice really would have been audible (about 3 dB off).
    wrong = "equal_power" if make_audio is in_phase_tone else "linear"
    assert abs(mid_crossfade_level_db(audio, 1.0, wrong)) > 2.0


def test_threshold_is_where_both_curves_err_equally():
    r = core.AUTO_CURVE_THRESHOLD
    equal_power_error = 10 * np.log10(1 + r)
    linear_error = 10 * np.log10((1 + r) / 2)
    assert equal_power_error == pytest.approx(-linear_error)
    assert core.choose_curve(r + 0.01) == "linear"
    assert core.choose_curve(r - 0.01) == "equal_power"
    assert core.choose_curve(-0.8) == "equal_power"


def test_multichannel_gets_one_pooled_correlation():
    stereo = np.stack([in_phase_tone(), in_phase_tone()], axis=1)
    curve, r = core.resolve_curve(stereo, SR, 1.0, "auto")
    assert curve == "linear" and r > 0.99


def test_silence_does_not_crash():
    curve, r = core.resolve_curve(np.zeros(SR * 2), SR, 0.5, "auto")
    assert r == 0.0 and curve == "equal_power"


def test_forced_curve_is_respected_but_correlation_still_reported():
    curve, r = core.resolve_curve(in_phase_tone(), SR, 1.0, "equal_power")
    assert curve == "equal_power" and r > 0.99
