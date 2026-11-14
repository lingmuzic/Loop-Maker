"""Tests for the pure-DSP core, using synthetic noise and sine signals."""

import numpy as np
import pytest

from loopmaker import core
from loopmaker.quality import measure_wrap

SR = 48_000


def noise(seconds: float, channels: int | None = None, seed: int = 0) -> np.ndarray:
    """Uncorrelated white noise, shaped like real ambience test material."""
    rng = np.random.default_rng(seed)
    shape = (int(seconds * SR),) if channels is None else (int(seconds * SR), channels)
    return rng.standard_normal(shape) * 0.1


def sine(seconds: float, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return 0.5 * np.sin(2 * np.pi * freq * t)


# --- Output length ----------------------------------------------------------

@pytest.mark.parametrize("xfade_s", [0.1, 1.0, 2.5])
def test_output_is_shorter_by_exactly_the_crossfade(xfade_s):
    x = noise(6.0)
    y = core.make_loop(x, SR, xfade_s)
    assert len(y) == len(x) - int(round(xfade_s * SR))


def test_odd_length_input():
    x = noise(1.0)[:-1]  # odd number of frames
    y = core.make_loop(x, SR, 0.25)
    assert len(y) == len(x) - int(0.25 * SR)


def test_crossfade_of_exactly_half_is_allowed():
    x = noise(2.0)
    y = core.make_loop(x, SR, 1.0)
    assert len(y) == len(x) // 2


# --- Seamless wrap point ----------------------------------------------------

def test_new_head_and_tail_are_the_original_midpoint_neighbours():
    x = noise(4.0)
    y = core.make_loop(x, SR, 1.0, safety_fade_s=0)
    mid = len(x) // 2
    assert y[0] == x[mid]
    assert y[-1] == x[mid - 1]


def test_no_discontinuity_at_wrap_point_sine():
    # A sine whose period does NOT divide the file length: looping the raw
    # file produces a big jump, the Loop Maker output must not.
    x = sine(3.0, freq=217.3)
    raw_jump = abs(x[0] - x[-1])
    y = core.make_loop(x, SR, 0.5, curve="linear", safety_fade_s=0)
    wrap_jump = abs(y[0] - y[-1])

    max_step = np.max(np.abs(np.diff(x)))  # largest step anywhere in a clean sine
    assert raw_jump > 10 * max_step        # sanity check: the raw loop really clicks
    assert wrap_jump <= max_step * 1.0001  # the new loop is no worse than a normal step


def test_no_discontinuity_at_wrap_point_noise():
    # Level drifts over the file so the raw head/tail levels differ a lot.
    x = noise(8.0) * np.linspace(0.1, 1.0, int(8.0 * SR))
    before = measure_wrap(x, SR)
    after = measure_wrap(core.make_loop(x, SR, 2.0, safety_fade_s=0), SR)

    assert abs(before.rms_diff_db) > 10    # raw loop: obvious level jump
    assert abs(after.rms_diff_db) < 1.0    # loop: levels match
    assert after.jump_ratio <= 1.0         # wrap step is an ordinary step
    assert after.is_smooth


def test_safety_fade_zeroes_the_edges_and_can_be_disabled():
    x = noise(2.0) + 0.5  # DC offset so edges are clearly non-zero
    faded = core.make_loop(x, SR, 0.5, safety_fade_s=0.005)
    assert faded[0] == 0.0 and faded[-1] == 0.0

    unfaded = core.make_loop(x, SR, 0.5, safety_fade_s=0)
    assert unfaded[0] != 0.0 and unfaded[-1] != 0.0


# --- Crossfade curves -------------------------------------------------------

def test_equal_power_gains_sum_to_unit_power():
    out, inn = core.crossfade_gains(1000, "equal_power")
    np.testing.assert_allclose(out**2 + inn**2, 1.0)


def test_linear_gains_sum_to_unity():
    out, inn = core.crossfade_gains(1000, "linear")
    np.testing.assert_allclose(out + inn, 1.0)


def test_unknown_curve_raises():
    with pytest.raises(ValueError, match="Unknown curve"):
        core.make_loop(noise(1.0), SR, 0.1, curve="s_curve")


# --- Validation -------------------------------------------------------------

def test_crossfade_longer_than_half_raises_clear_error():
    x = noise(2.0)
    with pytest.raises(ValueError, match="at most half the file length"):
        core.make_loop(x, SR, 1.01)


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_non_positive_crossfade_raises(bad):
    with pytest.raises(ValueError, match="greater than 0"):
        core.make_loop(noise(1.0), SR, bad)


def test_negative_safety_fade_raises():
    with pytest.raises(ValueError, match="negative"):
        core.make_loop(noise(1.0), SR, 0.1, safety_fade_s=-0.001)


# --- Multichannel -----------------------------------------------------------

@pytest.mark.parametrize("channels", [2, 6])
def test_multichannel_keeps_channel_count(channels):
    x = noise(3.0, channels=channels)
    y = core.make_loop(x, SR, 1.0)
    assert y.shape == (len(x) - SR, channels)


def test_multichannel_matches_per_channel_mono_processing():
    # Each channel must be processed independently and identically.
    x = noise(3.0, channels=4)
    y = core.make_loop(x, SR, 0.75)
    for ch in range(4):
        np.testing.assert_allclose(y[:, ch], core.make_loop(x[:, ch], SR, 0.75))


def test_mono_stays_one_dimensional():
    assert core.make_loop(noise(2.0), SR, 0.5).ndim == 1
