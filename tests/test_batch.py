"""Feature C: per-file validation, the "On problem" policy, reports and the CLI."""

import numpy as np
import pytest
import soundfile as sf

from loopmaker import batch, cli, naming, pipeline, quality

SR = 8_000


def make(path, seconds, channels=1, seed=0):
    data = np.random.default_rng(seed).standard_normal((int(seconds * SR), channels)) * 0.1
    sf.write(str(path), data if channels > 1 else data[:, 0], SR, subtype="PCM_16")
    return path


@pytest.fixture
def mixed(tmp_path):
    """Long (OK), short (warning with variations), too short (error) files."""
    folder = tmp_path / "in"
    folder.mkdir()
    return {
        "long": make(folder / "Forest_long.wav", 40),
        "short": make(folder / "Creek_short.wav", 12),
        "tiny": make(folder / "Rain_short.wav", 2),
    }


def settings(tmp_path, **kw):
    return pipeline.LoopSettings(out_dir=tmp_path / "out", **kw)


# --- Classification ----------------------------------------------------------

def test_ok_warning_error_classification(tmp_path, mixed):
    jobs = [batch.FileJob(mixed[k]) for k in ("long", "short", "tiny")]
    v_long, v_short, v_tiny = batch.validate_batch(jobs, settings(tmp_path, crossfade_s=3.0, variations=3))

    assert v_long.status == batch.OK and v_long.tooltip == "OK"
    assert v_short.status == batch.WARNING
    assert "nearly identical" in v_short.tooltip
    assert v_tiny.status == batch.ERROR
    assert "crossfade 3.0 s exceeds half the file length (1.00 s)" in v_tiny.tooltip
    assert v_tiny.output_paths == []
    assert batch.summary_line([v_long, v_short, v_tiny]) == "3 files: 1 OK, 1 warning, 1 cannot be processed"


def test_unreadable_file_is_an_error(tmp_path):
    bad = tmp_path / "broken.wav"
    bad.write_bytes(b"not a wav file")
    (v,) = batch.validate_batch([batch.FileJob(bad)], settings(tmp_path))
    assert v.status == batch.ERROR and "cannot read file" in v.tooltip


def test_per_file_overrides(tmp_path, mixed):
    job = batch.FileJob(mixed["tiny"], crossfade_s=0.5)          # fixes the too-long crossfade
    (v,) = batch.validate_batch([job], settings(tmp_path, crossfade_s=3.0))
    assert v.status == batch.OK and v.crossfade_s == 0.5

    job = batch.FileJob(mixed["short"], variations=0)            # variations off for this file only
    (v,) = batch.validate_batch([job], settings(tmp_path, crossfade_s=1.0, variations=3))
    assert v.status == batch.OK and len(v.output_paths) == 1

    job = batch.FileJob(mixed["long"], variations=7)
    (v,) = batch.validate_batch([job], settings(tmp_path))
    assert v.status == batch.ERROR and "off, 3, 4 or 5" in v.tooltip


def test_max_crossfade(tmp_path, mixed):
    info = batch.FileJob(mixed["short"]).info()
    assert batch.max_crossfade_s(info, 0, 0.12) == pytest.approx(6.0)
    assert batch.max_crossfade_s(info, 5, 0.12) == pytest.approx(12 * (1 - 0.6) / 2 - 0.001)
    vs = batch.validate_batch([batch.FileJob(p) for p in mixed.values()], settings(tmp_path))
    assert batch.batch_max_crossfade(vs) == pytest.approx(1.0)  # the 2 s file limits the batch


def test_name_collision_is_a_warning(tmp_path):
    a = make(tmp_path / "rain.wav", 4)
    b = tmp_path / "rain.flac"
    sf.write(str(b), np.zeros(4 * SR), SR)
    vs = batch.validate_batch([batch.FileJob(a), batch.FileJob(b)], settings(tmp_path, crossfade_s=1.0))
    assert vs[0].status == batch.OK
    assert vs[1].status == batch.WARNING and "rain_loop_2.wav" in vs[1].tooltip


# --- Policies -----------------------------------------------------------------

def test_skip_one_bad_file_does_not_stop_the_batch(tmp_path, mixed):
    s = settings(tmp_path, crossfade_s=3.0)
    vs = batch.validate_batch([batch.FileJob(mixed[k]) for k in ("long", "tiny", "short")], s)
    report = batch.run_batch(vs, s, "skip")
    assert [f.status for f in report.files] == [batch.WRITTEN, batch.SKIPPED, batch.WRITTEN]
    assert "exceeds half the file length" in report.files[1].reason
    assert report.result_line() == "Result: 2 written, 1 skipped, 0 failed"


def test_auto_clamp_changes_are_logged_and_applied(tmp_path, mixed):
    s = settings(tmp_path, crossfade_s=3.0, safety_fade_ms=0)
    vs = batch.validate_batch([batch.FileJob(mixed["tiny"])], s, "clamp")
    assert vs[0].status == batch.WARNING
    assert vs[0].changes == ["Rain_short.wav: crossfade 3.0 -> 1.0 s"]

    report = batch.run_batch(vs, s, "clamp")
    written = report.files[0]
    assert written.status == batch.WRITTEN and written.crossfade_s == 1.0
    data, _ = sf.read(str(written.outputs[0].output_path))
    assert len(data) == 2 * SR - 1 * SR  # really used the clamped crossfade
    assert "auto-clamp: Rain_short.wav: crossfade 3.0 -> 1.0 s" in report.format()


def test_auto_clamp_keeps_variations_by_shortening_the_crossfade(tmp_path, mixed):
    # 12 s with 4 variations allows up to 12 x (1 - 4 x 0.12) / 2 = 3.12 s; ask for 4 s.
    s = settings(tmp_path, crossfade_s=4.0, variations=4)
    (v,) = batch.validate_batch([batch.FileJob(mixed["short"])], s, "clamp")
    assert v.variations == 4
    assert v.crossfade_s == pytest.approx(12 * (1 - 0.48) / 2 - 0.001, abs=0.001)
    assert len(v.changes) == 1 and "crossfade 4.0 -> 3.119 s" in v.changes[0]


def test_auto_clamp_reduces_variations_when_crossfade_would_vanish(tmp_path):
    src = make(tmp_path / "tiny.wav", 0.2)
    s = settings(tmp_path, crossfade_s=1.0, variations=5)
    (v,) = batch.validate_batch([batch.FileJob(src)], s, "clamp")
    assert v.variations == 0 and any("variations 5 -> off" in c for c in v.changes)
    assert v.status == batch.WARNING


def test_stop_policy_stops_at_first_problem(tmp_path, mixed):
    s = settings(tmp_path, crossfade_s=3.0)
    vs = batch.validate_batch([batch.FileJob(mixed[k]) for k in ("long", "tiny", "short")], s, "stop")
    report = batch.run_batch(vs, s, "stop")
    assert [f.status for f in report.files] == [batch.WRITTEN, batch.SKIPPED, batch.NOT_RUN]
    assert report.stopped and "batch stopped" in report.result_line()


def test_runtime_failure_is_reported_and_batch_continues(tmp_path, mixed):
    s = settings(tmp_path, crossfade_s=1.0)
    vs = batch.validate_batch([batch.FileJob(mixed[k]) for k in ("long", "short", "tiny")], s)
    mixed["short"].unlink()  # disappears between validation and processing
    events = []
    report = batch.run_batch(vs, s, "skip", lambda kind, i, _p: events.append((kind, i)))
    assert [f.status for f in report.files] == [batch.WRITTEN, batch.FAILED, batch.WRITTEN]
    assert ("start", 0) in events and ("done", 2) in events  # per-row progress events


# --- Report -------------------------------------------------------------------

def test_report_contents_and_export(tmp_path, mixed):
    s = settings(tmp_path, crossfade_s=3.0, naming=naming.Naming("AMB_{name}_{index}"))
    vs = batch.validate_batch([batch.FileJob(mixed[k]) for k in ("long", "tiny")], s)
    report = batch.run_batch(vs, s)
    text = report.format()
    assert "Before running: 2 files: 1 OK, 0 warnings, 1 cannot be processed" in text
    assert "[written] Forest_long.wav: crossfade used 3 s" in text
    assert "AMB_Forest_long_01.wav" in text and "seam score" in text
    assert "[skipped] Rain_short.wav: crossfade 3.0 s exceeds" in text
    path = tmp_path / "report.txt"
    report.export(path)
    assert path.read_text(encoding="utf-8") == text


def test_seam_score_and_crossfade_level():
    rng = np.random.default_rng(0)
    x = rng.standard_normal(SR * 20) * 0.1
    xfade = 2 * SR
    flat = np.concatenate([x[:5 * SR], x[5 * SR:5 * SR + xfade], x[5 * SR + xfade:]])
    dipped = flat.copy()
    dipped[5 * SR:5 * SR + xfade] *= np.sqrt(0.5)  # the whole crossfade 3 dB down
    assert abs(quality.crossfade_level_db(flat, 5 * SR, xfade)) < 0.5
    assert quality.crossfade_level_db(dipped, 5 * SR, xfade) == pytest.approx(-3.0, abs=0.5)
    wrap = quality.measure_wrap(flat, SR)
    assert quality.seam_score(wrap, 0.0) == 100
    assert quality.seam_score(wrap, -3.0) == 80  # 2 dB beyond the free 1 dB -> -20


# --- CLI ------------------------------------------------------------------------

def test_cli_summary_policies_and_report(tmp_path, mixed, capsys):
    folder = mixed["long"].parent
    out, report_path = tmp_path / "out", tmp_path / "r.txt"

    code = cli.main([str(folder), "--crossfade", "3", "--out", str(out), "--report", str(report_path)])
    captured = capsys.readouterr()
    assert code == 1  # something was skipped
    assert "3 files: 2 OK, 0 warnings, 1 cannot be processed" in captured.out
    assert "SKIPPED  Rain_short.wav" in captured.err
    assert "Result: 2 written, 1 skipped, 0 failed" in report_path.read_text(encoding="utf-8")

    code = cli.main([str(folder), "--crossfade", "3", "--out", str(out), "--on-problem", "clamp"])
    captured = capsys.readouterr()
    assert code == 0
    assert "auto-clamp: Rain_short.wav: crossfade 3.0 -> 1.0 s" in captured.out
    assert "Result: 3 written" in captured.out

    # Files run in name order: Creek, Forest, Rain_short (last), so nothing is left "not run".
    code = cli.main([str(folder), "--crossfade", "3", "--out", str(out), "--on-problem", "stop"])
    assert code == 1
    assert "(batch stopped)" in capsys.readouterr().out
