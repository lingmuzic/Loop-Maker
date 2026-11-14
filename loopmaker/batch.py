"""Batch planning: per-file validation, the "On problem" policy, and reports.

UI-free, shared by the CLI and the GUI:

1. :func:`validate_batch` checks every file *before* anything is written,
   using only file headers (``soundfile.info``), so the GUI can re-run it on
   every settings change. Each file gets a status:

   * ``OK``       nothing to report
   * ``warning``  will be processed, but something is worth knowing
   * ``error``    cannot be processed as configured

2. The policy decides what happens to errors:

   * ``skip``  (default) skip that file and log why; the batch continues
   * ``clamp`` fix what can be fixed (crossfade, variation count), log every
               change, and process the file (status becomes ``warning``)
   * ``stop``  stop the batch when it reaches the first file that cannot be
               processed (or that fails while processing)

   Warnings never block anything. An error only ever blocks its own file
   (except under ``stop``).

3. :func:`run_batch` processes the planned files and returns a
   :class:`BatchReport` that can be printed or exported as a text file.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

import soundfile as sf

from . import APP_NAME, pipeline
from . import variations as variations_mod

OK, WARNING, ERROR = "OK", "warning", "error"
POLICIES = ("skip", "clamp", "stop")
POLICY_LABELS = {"skip": "skip and log", "clamp": "auto-clamp", "stop": "stop the batch"}
DEFAULT_POLICY = "skip"

#: BS.1770 needs at least one 400 ms block to measure loudness.
MIN_LOUDNESS_S = 0.4
#: Auto-clamp never shortens a crossfade below this; it reduces variations instead.
MIN_CLAMPED_CROSSFADE_S = 0.05


@dataclass(frozen=True)
class FileInfo:
    """Header facts read with soundfile.info (cheap: no audio decoding)."""

    frames: int
    sample_rate: int
    channels: int
    subtype: str

    @property
    def duration_s(self) -> float:
        return self.frames / self.sample_rate


def probe(path: Path) -> FileInfo:
    info = sf.info(str(path))
    return FileInfo(info.frames, info.samplerate, info.channels, info.subtype)


@dataclass
class FileJob:
    """One row of the batch: a file plus optional per-file overrides."""

    path: Path
    crossfade_s: float | None = None   # None = use the global setting
    variations: int | None = None      # None = global; 0 = off for this file
    _info: FileInfo | None = field(default=None, repr=False)
    _probe_error: str | None = field(default=None, repr=False)

    def info(self) -> FileInfo | None:
        """Cached header info (None if the file cannot be read)."""
        if self._info is None and self._probe_error is None:
            try:
                self._info = probe(self.path)
            except Exception as exc:  # noqa: BLE001 - becomes a row error
                self._probe_error = str(exc)
        return self._info


@dataclass
class Validation:
    """Outcome of validating one :class:`FileJob` against the settings."""

    job: FileJob
    status: str = OK
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)   # auto-clamp log lines
    crossfade_s: float = 0.0                           # effective, after overrides/clamping
    variations: int = 0
    max_crossfade_s: float | None = None
    output_paths: list[Path] = field(default_factory=list)

    @property
    def processable(self) -> bool:
        return self.status != ERROR

    @property
    def tooltip(self) -> str:
        lines = [f"Error: {e}" for e in self.errors] + [f"Warning: {w}" for w in self.warnings]
        lines += [f"Auto-clamp: {c}" for c in self.changes]
        return "\n".join(lines) if lines else "OK"


def max_crossfade_s(info: FileInfo, variations: int, min_spacing: float) -> float:
    """Longest crossfade this file allows (seconds).

    Half the file for the standard loop; with N variations also
    ``(duration x (1 - N x spacing)) / 2``, from ``2C + N x S < duration``.
    """
    half = (info.frames // 2) / info.sample_rate
    if not variations:
        return half
    room = info.duration_s * (1.0 - variations * min_spacing) / 2.0
    # The length rule is strict ("longer than"), so stay one millisecond inside it.
    return max(0.0, min(half, room - 0.001))


def _floor_ms(seconds: float) -> float:
    return math.floor(seconds * 1000) / 1000


def _fmt_s(seconds: float) -> str:
    """'3.0', '1.0', '0.779': one decimal when that is exact, else milliseconds."""
    return f"{seconds:.1f}" if abs(seconds - round(seconds, 1)) < 1e-9 else f"{seconds:.3f}"


def validate_job(
    job: FileJob,
    settings: pipeline.LoopSettings,
    policy: str = DEFAULT_POLICY,
    index: int = 1,
    batch_size: int = 1,
    taken: set[Path] | None = None,
) -> Validation:
    """Validate one file. ``taken`` tracks output names across the batch."""
    name = job.path.name
    crossfade = settings.crossfade_s if job.crossfade_s is None else job.crossfade_s
    count = settings.variations if job.variations is None else job.variations
    v = Validation(job, crossfade_s=crossfade, variations=count)

    info = job.info()
    if info is None:
        v.errors.append(f"cannot read file: {job._probe_error}")
        v.status = ERROR
        return v

    # --- per-file overrides must be valid values ---
    if crossfade <= 0:
        v.errors.append(f"crossfade must be greater than 0 s (got {crossfade:g})")
    if count not in (0, *variations_mod.VARIATION_COUNTS):
        v.errors.append(f"variations must be off, 3, 4 or 5 (got {count})")
    if v.errors:
        v.status = ERROR
        return v

    duration = info.duration_s
    clamp = policy == "clamp"

    half = (info.frames // 2) / info.sample_rate
    if clamp:
        # Prefer shortening the crossfade (keeps the requested variations);
        # only reduce the variation count if the crossfade would become tiny.
        limit = max_crossfade_s(info, count, settings.min_spacing)
        if count and crossfade > limit and limit < MIN_CLAMPED_CROSSFADE_S:
            reduced = variations_mod.max_variations(duration, min(crossfade, half), settings.min_spacing)
            new = f"{reduced}" if reduced else "off (standard loop)"
            v.changes.append(f"{name}: variations {count} -> {new} (file too short for {count} at this crossfade)")
            count = v.variations = reduced
            limit = max_crossfade_s(info, count, settings.min_spacing)
        if crossfade > limit:
            v.crossfade_s = _floor_ms(limit)
            v.changes.append(f"{name}: crossfade {_fmt_s(crossfade)} -> {_fmt_s(v.crossfade_s)} s")
    else:
        if crossfade > half:
            v.errors.append(f"crossfade {_fmt_s(crossfade)} s exceeds half the file length ({half:.2f} s)")
        if count:
            try:
                variations_mod.check_length(duration, crossfade, count, settings.min_spacing)
            except variations_mod.VariationError as exc:
                v.errors.append(str(exc))

    if count:
        warning = variations_mod.short_source_warning(duration, settings.short_warning_s)
        if warning:
            v.warnings.append(warning)
    v.max_crossfade_s = max_crossfade_s(info, count, settings.min_spacing)

    # --- loudness normalisation feasibility (warnings only) ---
    if settings.normalize_lufs is not None:
        if duration < MIN_LOUDNESS_S:
            v.warnings.append(f"shorter than {MIN_LOUDNESS_S} s: loudness cannot be measured, will not be normalised")
        if info.channels > 6:
            v.warnings.append(f"{info.channels} channels: loudness normalisation supports up to 5.1")

    # --- output names (template) ---
    if not v.errors:
        effective = replace(settings, crossfade_s=v.crossfade_s, variations=count)
        try:
            v.output_paths = pipeline.output_paths_for(job.path, effective, index, batch_size, taken)
        except ValueError as exc:
            v.errors.append(str(exc))
        else:
            if any(p.resolve() == job.path.resolve() for p in v.output_paths):
                v.errors.append("output name equals the input file; it would be overwritten")
            planned = [effective.naming.render(job.path.stem, index, batch_size, variation=n)
                       for n in (range(1, count + 1) if count else [None])]
            renamed = [p.name for p, stem in zip(v.output_paths, planned) if p.stem != stem]
            if renamed:
                v.warnings.append(f"output name collides with another file in the batch; written as {', '.join(renamed)}")

    if v.errors:
        v.status = ERROR
    elif v.warnings or v.changes:
        v.status = WARNING
    return v


def validate_batch(
    jobs: list[FileJob], settings: pipeline.LoopSettings, policy: str = DEFAULT_POLICY
) -> list[Validation]:
    """Validate every job. Settings-level problems (``settings.errors()``) are the caller's to check."""
    taken: set[Path] = set()
    return [validate_job(job, settings, policy, i, len(jobs), taken) for i, job in enumerate(jobs, start=1)]


def summary_line(validations: list[Validation]) -> str:
    """E.g. ``12 files: 9 OK, 2 warnings, 1 cannot be processed``."""
    ok = sum(v.status == OK for v in validations)
    warn = sum(v.status == WARNING for v in validations)
    bad = sum(v.status == ERROR for v in validations)
    files = f"{len(validations)} file{'s' if len(validations) != 1 else ''}"
    return f"{files}: {ok} OK, {warn} warning{'s' if warn != 1 else ''}, {bad} cannot be processed"


def batch_max_crossfade(validations: list[Validation]) -> float | None:
    """Smallest per-file maximum, i.e. the longest crossfade that suits them all."""
    values = [v.max_crossfade_s for v in validations if v.max_crossfade_s is not None]
    return min(values) if values else None


# --------------------------------------------------------------------------
# Running and reporting
# --------------------------------------------------------------------------

WRITTEN, SKIPPED, FAILED, NOT_RUN = "written", "skipped", "failed", "not run"


@dataclass
class FileReport:
    input_path: Path
    status: str                                   # written / skipped / failed / not run
    reason: str = ""
    crossfade_s: float | None = None
    variations: int = 0
    outputs: list[pipeline.ProcessResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)

    def format(self) -> str:
        head = f"[{self.status}] {self.input_path.name}"
        if self.status != WRITTEN:
            return f"{head}: {self.reason}"
        lines = [f"{head}: crossfade used {self.crossfade_s:g} s"
                 + (f", {self.variations} variations" if self.variations else "")]
        for out in self.outputs:
            lines.append(f"    -> {out.output_path}   seam score {out.seam_score}/100, curve {out.curve}"
                         + (f", {out.normalization.final_lufs:.1f} LUFS" if out.normalization else ""))
        lines += [f"    auto-clamp: {c}" for c in self.changes]
        lines += [f"    warning: {w}" for w in self.warnings]
        return "\n".join(lines)


@dataclass
class BatchReport:
    settings_text: str
    summary: str
    files: list[FileReport] = field(default_factory=list)
    stopped: bool = False
    created: datetime = field(default_factory=datetime.now)

    @property
    def counts(self) -> dict[str, int]:
        return {s: sum(f.status == s for f in self.files) for s in (WRITTEN, SKIPPED, FAILED, NOT_RUN)}

    def result_line(self) -> str:
        c = self.counts
        line = f"Result: {c[WRITTEN]} written, {c[SKIPPED]} skipped, {c[FAILED]} failed"
        if c[NOT_RUN]:
            line += f", {c[NOT_RUN]} not run"
        return line + (" (batch stopped)" if self.stopped else "")

    def format(self) -> str:
        lines = [
            f"{APP_NAME} batch report - {self.created:%Y-%m-%d %H:%M:%S}",
            f"Settings: {self.settings_text}",
            f"Before running: {self.summary}",
            "",
        ]
        lines += [f.format() for f in self.files]
        lines += ["", self.result_line()]
        return "\n".join(lines) + "\n"

    def export(self, path: Path) -> None:
        Path(path).write_text(self.format(), encoding="utf-8")


def describe_settings(settings: pipeline.LoopSettings, policy: str) -> str:
    var = f"{settings.variations} variations ({settings.min_spacing:.0%} spacing)" if settings.variations else "variations off"
    norm = f"normalise to {settings.normalize_lufs:g} LUFS" if settings.normalize_lufs is not None else "no normalisation"
    return (
        f"crossfade {settings.crossfade_s:g} s, curve {settings.curve}, {var}, {settings.output.describe()}, "
        f"template '{settings.naming.template}', {norm}, on problem: {POLICY_LABELS[policy]}"
    )


EventCallback = Callable[[str, int, object], None]


def run_batch(
    validations: list[Validation],
    settings: pipeline.LoopSettings,
    policy: str = DEFAULT_POLICY,
    on_event: EventCallback | None = None,
) -> BatchReport:
    """Process the validated files according to ``policy``.

    ``on_event(kind, row_index, payload)`` is called with ``("start", i, None)``
    before a file and ``("done", i, FileReport)`` after it (also for skipped
    and not-run files), so a UI can show per-row progress.
    """
    emit = on_event or (lambda *_: None)
    report = BatchReport(describe_settings(settings, policy), summary_line(validations))

    for i, v in enumerate(validations):
        if report.stopped:
            file_report = FileReport(v.job.path, NOT_RUN, "batch stopped earlier")
        elif not v.processable:
            file_report = FileReport(v.job.path, SKIPPED, "; ".join(v.errors), warnings=list(v.warnings))
            if policy == "stop":
                report.stopped = True
                file_report.reason += " (stopping the batch)"
        else:
            emit("start", i, None)
            effective = replace(settings, crossfade_s=v.crossfade_s, variations=v.variations)
            try:
                result = pipeline.process_file(v.job.path, effective, v.output_paths)
            except Exception as exc:  # noqa: BLE001 - reported per file
                file_report = FileReport(v.job.path, FAILED, str(exc), v.crossfade_s, v.variations)
                if policy == "stop":
                    report.stopped = True
                    file_report.reason += " (stopping the batch)"
            else:
                file_report = FileReport(
                    v.job.path, WRITTEN, crossfade_s=v.crossfade_s, variations=v.variations,
                    outputs=result.outputs, warnings=list(dict.fromkeys(v.warnings + result.notes)),
                    changes=list(v.changes),
                )
        report.files.append(file_report)
        emit("done", i, file_report)
    return report
