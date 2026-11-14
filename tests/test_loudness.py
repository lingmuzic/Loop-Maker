"""Tests for loudness measurement and batch normalisation."""

import numpy as np
import pytest
import soundfile as sf

from loopmaker import loudness, pipeline

SR = 48_000


def sine(seconds=5.0, peak_db=-20.0, freq=997.0):
    t = np.arange(int(seconds * SR)) / SR
    return 10 ** (peak_db / 20) * np.sin(2 * np.pi * freq * t)


def test_reference_tone_measures_as_expected():
    # BS.1770: a 1 kHz sine at -20 dBFS peak in one channel reads about -23 LUFS.
    assert loudness.integrated_loudness(sine(), SR) == pytest.approx(-23.0, abs=0.1)


def test_true_peak_catches_inter_sample_peaks():
    # fs/4 sine sampled at +/-45 degrees: every sample is 0.707 of the real peak.
    n = np.arange(SR)
    x = np.sin(2 * np.pi * n / 4 + np.pi / 4)
    sample_peak_db = 20 * np.log10(np.max(np.abs(x)))
    assert sample_peak_db == pytest.approx(-3.0, abs=0.1)
    assert loudness.true_peak_db(x) == pytest.approx(0.0, abs=0.2)


def test_chunked_true_peak_matches_whole_file():
    from scipy.signal import resample_poly

    x = np.random.default_rng(5).standard_normal((SR * 3, 2)) * 0.2
    whole = 20 * np.log10(np.max(np.abs(resample_poly(x, 4, 1, axis=0))))
    assert loudness.true_peak_db(x, chunk_frames=10_000) == pytest.approx(whole, abs=0.01)


def test_normalize_reaches_target():
    out, info = loudness.normalize(sine(peak_db=-35), SR, target_lufs=-23)
    assert info.final_lufs == pytest.approx(-23.0, abs=0.01)
    assert loudness.integrated_loudness(out, SR) == pytest.approx(-23.0, abs=0.05)
    assert not info.limited_by_peak
    assert info.shortfall_db == 0.0


def test_peak_ceiling_wins_over_target():
    # The -20 dBFS sine reads -23 LUFS; -3 LUFS would need +20 dB, i.e. a 0 dBTP
    # peak, which is above the -1 dBTP ceiling, so the gain must stop at +19 dB.
    out, info = loudness.normalize(sine(peak_db=-20), SR, target_lufs=-3, peak_ceiling_dbtp=-1)
    assert info.limited_by_peak
    # Gain stops at +19 dB, so the shortfall is target - (measured + 19).
    assert info.shortfall_db == pytest.approx(-3 - (info.measured_lufs + 19.0), abs=0.01)
    assert info.true_peak_after_dbtp == pytest.approx(-1.0, abs=0.01)
    assert loudness.true_peak_db(out) <= -1.0 + 0.05
    assert "short of target" in info.format()


def test_five_point_one_ignores_lfe():
    base = np.stack([sine()] * 5, axis=1)  # L R C Ls Rs
    with_lfe = np.insert(base, 3, sine(peak_db=0) * 10, axis=1)  # absurdly loud LFE
    assert loudness.integrated_loudness(with_lfe, SR) == pytest.approx(
        loudness.integrated_loudness(base, SR), abs=0.01
    )


@pytest.mark.parametrize("audio", [np.zeros(SR * 2), sine(seconds=0.2)], ids=["silent", "too_short"])
def test_unmeasurable_audio_raises_clear_error(audio):
    with pytest.raises(loudness.LoudnessError):
        loudness.integrated_loudness(audio, SR)


def test_batch_ends_up_level_matched(tmp_path):
    rng = np.random.default_rng(3)
    sources = []
    for name, level in [("quiet", 0.01), ("medium", 0.05), ("loud", 0.2)]:
        path = tmp_path / f"{name}.wav"
        sf.write(str(path), rng.standard_normal((SR * 6, 2)) * level, SR, subtype="PCM_24")
        sources.append(path)

    settings = pipeline.LoopSettings(crossfade_s=1.0, out_dir=tmp_path / "out", normalize_lufs=-24.0)
    results = [r for _, r in pipeline.process_batch(sources, settings)]

    for result in results:
        written, _ = sf.read(str(result.output_path))
        assert loudness.integrated_loudness(written, SR) == pytest.approx(-24.0, abs=0.1)
        assert "LUFS" in result.format()


def test_normalisation_off_by_default(tmp_path):
    path = tmp_path / "a.wav"
    sf.write(str(path), np.random.default_rng(0).standard_normal(SR * 4) * 0.05, SR)
    result = pipeline.process_file(path, pipeline.LoopSettings(crossfade_s=1.0, out_dir=tmp_path / "out"))
    assert result.normalization is None


def test_unmeasurable_file_is_still_written_with_a_note(tmp_path):
    path = tmp_path / "silent.wav"
    sf.write(str(path), np.zeros(SR * 2), SR)
    settings = pipeline.LoopSettings(crossfade_s=0.5, out_dir=tmp_path / "out", normalize_lufs=-23)
    result = pipeline.process_file(path, settings)
    assert result.output_path.exists()
    assert any("not loudness-normalised" in note for note in result.notes)
