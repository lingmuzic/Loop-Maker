"""Feature A: output presets, resampling, dither, and the JSON preset file."""

import json
import math

import numpy as np
import pytest
import soundfile as sf

from loopmaker import cli, convert, pipeline, presets
from loopmaker.quality import measure_wrap


def write_noise(path, sr=96_000, seconds=3.0, channels=2, subtype="PCM_24", seed=0):
    rng = np.random.default_rng(seed)
    data = rng.standard_normal((int(seconds * sr), channels)) * 0.1
    sf.write(str(path), data[:, 0] if channels == 1 else data, sr, subtype=subtype)
    return path


# --- Preset values ----------------------------------------------------------

def test_builtin_preset_values():
    p = presets.BUILTIN_PRESETS
    assert (p["Wwise"].spec.sample_rate, p["Wwise"].spec.bit_depth, p["Wwise"].spec.file_format) == (48000, "24", "WAV")
    assert (p["Unreal"].spec.sample_rate, p["Unreal"].spec.bit_depth, p["Unreal"].spec.dither) == (48000, "16", "always")
    for name in ("Unity", "Godot"):
        assert p[name].spec.sample_rate == 48000 and p[name].spec.file_format == "WAV"
        assert p[name].spec.bit_depth is None  # keep source bit depth


@pytest.mark.parametrize(
    "preset, expected_subtype",
    [("Wwise", "PCM_24"), ("Unreal", "PCM_16"), ("Unity", "FLOAT"), ("Godot", "FLOAT")],
)
def test_presets_write_the_promised_format(tmp_path, preset, expected_subtype):
    src = write_noise(tmp_path / "src.wav", sr=96_000, subtype="FLOAT")
    settings = pipeline.LoopSettings(crossfade_s=0.5, out_dir=tmp_path / "out",
                                     output=presets.BUILTIN_PRESETS[preset].spec)
    out = pipeline.process_file(src, settings).outputs[0]
    info = sf.info(str(out.output_path))
    assert info.samplerate == 48_000
    assert info.subtype == expected_subtype
    assert info.channels == 2


def test_spec_validation():
    assert presets.OutputSpec(48000, "24", "WAV").errors() == []
    assert any("FLAC cannot store" in e for e in presets.OutputSpec(None, "32f", "FLAC").errors())
    assert presets.OutputSpec(22050).errors()  # not an offered rate
    assert presets.OutputSpec(file_format="MP3").errors()


@pytest.mark.parametrize("fmt, ext, subtype", [("FLAC", ".flac", "PCM_16"), ("OGG", ".ogg", "VORBIS")])
def test_custom_flac_and_ogg(tmp_path, fmt, ext, subtype):
    if fmt not in presets.available_formats():
        pytest.skip(f"{fmt} not supported by this libsndfile")
    src = write_noise(tmp_path / "src.wav", sr=48_000, subtype="PCM_16")
    spec = presets.OutputSpec(None, None, fmt)
    out = pipeline.process_file(src, pipeline.LoopSettings(crossfade_s=0.5, out_dir=tmp_path, output=spec)).outputs[0]
    assert out.output_path.suffix == ext
    assert sf.info(str(out.output_path)).subtype == subtype


# --- Dither -----------------------------------------------------------------

def test_dither_decision():
    assert convert.should_dither("auto", "PCM_24", 16)
    assert convert.should_dither("auto", "FLOAT", 24)
    assert not convert.should_dither("auto", "PCM_16", 16)
    assert not convert.should_dither("auto", "PCM_16", 24)
    assert convert.should_dither("always", "PCM_16", 16)
    assert not convert.should_dither("never", "FLOAT", 16)


def test_tpdf_dither_statistics():
    # Rounding error alone has variance 1/12 LSB^2; TPDF adds 1/6 -> 1/4 in total,
    # and the error has no DC offset.
    x = np.random.default_rng(1).uniform(-0.5, 0.5, 400_000)
    q, _ = convert.quantize(x, 16, dither=True, rng=np.random.default_rng(2))
    error = q.astype(np.float64) - x * 32768
    assert error.mean() == pytest.approx(0.0, abs=0.01)
    assert error.var() == pytest.approx(0.25, rel=0.02)
    assert np.max(np.abs(error)) <= 1.5  # +/-1 LSB dither +/-0.5 LSB rounding


def test_dither_keeps_low_level_detail():
    # A sine of 0.4 LSB vanishes completely without dither, survives with it.
    t = np.arange(48_000) / 48_000
    x = 0.4 / 32768 * np.sin(2 * np.pi * 440 * t)
    plain, _ = convert.quantize(x, 16, dither=False)
    dithered, _ = convert.quantize(x, 16, dither=True, rng=np.random.default_rng(0))
    assert not plain.any()
    # Projecting onto the sine recovers its amplitude (0.4 LSB) on average;
    # require at least half of it to be clearly above the dither noise.
    reference = np.sin(2 * np.pi * 440 * t)
    recovered_amplitude = np.dot(dithered.astype(float), reference) / np.dot(reference, reference)
    assert recovered_amplitude > 0.2


def test_dithered_16_bit_output_has_no_clipping(tmp_path):
    # Near full scale: dither must never wrap around or push past full scale.
    sr = 48_000
    t = np.arange(sr * 3) / sr
    loud = 0.995 * np.sin(2 * np.pi * 100 * t)
    src = tmp_path / "loud.wav"
    sf.write(str(src), loud, sr, subtype="PCM_24")
    settings = pipeline.LoopSettings(crossfade_s=0.5, curve="linear", out_dir=tmp_path / "out",
                                     output=presets.BUILTIN_PRESETS["Unreal"].spec)
    out = pipeline.process_file(src, settings).outputs[0]
    assert out.dithered
    assert not any("clipped" in n for n in out.notes)
    ints, _ = sf.read(str(out.output_path), dtype="int16")
    assert ints.max() <= 32767 and ints.min() >= -32768
    assert np.max(np.abs(ints)) > 32000  # still loud, i.e. not silently attenuated


def test_24_bit_quantisation_round_trips_exactly(tmp_path):
    x = np.random.default_rng(3).uniform(-0.9, 0.9, 10_000)
    q, _ = convert.quantize(x, 24, dither=False)
    path = tmp_path / "q.wav"
    sf.write(str(path), q, 48_000, subtype="PCM_24")
    back, _ = sf.read(str(path), dtype="int32")
    np.testing.assert_array_equal(back >> 8, q >> 8)
    np.testing.assert_allclose((q >> 8) / 2**23, x, atol=1 / 2**23)


# --- Resampling -------------------------------------------------------------

@pytest.mark.parametrize("src_rate, dst_rate", [(44_100, 48_000), (96_000, 48_000), (48_000, 44_100)])
def test_resample_length_and_pitch(src_rate, dst_rate):
    n = src_rate * 2
    tone = np.sin(2 * np.pi * 1000 * np.arange(n) / src_rate)
    out = convert.resample(tone, src_rate, dst_rate)
    assert len(out) == math.ceil(n * dst_rate / src_rate)
    spectrum = np.abs(np.fft.rfft(out * np.hanning(len(out))))
    peak_hz = np.argmax(spectrum) * dst_rate / len(out)
    assert peak_hz == pytest.approx(1000, abs=1.0)  # same pitch, not sped up/down


def test_resampled_output_length_and_seamless_wrap(tmp_path):
    src = write_noise(tmp_path / "a.wav", sr=44_100, seconds=4.0)
    spec = presets.OutputSpec(48_000, "24", "WAV")
    settings = pipeline.LoopSettings(crossfade_s=1.0, safety_fade_ms=0, out_dir=tmp_path, output=spec)
    out = pipeline.process_file(src, settings).outputs[0]
    data, sr = sf.read(str(out.output_path))
    assert sr == 48_000
    assert len(data) == math.ceil(4.0 * 44_100 * 48_000 / 44_100) - 48_000
    assert measure_wrap(data, sr).jump_ratio <= 1.0


# --- JSON preset file -------------------------------------------------------

def test_missing_preset_file_falls_back(tmp_path):
    loaded, warnings = presets.load_presets(tmp_path / "nope.json")
    assert loaded == presets.BUILTIN_PRESETS
    assert "not found" in warnings[0]


@pytest.mark.parametrize("content", ["{not json", "[]", '{"presets": 5}', '{"presets": {}}'])
def test_invalid_preset_file_falls_back(tmp_path, content):
    path = tmp_path / "presets.json"
    path.write_text(content, encoding="utf-8")
    loaded, warnings = presets.load_presets(path)
    assert loaded == presets.BUILTIN_PRESETS
    assert warnings


def test_bad_entry_is_skipped_good_entries_kept(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text(json.dumps({"presets": {
        "Studio": {"sample_rate": 96000, "bit_depth": "24", "format": "FLAC"},
        "Broken": {"sample_rate": 12345},
        "Custom": {"sample_rate": 48000},
    }}), encoding="utf-8")
    loaded, warnings = presets.load_presets(path)
    assert list(loaded) == ["Studio"]
    assert loaded["Studio"].spec == presets.OutputSpec(96000, "24", "FLAC", "auto")
    assert any("Broken" in w for w in warnings) and any("reserved" in w for w in warnings)


def test_shipped_preset_file_matches_builtins(tmp_path):
    loaded, warnings = presets.load_presets()  # the repo's presets.json
    assert warnings == []
    assert {k: v.spec for k, v in loaded.items()} == {k: v.spec for k, v in presets.BUILTIN_PRESETS.items()}
    path = tmp_path / "round.json"
    path.write_text(presets.presets_to_json(loaded), encoding="utf-8")
    assert presets.load_presets(path)[0] == loaded


# --- CLI --------------------------------------------------------------------

def test_cli_rejects_custom_fields_with_a_named_preset(tmp_path, capsys):
    src = write_noise(tmp_path / "a.wav", sr=48_000)
    code = cli.main([str(src), "--preset", "Unreal", "--bit-depth", "24", "--out", str(tmp_path)])
    assert code == 2
    assert "only apply to --preset Custom" in capsys.readouterr().err


def test_cli_preset_wwise(tmp_path):
    src = write_noise(tmp_path / "a.wav", sr=96_000, subtype="FLOAT")
    assert cli.main([str(src), "--preset", "Wwise", "--crossfade", "0.5", "--out", str(tmp_path / "o")]) == 0
    info = sf.info(str(tmp_path / "o" / "a_loop.wav"))
    assert (info.samplerate, info.subtype) == (48_000, "PCM_24")
