"""Tests for the GUI's audition helpers (no window or sound card needed)."""

import numpy as np
import pytest

from loopmaker import pipeline, preview


def test_render_block_wraps_seamlessly_from_end_to_start():
    data = np.arange(10, dtype=np.float32)[:, None]
    block, pos = preview.render_block(data, position=7, frames=6)
    # 7, 8, 9 then straight back to 0, 1, 2: no gap, no repeated sample.
    assert block[:, 0].tolist() == [7, 8, 9, 0, 1, 2]
    assert pos == 3


def test_render_block_consecutive_calls_reproduce_the_loop():
    data = np.arange(7, dtype=np.float32)[:, None]
    pos, played = 0, []
    for _ in range(5):  # odd block size vs. file length, like a real audio callback
        block, pos = preview.render_block(data, pos, 3)
        played.extend(block[:, 0].tolist())
    assert played == [i % 7 for i in range(15)]


def test_compute_peaks_one_pair_per_column():
    audio = np.sin(np.linspace(0, 20 * np.pi, 10_000))
    lows, highs = preview.compute_peaks(audio, 200)
    assert len(lows) == len(highs) == 200
    assert np.all(lows <= highs)
    assert highs.max() == pytest.approx(audio.max())
    assert lows.min() == pytest.approx(audio.min())


def test_compute_peaks_combines_channels_and_handles_short_audio():
    stereo = np.array([[0.5, -0.2], [0.1, 0.9], [-0.7, 0.0]])
    lows, highs = preview.compute_peaks(stereo, 100)  # more columns than frames
    assert lows.tolist() == [-0.2, 0.1, -0.7]
    assert highs.tolist() == [0.5, 0.9, 0.0]


@pytest.mark.parametrize(
    "file_channels, device_channels, expected",
    [(1, 2, 2), (2, 2, 2), (6, 2, 2), (2, 1, 1), (4, 8, 4)],
)
def test_fit_channels(file_channels, device_channels, expected):
    data = np.ones((100, file_channels), dtype=np.float32)
    assert preview.fit_channels(data, device_channels).shape == (100, expected)


def test_crossfade_region_reported_in_output_time(tmp_path):
    import soundfile as sf

    sr = 1000
    src = tmp_path / "a.wav"
    sf.write(str(src), np.random.default_rng(0).standard_normal(5001) * 0.1, sr)  # odd length
    result = pipeline.process_file(src, pipeline.LoopSettings(crossfade_s=1.0, out_dir=tmp_path / "out")).outputs[0]
    # B has 2501 frames, so the crossfade covers 1.501 s .. 2.501 s of the output.
    assert result.crossfade_start_s == pytest.approx(1.501)
    assert result.crossfade_end_s == pytest.approx(2.501)
    assert result.output_seconds == pytest.approx(4.001)
