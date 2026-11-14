"""End-to-end tests: real files on disk (in pytest's tmp_path), CLI included."""

import numpy as np
import pytest
import soundfile as sf

from loopmaker import cli, pipeline
from loopmaker.audio_io import collect_inputs

SR = 44_100


def write_noise(path, seconds=4.0, channels=2, subtype="PCM_24", seed=0):
    rng = np.random.default_rng(seed)
    data = rng.standard_normal((int(seconds * SR), channels)) * 0.1
    if channels == 1:
        data = data[:, 0]
    sf.write(str(path), data, SR, subtype=subtype)
    return path


@pytest.mark.parametrize(
    "name, channels, subtype",
    [
        ("st24.wav", 2, "PCM_24"),
        ("mono16.wav", 1, "PCM_16"),
        ("float.wav", 2, "FLOAT"),
        ("st24.flac", 2, "PCM_24"),
        ("quad16.flac", 4, "PCM_16"),
    ],
)
def test_preserves_rate_channels_and_bit_depth(tmp_path, name, channels, subtype):
    src = write_noise(tmp_path / name, channels=channels, subtype=subtype)
    settings = pipeline.LoopSettings(crossfade_s=1.0, out_dir=tmp_path / "out")
    result = pipeline.process_file(src, settings)

    info = sf.info(str(result.output_path))
    assert result.output_path.name == f"{src.stem}_loop.wav"
    assert info.samplerate == SR
    assert info.channels == channels
    assert info.subtype == subtype
    assert info.frames == int(4.0 * SR) - SR


def test_input_file_is_never_modified(tmp_path):
    src = write_noise(tmp_path / "rain.wav")
    before = src.read_bytes()
    pipeline.process_file(src, pipeline.LoopSettings(crossfade_s=1.0, out_dir=tmp_path))
    assert src.read_bytes() == before
    assert (tmp_path / "rain_loop.wav").exists()


def test_refuses_to_overwrite_input(tmp_path):
    src = write_noise(tmp_path / "x_loop.wav")
    with pytest.raises(ValueError, match="overwrite"):
        pipeline.process_file(src, pipeline.LoopSettings(crossfade_s=1.0), output_path=src)


def test_batch_from_folder_continues_after_a_failure(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    write_noise(folder / "a.wav")
    write_noise(folder / "b.flac")
    write_noise(folder / "tiny.wav", seconds=0.5)  # too short for a 1 s crossfade
    (folder / "readme.txt").write_text("not audio")

    files, problems = collect_inputs([folder])
    assert [f.name for f in files] == ["a.wav", "b.flac", "tiny.wav"]
    assert problems == []

    results = dict(pipeline.process_batch(files, pipeline.LoopSettings(crossfade_s=1.0, out_dir=tmp_path / "out")))
    assert isinstance(results[folder / "tiny.wav"], ValueError)
    assert not isinstance(results[folder / "a.wav"], Exception)
    assert not isinstance(results[folder / "b.flac"], Exception)


def test_batch_avoids_output_name_collisions(tmp_path):
    a = write_noise(tmp_path / "rain.wav")
    b = write_noise(tmp_path / "rain.flac")
    out = tmp_path / "out"
    results = [r for _, r in pipeline.process_batch([a, b], pipeline.LoopSettings(crossfade_s=1.0, out_dir=out))]
    assert {r.output_path.name for r in results} == {"rain_loop.wav", "rain_loop_2.wav"}


def test_clipping_is_reported_for_integer_output(tmp_path):
    # A full-scale DC signal: the equal-power crossfade of two identical
    # signals peaks at +3 dB, which a PCM file cannot hold. (Forced curve:
    # "auto" would correctly pick linear here and avoid the overshoot.)
    src = tmp_path / "dc.wav"
    sf.write(str(src), np.full(SR * 2, 0.99), SR, subtype="PCM_16")
    settings = pipeline.LoopSettings(crossfade_s=0.5, curve="equal_power", out_dir=tmp_path / "out")
    result = pipeline.process_file(src, settings)
    assert any("clipped" in note for note in result.notes)
    written, _ = sf.read(str(result.output_path))
    assert np.max(np.abs(written)) <= 1.0


def test_cli_end_to_end(tmp_path, capsys):
    src = write_noise(tmp_path / "wind.wav")
    out = tmp_path / "out"
    code = cli.main([str(src), "--crossfade", "1.5", "--curve", "linear", "--out", str(out)])
    assert code == 0
    assert (out / "wind_loop.wav").exists()
    printed = capsys.readouterr().out
    assert "Loop-point check" in printed and "Done: 1 written" in printed


def test_cli_reports_failure_with_nonzero_exit(tmp_path, capsys):
    src = write_noise(tmp_path / "short.wav", seconds=1.0)
    code = cli.main([str(src), "--crossfade", "3", "--out", str(tmp_path / "out")])
    assert code == 1
    assert "Crossfade too long" in capsys.readouterr().err
