"""File-level processing shared by the CLI and the GUI.

Both front-ends build a :class:`LoopSettings`, then call :func:`process_file`
(or :func:`process_batch`) and display what comes back. Nothing here prints
or touches a widget.

Processing order for one input file::

    read -> resample (if the preset changes the rate) -> choose curve
         -> for each output: make_loop -> quality check -> loudness
                             -> quantise/dither + write
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

from . import audio_io, convert, core, loudness, quality
from . import variations as variations_mod
from .naming import Naming
from .presets import OutputSpec

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
    # Output format (preset) and file naming.
    output: OutputSpec = field(default_factory=OutputSpec)
    naming: Naming = field(default_factory=Naming)
    # Loop variations: 0 = off (one standard loop), otherwise 3..5 rotations.
    variations: int = 0
    min_spacing: float = variations_mod.DEFAULT_MIN_SPACING
    short_warning_s: float = variations_mod.DEFAULT_SHORT_WARNING_S

    def errors(self) -> list[str]:
        """Problems that block the whole batch (e.g. a bad template)."""
        problems = self.output.errors() + self.naming.errors()
        if self.variations not in (0, *variations_mod.VARIATION_COUNTS):
            problems.append(f"variations must be off or one of {variations_mod.VARIATION_COUNTS}, not {self.variations}")
        if not 0 < self.min_spacing < 1:
            problems.append(f"minimum spacing must be between 0 and 100 %, not {self.min_spacing:.0%}")
        return problems


@dataclass
class ProcessResult:
    """Everything worth reporting about one written output file."""

    input_path: Path
    output_path: Path
    sample_rate: int            # of the output
    source_sample_rate: int
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
    crossfade_s: float = 0.0
    dithered: bool = False
    # Where the crossfade sits inside the output file, in seconds. Used by the
    # GUI to highlight the only edited region of the loop.
    crossfade_start_s: float = 0.0
    crossfade_end_s: float = 0.0
    normalization: loudness.Normalization | None = None
    # Variation number (1-based) and where its split point sits in the
    # source, or None for the standard midpoint loop.
    variation: int | None = None
    split_s: float | None = None
    # Middle seam: crossfade centre level vs. its surroundings, and the
    # combined 0-100 seam score (see quality.seam_score).
    crossfade_level_db: float = 0.0
    seam_score: int = 100
    notes: list[str] = field(default_factory=list)

    def format(self) -> str:
        how = "auto" if self.curve_was_auto else "manual"
        rate = (
            f"{self.source_sample_rate} -> {self.sample_rate} Hz"
            if self.source_sample_rate != self.sample_rate else f"{self.sample_rate} Hz"
        )
        dither = ", TPDF dither" if self.dithered else ""
        lines = [
            f"{self.input_path.name} -> {self.output_path}",
            f"  {rate}, {self.channels} ch, {self.input_subtype} -> {self.output_subtype}{dither}, "
            f"{self.input_seconds:.3f} s -> {self.output_seconds:.3f} s",
            f"  Crossfade: {self.crossfade_s:g} s, curve {self.curve} ({how}; end/start correlation "
            f"{self.correlation:+.2f}, threshold {core.AUTO_CURVE_THRESHOLD:.2f})",
        ]
        if self.variation is not None:
            lines.append(f"  Variation {self.variation:02d}: split at {self.split_s:.2f} s of the source")
        if self.normalization is not None:
            lines.append(self.normalization.format())
        lines.append(self.quality.format())
        lines.append(f"  Crossfade centre level {self.crossfade_level_db:+.2f} dB vs. surroundings; "
                     f"seam score {self.seam_score}/100")
        lines += [f"  NOTE: {note}" for note in self.notes]
        return "\n".join(lines)


@dataclass
class FileResult:
    """One input file and the output file(s) made from it."""

    input_path: Path
    outputs: list[ProcessResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # file-level warnings

    def format(self) -> str:
        lines = [f"WARNING {self.input_path.name}: {note}" for note in self.notes]
        return "\n".join(lines + [result.format() for result in self.outputs])


def output_paths_for(
    input_path: Path,
    settings: LoopSettings,
    index: int = 1,
    batch_size: int = 1,
    taken: set[Path] | None = None,
) -> list[Path]:
    """Planned output path(s) for one input, following the naming template.

    ``taken`` collects already-planned paths of the batch; on a collision
    ``_2``, ``_3`` ... is appended (the batch then reports it).
    """
    extension = audio_io.EXTENSIONS[settings.output.file_format]
    out_dir = Path(settings.out_dir)
    input_stem = Path(input_path).stem
    numbers = range(1, settings.variations + 1) if settings.variations else [None]
    paths = []
    for stem in [settings.naming.render(input_stem, index, batch_size, variation=v) for v in numbers]:
        candidate, counter = out_dir / f"{stem}{extension}", 2
        while taken is not None and candidate.resolve() in taken:
            candidate, counter = out_dir / f"{stem}_{counter}{extension}", counter + 1
        if taken is not None:
            taken.add(candidate.resolve())
        paths.append(candidate)
    return paths


def _dither_rng(output_path: Path) -> np.random.Generator:
    """Deterministic dither per output name: same input + settings -> identical bytes.

    Handy for asset pipelines under version control (no spurious diffs).
    """
    return np.random.default_rng(zlib.crc32(output_path.name.encode("utf-8")))


def process_file(
    input_path: Path,
    settings: LoopSettings,
    output_paths: list[Path] | None = None,
) -> FileResult:
    """Load, loop, write and measure one input file.

    Raises ``ValueError`` for invalid settings (e.g. crossfade too long) and
    lets soundfile errors propagate for unreadable files.
    """
    input_path = Path(input_path)
    problems = settings.errors()
    if problems:
        raise ValueError("; ".join(problems))
    if output_paths is None:
        output_paths = output_paths_for(input_path, settings)

    # Hard guarantee: never overwrite the source recording.
    for path in output_paths:
        if path.resolve() == input_path.resolve():
            raise ValueError(f"Refusing to overwrite the input file: {input_path}")

    audio = audio_io.read_audio(input_path)
    spec = settings.output

    # Resample the *source* first: the filter's edge transients then sit at the
    # original start/end, which end up inside the crossfade at ~zero gain.
    sample_rate = spec.sample_rate or audio.sample_rate
    data = convert.resample(audio.data, audio.sample_rate, sample_rate)

    # Resolve "auto" once per input (rather than inside make_loop) so the
    # choice and the correlation behind it can be reported.
    curve, correlation = core.resolve_curve(data, sample_rate, settings.crossfade_s, settings.curve)
    out_subtype, subtype_note = audio_io.resolve_subtype(spec.bit_depth, spec.file_format, audio.subtype)
    dither = out_subtype in ("PCM_16", "PCM_24") and convert.should_dither(
        spec.dither, audio.subtype, 16 if out_subtype == "PCM_16" else 24
    )

    result = FileResult(input_path)
    frames = data.shape[0]
    xfade_len = int(round(settings.crossfade_s * sample_rate))

    # Standard loop: one output split at the midpoint. Variations: rotated
    # split points at quiet spots (see variations.py).
    if settings.variations:
        splits: list[int | None] = variations_mod.choose_split_points(
            data, sample_rate, settings.crossfade_s, settings.variations, settings.min_spacing
        )
        warning = variations_mod.short_source_warning(frames / sample_rate, settings.short_warning_s)
        if warning:
            result.notes.append(warning)
    else:
        splits = [None]
    if len(splits) != len(output_paths):
        raise ValueError(f"expected {len(splits)} output paths, got {len(output_paths)}")

    for number, (split, output_path) in enumerate(zip(splits, output_paths), start=1):
        notes = [subtype_note] if subtype_note else []
        looped = core.make_loop(
            data, sample_rate, crossfade_s=settings.crossfade_s, curve=curve,
            safety_fade_s=settings.safety_fade_ms / 1000.0, split_at=split,
        )

        # Compare against the original at the *same* gain, i.e. before
        # normalisation (gain doesn't change smoothness).
        report = quality.compare(data, looped, sample_rate, settings.report_window_ms)
        # The output is B | A; the crossfade ends where B's full length ends.
        second_half_len = frames - (frames // 2 if split is None else split)
        xfade_db = quality.crossfade_level_db(looped, second_half_len - xfade_len, xfade_len)

        # Loudness is measured on the finished loop: exactly what will play.
        normalization = None
        if settings.normalize_lufs is not None:
            try:
                looped, normalization = loudness.normalize(
                    looped, sample_rate, settings.normalize_lufs, settings.peak_ceiling_dbtp
                )
            except loudness.LoudnessError as exc:
                notes.append(f"not loudness-normalised: {exc}")

        notes += audio_io.write_audio(
            output_path, looped, sample_rate, out_subtype, spec.file_format, dither, _dither_rng(output_path)
        )

        result.outputs.append(
            ProcessResult(
                input_path=input_path,
                output_path=output_path,
                sample_rate=sample_rate,
                source_sample_rate=audio.sample_rate,
                channels=audio.channels,
                input_subtype=audio.subtype,
                output_subtype=out_subtype,
                input_seconds=audio.data.shape[0] / audio.sample_rate,
                output_seconds=looped.shape[0] / sample_rate,
                quality=report,
                curve=curve,
                curve_was_auto=settings.curve == "auto",
                correlation=correlation,
                crossfade_s=settings.crossfade_s,
                dithered=dither,
                crossfade_start_s=(second_half_len - xfade_len) / sample_rate,
                crossfade_end_s=second_half_len / sample_rate,
                normalization=normalization,
                variation=None if split is None else number,
                split_s=None if split is None else split / sample_rate,
                crossfade_level_db=xfade_db,
                seam_score=quality.seam_score(report.after, xfade_db),
                notes=notes,
            )
        )
    return result


def process_batch(
    input_paths: Iterable[Path], settings: LoopSettings
) -> Iterator[tuple[Path, FileResult | Exception]]:
    """Process many files, yielding ``(input_path, result_or_error)`` as each finishes.

    One bad file never stops the batch: its exception is yielded instead of
    raised, so the caller can log it and carry on. ``{index}`` in the naming
    template counts the files of this batch.
    """
    paths = [Path(p) for p in input_paths]
    taken: set[Path] = set()
    for index, path in enumerate(paths, start=1):
        try:
            outputs = output_paths_for(path, settings, index, len(paths), taken)
            yield path, process_file(path, settings, outputs)
        except Exception as exc:  # noqa: BLE001 - reported to the user, batch continues
            yield path, exc
