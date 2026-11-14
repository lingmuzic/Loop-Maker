"""File-level processing shared by the CLI and the GUI.

Both front-ends build a :class:`LoopSettings`, call :func:`process_batch`, and
display what comes back. Nothing here prints or touches a widget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from . import audio_io, core, loudness, quality

OUTPUT_SUFFIX = "_loop"
DEFAULT_OUT_DIR = Path("output")
DEFAULT_CROSSFADE_S = 2.0


@dataclass(frozen=True)
class LoopSettings:
    """All user-facing options in one place."""

    crossfade_s: float = DEFAULT_CROSSFADE_S
    curve: str = core.DEFAULT_CURVE
    safety_fade_ms: float = core.DEFAULT_SAFETY_FADE_S * 1000
    out_dir: Path = DEFAULT_OUT_DIR
    report_window_ms: float = quality.DEFAULT_WINDOW_MS
    # Loudness normalisation: None = off, otherwise the target in LUFS that
    # every file of the batch is brought to.
    normalize_lufs: float | None = None
    peak_ceiling_dbtp: float = loudness.DEFAULT_PEAK_CEILING_DBTP


@dataclass
class ProcessResult:
    """Everything worth reporting about one processed file."""

    input_path: Path
    output_path: Path
    sample_rate: int
    channels: int
    input_subtype: str
    output_subtype: str
    input_seconds: float
    output_seconds: float
    quality: quality.LoopQuality
    # Fade shape actually applied ("equal_power"/"linear"), whether it was
    # picked automatically, and the measured correlation behind the choice.
    curve: str = "equal_power"
    curve_was_auto: bool = False
    correlation: float = 0.0
    # Where the crossfade sits inside the output file, in seconds. Used by the
    # GUI to highlight the only edited region of the loop.
    crossfade_start_s: float = 0.0
    crossfade_end_s: float = 0.0
    normalization: loudness.Normalization | None = None
    notes: list[str] = field(default_factory=list)

    def format(self) -> str:
        how = "auto" if self.curve_was_auto else "manual"
        lines = [
            f"{self.input_path.name} -> {self.output_path}",
            f"  {self.sample_rate} Hz, {self.channels} ch, {self.input_subtype} -> {self.output_subtype}, "
            f"{self.input_seconds:.3f} s -> {self.output_seconds:.3f} s",
            f"  Crossfade curve: {self.curve} ({how}; end/start correlation {self.correlation:+.2f}, "
            f"threshold {core.AUTO_CURVE_THRESHOLD:.2f})",
        ]
        if self.normalization is not None:
            lines.append(self.normalization.format())
        lines.append(self.quality.format())
        lines += [f"  NOTE: {note}" for note in self.notes]
        return "\n".join(lines)


def output_path_for(input_path: Path, out_dir: Path, taken: set[Path] | None = None) -> Path:
    """``<out_dir>/<stem>_loop.wav``, made unique within ``taken`` if needed.

    ``taken`` lets a batch avoid two inputs (e.g. ``rain.wav`` and
    ``rain.flac``) writing to the same output file.
    """
    candidate = out_dir / f"{input_path.stem}{OUTPUT_SUFFIX}.wav"
    counter = 2
    while taken is not None and candidate.resolve() in taken:
        candidate = out_dir / f"{input_path.stem}{OUTPUT_SUFFIX}_{counter}.wav"
        counter += 1
    return candidate


def process_file(input_path: Path, settings: LoopSettings, output_path: Path | None = None) -> ProcessResult:
    """Load, loop, write and measure a single file.

    Raises ``ValueError`` for invalid settings (e.g. crossfade too long) and
    lets soundfile errors propagate for unreadable files.
    """
    input_path = Path(input_path)
    if output_path is None:
        output_path = output_path_for(input_path, Path(settings.out_dir))

    # Hard guarantee: never overwrite the source recording.
    if output_path.resolve() == input_path.resolve():
        raise ValueError(f"Refusing to overwrite the input file: {input_path}")

    audio = audio_io.read_audio(input_path)

    # Resolve "auto" here (rather than inside make_loop) so the choice and the
    # correlation behind it can be reported.
    curve, correlation = core.resolve_curve(audio.data, audio.sample_rate, settings.crossfade_s, settings.curve)
    looped = core.make_loop(
        audio.data,
        audio.sample_rate,
        crossfade_s=settings.crossfade_s,
        curve=curve,
        safety_fade_s=settings.safety_fade_ms / 1000.0,
    )

    notes: list[str] = []

    # The loop-point report compares against the original at the *same* gain,
    # so it is measured before normalisation (gain doesn't change smoothness).
    report = quality.compare(audio.data, looped, audio.sample_rate, settings.report_window_ms)

    # Loudness normalisation is measured on the finished loop, because that is
    # exactly what will play in the game.
    normalization = None
    if settings.normalize_lufs is not None:
        try:
            looped, normalization = loudness.normalize(
                looped, audio.sample_rate, settings.normalize_lufs, settings.peak_ceiling_dbtp
            )
        except loudness.LoudnessError as exc:
            notes.append(f"not loudness-normalised: {exc}")

    out_subtype, subtype_note = audio_io.wav_subtype_for(audio.subtype)
    if subtype_note:
        notes.append(subtype_note)
    clip_warning = audio_io.write_wav(output_path, looped, audio.sample_rate, out_subtype)
    if clip_warning:
        notes.append(clip_warning)

    # The output is B | A with the crossfade ending where B's full length ends
    # (see core.make_loop): B has frames - frames // 2 samples.
    sr = audio.sample_rate
    second_half_len = audio.data.shape[0] - audio.data.shape[0] // 2
    xfade_len = int(round(settings.crossfade_s * sr))

    return ProcessResult(
        crossfade_start_s=(second_half_len - xfade_len) / sr,
        crossfade_end_s=second_half_len / sr,
        input_path=input_path,
        output_path=output_path,
        sample_rate=audio.sample_rate,
        channels=audio.channels,
        input_subtype=audio.subtype,
        output_subtype=out_subtype,
        input_seconds=audio.data.shape[0] / audio.sample_rate,
        output_seconds=looped.shape[0] / audio.sample_rate,
        quality=report,
        curve=curve,
        curve_was_auto=settings.curve == "auto",
        correlation=correlation,
        normalization=normalization,
        notes=notes,
    )


def process_batch(
    input_paths: Iterable[Path], settings: LoopSettings
) -> Iterator[tuple[Path, ProcessResult | Exception]]:
    """Process many files, yielding ``(input_path, result_or_error)`` as each finishes.

    One bad file never stops the batch: its exception is yielded instead of
    raised, so the caller can log it and carry on. Yielding (rather than
    returning a list) lets the GUI show progress live.
    """
    taken: set[Path] = set()
    for path in input_paths:
        path = Path(path)
        out = output_path_for(path, Path(settings.out_dir), taken)
        taken.add(out.resolve())
        try:
            yield path, process_file(path, settings, out)
        except Exception as exc:  # noqa: BLE001 - reported to the user, batch continues
            yield path, exc
