"""Audition helpers for the GUI: waveform peaks and seamless looping playback.

No Tkinter code lives here, so the logic can be unit-tested without a window
(and, for the pure functions, without a sound card).

Playback uses ``sounddevice`` (PortAudio) with a callback stream. The callback
reads from the decoded file with a wrapping index, so when playback reaches the
last sample it continues straight into the first one: exactly what a game
engine does when it loops the file. That makes the playback a faithful test of
the loop point (no gap or re-trigger delay, unlike "play file again" players).
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import soundfile as sf

try:  # Optional dependency: the rest of Loop Maker works without it.
    import sounddevice as sd
except (ImportError, OSError):  # OSError: PortAudio library missing
    sd = None


class PlaybackUnavailable(RuntimeError):
    """Raised when audio playback cannot be used on this machine."""


# --------------------------------------------------------------------------
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------

def load_for_preview(path: Path) -> tuple[np.ndarray, int]:
    """Read a file as float32 ``(frames, channels)`` for display and playback."""
    data, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    return data, sample_rate


def compute_peaks(audio: np.ndarray, columns: int) -> tuple[np.ndarray, np.ndarray]:
    """Reduce ``audio`` to one (min, max) pair per screen column.

    All channels are combined (lowest min, highest max) so a stereo file draws
    as a single envelope. If there are fewer frames than columns, one column
    per frame is returned.
    """
    data = np.asarray(audio)
    if data.ndim == 2:
        lows, highs = data.min(axis=1), data.max(axis=1)
    else:
        lows = highs = data
    n = len(lows)
    if n == 0 or columns <= 0:
        return np.zeros(0), np.zeros(0)

    columns = min(columns, n)
    # Start index of each column's bin; strictly increasing because columns <= n.
    starts = np.linspace(0, n, columns, endpoint=False).astype(int)
    return np.minimum.reduceat(lows, starts), np.maximum.reduceat(highs, starts)


def render_block(data: np.ndarray, position: int, frames: int) -> tuple[np.ndarray, int]:
    """Return the next ``frames`` frames starting at ``position``, wrapping at the end.

    This is the heart of the seamless loop playback: the index wraps modulo the
    file length, so the end of the file flows directly into the start.
    """
    length = len(data)
    indices = (position + np.arange(frames)) % length
    return data[indices], (position + frames) % length


def fit_channels(data: np.ndarray, device_channels: int) -> np.ndarray:
    """Adapt ``(frames, channels)`` audio to what the output device can play.

    - mono on a stereo (or wider) device: duplicated to both speakers
    - more channels than the device has: averaged to mono, then duplicated
      (an audition fold-down, not a proper surround downmix)
    - otherwise unchanged
    """
    channels = data.shape[1]
    if channels == 1 and device_channels >= 2:
        return np.repeat(data, 2, axis=1)
    if channels > device_channels:
        mono = data.mean(axis=1, keepdims=True)
        return np.repeat(mono, min(device_channels, 2), axis=1)
    return data


# --------------------------------------------------------------------------
# Player
# --------------------------------------------------------------------------

class LoopPlayer:
    """Plays one buffer on an endless loop and reports the playhead position.

    The audio callback runs on a PortAudio thread; ``_position`` is shared with
    the UI thread, so every access goes through ``_lock``.
    """

    def __init__(self) -> None:
        self._data: np.ndarray | None = None
        self._sample_rate = 0
        self._position = 0
        self._lock = threading.Lock()
        self._stream = None

    # ---- state ------------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._data is not None

    @property
    def is_playing(self) -> bool:
        return self._stream is not None

    @property
    def duration_s(self) -> float:
        return 0.0 if self._data is None else len(self._data) / self._sample_rate

    @property
    def position_s(self) -> float:
        """Playhead in seconds, corrected for output latency (what you actually hear)."""
        if self._data is None:
            return 0.0
        with self._lock:
            position = self._position
        if self._stream is not None:
            # The callback runs ahead of the speakers by the stream latency.
            position -= int(self._stream.latency * self._sample_rate)
        return (position % len(self._data)) / self._sample_rate

    # ---- control ----------------------------------------------------------

    def load(self, data: np.ndarray, sample_rate: int) -> None:
        """Stop any playback and load new ``(frames, channels)`` audio."""
        self.stop()
        device_channels = self._device_channels()
        self._data = np.ascontiguousarray(fit_channels(data, device_channels), dtype=np.float32)
        self._sample_rate = sample_rate
        self._position = 0

    def play(self, start_s: float | None = None) -> None:
        """Start (or keep) playing; ``start_s`` jumps to that time first."""
        if self._data is None:
            return
        if start_s is not None:
            self.seek(start_s)
        if self._stream is None:
            self._stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=self._data.shape[1],
                dtype="float32",
                latency="low",
                callback=self._callback,
            )
            self._stream.start()

    def seek(self, seconds: float) -> None:
        if self._data is None:
            return
        frame = int(seconds * self._sample_rate) % len(self._data)
        with self._lock:
            self._position = frame

    def stop(self) -> None:
        """Stop playback; the position is kept so Play resumes from there."""
        if self._stream is not None:
            heard = self.position_s  # keep the audible position, not the buffered one
            self._stream.stop()
            self._stream.close()
            self._stream = None
            self.seek(heard)

    def close(self) -> None:
        self.stop()
        self._data = None

    # ---- internals --------------------------------------------------------

    def _callback(self, outdata, frames, time_info, status) -> None:  # PortAudio thread
        with self._lock:
            block, self._position = render_block(self._data, self._position, frames)
        outdata[:] = block

    @staticmethod
    def _device_channels() -> int:
        if sd is None:
            raise PlaybackUnavailable(
                "Playback needs the 'sounddevice' package: pip install sounddevice"
            )
        try:
            return int(sd.query_devices(kind="output")["max_output_channels"])
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as one clear error
            raise PlaybackUnavailable(f"No usable audio output device: {exc}") from exc
