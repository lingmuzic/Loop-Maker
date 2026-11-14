"""Feature B: loop variations (rotated split points at quiet spots)."""

import numpy as np
import pytest
import soundfile as sf

from loopmaker import cli, core, naming, pipeline, variations
from loopmaker.quality import measure_wrap

SR = 8_000  # low rate keeps the tests fast; the algorithm is rate-independent


def bed(seconds, seed=0):
    return np.random.default_rng(seed).standard_normal(int(seconds * SR)) * 0.05


def with_quiet_gaps(seconds, gaps_s, gap_len_s=1.0, seed=0):
    """Loud-ish noise everywhere except a few quiet gaps at known times."""
    x = np.random.default_rng(seed).standard_normal(int(seconds * SR)) * 0.3
    for start in gaps_s:
        a, b = int(start * SR), int((start + gap_len_s) * SR)
        x[a:b] *= 0.02
    return x


# --- Split point selection ---------------------------------------------------

def test_split_points_respect_spacing_and_bounds():
    x = bed(60)
    xfade_s, spacing = 2.0, 0.12
    points = variations.choose_split_points(x, SR, xfade_s, 5, spacing)
    assert len(points) == 5 and points == sorted(points)
    assert np.min(np.diff(points)) >= spacing * len(x)
    assert min(points) >= xfade_s * SR and max(points) <= len(x) - xfade_s * SR
    assert all(abs(p - len(x) // 2) >= 0.01 * len(x) for p in points)  # not the midpoint


def test_split_points_land_in_quiet_valleys():
    gaps = [6.0, 17.0, 33.0, 46.0]
    x = with_quiet_gaps(60, gaps)
    points = variations.choose_split_points(x, SR, 2.0, 4, 0.12)
    for p in points:
        assert any(g * SR <= p <= (g + 1.0) * SR for g in gaps), p / SR


def test_split_points_avoid_loud_transients():
    x = bed(40)
    # A few loud clicks; the quietest smoothed spots may sit right next to them.
    clicks = [5.0, 12.5, 21.0, 29.5]
    for c in clicks:
        i = int(c * SR)
        x[i:i + 80] += 0.9 * np.hanning(80)
        x[i - 4000:i] *= 0.2       # quiet just *before* each click
    points = variations.choose_split_points(x, SR, 1.0, 3, 0.12)
    for p in points:
        assert all(abs(p / SR - c) > variations.TRANSIENT_GUARD_S for c in clicks), p / SR


# --- Length checks -----------------------------------------------------------

def test_too_short_source_is_rejected_with_the_numbers():
    with pytest.raises(variations.VariationError) as err:
        variations.check_length(duration_s=10.0, crossfade_s=2.0, count=5, min_spacing=0.12)
    message = str(err.value)
    assert "2 x 2 s crossfade" in message
    assert "5 x 1.2 s minimum spacing" in message
    assert "= 10.0 s" in message and "file is 10.0 s" in message


def test_just_long_enough_source_works():
    variations.check_length(duration_s=10.1, crossfade_s=2.0, count=5, min_spacing=0.12)
    assert len(variations.choose_split_points(bed(10.1), SR, 2.0, 5, 0.12)) == 5


def test_max_variations():
    assert variations.max_variations(30, 2.0, 0.12) == 5
    # 7.5 s: five need > 3 + 5 x 0.9 = 7.5 s (fails), four need > 3 + 4 x 0.9 = 6.6 s (fits)
    assert variations.max_variations(7.5, 1.5, 0.12) == 4
    assert variations.max_variations(4, 1.5, 0.12) == 0


def test_short_source_warning_is_configurable():
    assert variations.short_source_warning(12.0) is not None
    assert "nearly identical" in variations.short_source_warning(12.0)
    assert variations.short_source_warning(12.0, threshold_s=10.0) is None
    assert variations.short_source_warning(40.0) is None


# --- Each variation is a seamless loop --------------------------------------

def test_variation_is_the_same_loop_rotated():
    x = bed(30)
    standard = core.make_loop(x, SR, 2.0, safety_fade_s=0)
    split = int(9.3 * SR)
    variation = core.make_loop(x, SR, 2.0, safety_fade_s=0, split_at=split)
    np.testing.assert_allclose(variation, np.roll(standard, len(x) // 2 - split))


def test_every_variation_wraps_without_discontinuity():
    x = bed(40) * np.linspace(0.3, 1.0, int(40 * SR))  # drifting level: a raw loop jumps
    for p in variations.choose_split_points(x, SR, 2.0, 5, 0.12):
        y = core.make_loop(x, SR, 2.0, safety_fade_s=0, split_at=p)
        assert y[0] == x[p] and y[-1] == x[p - 1]  # original neighbours meet at the wrap
        assert measure_wrap(y, SR).is_smooth


def test_split_too_close_to_an_edge_raises():
    with pytest.raises(ValueError, match="Split point"):
        core.make_loop(bed(10), SR, 2.0, split_at=int(1.0 * SR))


# --- Pipeline / naming / CLI -------------------------------------------------

def test_pipeline_writes_n_distinct_variations(tmp_path):
    src = tmp_path / "Desert Wind.wav"
    sf.write(str(src), bed(40), SR, subtype="PCM_24")
    settings = pipeline.LoopSettings(
        crossfade_s=2.0, out_dir=tmp_path / "out", variations=4,
        naming=naming.Naming("AMB_{name}_{index}_loop"),
    )
    result = pipeline.process_file(src, settings)
    names = [o.output_path.name for o in result.outputs]
    assert names == [f"AMB_Desert Wind_01_loop_v{i:02d}.wav" for i in range(1, 5)]

    audio = [sf.read(str(o.output_path))[0] for o in result.outputs]
    assert all(len(a) == int(40 * SR) - 2 * SR for a in audio)
    for i in range(4):
        for j in range(i + 1, 4):
            assert not np.allclose(audio[i], audio[j])  # really different starting points

    # Reported crossfade region and split are consistent with the rotation.
    for o in result.outputs:
        frames = int(40 * SR)
        assert o.crossfade_end_s == pytest.approx((frames - o.split_s * SR) / SR)
        assert o.crossfade_end_s - o.crossfade_start_s == pytest.approx(2.0)
    assert result.notes == []  # 40 s source: no short-source warning


def test_short_source_gets_a_warning_but_is_processed(tmp_path):
    src = tmp_path / "short.wav"
    sf.write(str(src), bed(12), SR)
    result = pipeline.process_file(src, pipeline.LoopSettings(crossfade_s=1.0, out_dir=tmp_path, variations=3))
    assert len(result.outputs) == 3
    assert any("nearly identical" in n for n in result.notes)


def test_invalid_variation_count_is_a_settings_error():
    assert pipeline.LoopSettings(variations=2).errors()
    assert pipeline.LoopSettings(variations=6).errors()
    assert pipeline.LoopSettings(variations=3).errors() == []


def test_cli_variations(tmp_path, capsys):
    src = tmp_path / "rain.wav"
    sf.write(str(src), bed(30), SR)
    out = tmp_path / "out"
    assert cli.main([str(src), "--variations", "3", "--crossfade", "1", "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == [f"rain_loop_v0{i}.wav" for i in (1, 2, 3)]

    tiny = tmp_path / "tiny.wav"
    sf.write(str(tiny), bed(5), SR)
    assert cli.main([str(tiny), "--variations", "5", "--crossfade", "1", "--out", str(out)]) == 1
    assert "too short for 5 variations" in capsys.readouterr().err
