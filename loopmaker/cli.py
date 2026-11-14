"""Command-line interface.

Examples::

    python -m loopmaker rain.wav forest.flac --crossfade 3.0 --out output
    python -m loopmaker recordings/ --preset Wwise --template "AMB_{name}_{index}_loop"
    python -m loopmaker rain.wav --preset Custom --sample-rate 44100 --bit-depth 16 --format FLAC
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import APP_NAME, __version__, audio_io, core, loudness, naming, pipeline, presets, quality, variations

# Options that only make sense with --preset Custom.
_CUSTOM_OPTIONS = ("sample_rate", "bit_depth", "file_format", "dither")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loopmaker",
        description=(
            f"{APP_NAME}: make ambience recordings seamlessly loopable: split in half, swap the halves "
            "and crossfade across the middle join."
        ),
    )
    parser.add_argument("inputs", nargs="+", help="audio files and/or folders (folders are scanned non-recursively)")

    loop = parser.add_argument_group("loop")
    loop.add_argument(
        "--crossfade", type=float, default=pipeline.DEFAULT_CROSSFADE_S, metavar="SECONDS",
        help=f"crossfade length in seconds (default: {pipeline.DEFAULT_CROSSFADE_S})",
    )
    loop.add_argument(
        "--curve", choices=core.CURVES, default=core.DEFAULT_CURVE,
        help="crossfade curve; 'auto' measures how correlated the file's end and start are and "
             "picks linear (correlated/tonal) or equal_power (uncorrelated, e.g. wind, rain) "
             "(default: %(default)s)",
    )
    loop.add_argument(
        "--safety-fade", type=float, default=core.DEFAULT_SAFETY_FADE_S * 1000, metavar="MS",
        help="tiny fade at the very start/end of the output in ms, 0 to disable (default: %(default)g)",
    )

    var = parser.add_argument_group("variations")
    var.add_argument("--variations", type=int, choices=(0, *variations.VARIATION_COUNTS), default=0, metavar="N",
                     help="write N rotated loop variations (3-5) instead of one loop; 0 = off (default)")
    var.add_argument("--min-spacing", type=float, default=variations.DEFAULT_MIN_SPACING * 100, metavar="PCT",
                     help="minimum distance between variation split points, %% of file length (default: %(default)g)")
    var.add_argument("--short-warning", type=float, default=variations.DEFAULT_SHORT_WARNING_S, metavar="SECONDS",
                     help="warn that variations may sound alike below this source length (default: %(default)g)")

    level = parser.add_argument_group("loudness")
    level.add_argument(
        "--normalize", type=float, default=None, metavar="LUFS",
        help="bring every output to this integrated loudness, e.g. -23 (default: off)",
    )
    level.add_argument(
        "--peak-ceiling", type=float, default=loudness.DEFAULT_PEAK_CEILING_DBTP, metavar="DBTP",
        help="true-peak ceiling used by --normalize; gain is reduced rather than clipping (default: %(default)g)",
    )

    output = parser.add_argument_group("output format")
    output.add_argument(
        "--preset", default=presets.CUSTOM, metavar="NAME",
        help="output preset from the preset file (e.g. Wwise, Unreal, Unity, Godot) or Custom (default: %(default)s)",
    )
    output.add_argument("--presets", type=Path, default=None, metavar="JSON",
                        help=f"preset file (default: {presets.DEFAULT_PRESETS_PATH.name} in the project folder)")
    output.add_argument("--sample-rate", type=int, choices=presets.SAMPLE_RATES, default=None,
                        help="Custom only: output sample rate (default: keep source)")
    output.add_argument("--bit-depth", choices=presets.BIT_DEPTHS, default=None,
                        help="Custom only: output bit depth (default: keep source)")
    output.add_argument("--format", dest="file_format", choices=presets.available_formats(), default=None,
                        help="Custom only: output container (default: WAV)")
    output.add_argument("--dither", choices=presets.DITHER_MODES, default=None,
                        help="Custom only: auto = only when reducing bit depth (default: auto)")

    names = parser.add_argument_group("naming")
    names.add_argument("--template", default=naming.DEFAULT_TEMPLATE,
                       help="file name template with {name} {category} {index} {suffix} (default: %(default)s)")
    names.add_argument("--category", default=naming.DEFAULT_CATEGORY, help="value of {category} (default: %(default)s)")
    names.add_argument("--suffix", default=naming.DEFAULT_SUFFIX, help="value of {suffix} (default: %(default)s)")
    names.add_argument(
        "--out", type=Path, default=pipeline.DEFAULT_OUT_DIR, metavar="FOLDER",
        help="output folder (default: %(default)s)",
    )

    misc = parser.add_argument_group("other")
    misc.add_argument(
        "--report-window", type=float, default=quality.DEFAULT_WINDOW_MS, metavar="MS",
        help="window used by the loop-quality report in ms (default: %(default)g)",
    )
    misc.add_argument("--dry-run", action="store_true", help="validate and preview output names, write nothing")
    misc.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser


def output_spec_from_args(args: argparse.Namespace) -> presets.OutputSpec:
    """Turn --preset (+ Custom fields) into an OutputSpec. Raises ValueError with a clear message."""
    available, warnings = presets.load_presets(args.presets)
    for warning in warnings:
        print(f"WARNING {warning}", file=sys.stderr)

    custom_given = [opt for opt in _CUSTOM_OPTIONS if getattr(args, opt) is not None]
    if args.preset == presets.CUSTOM:
        return presets.OutputSpec(
            sample_rate=args.sample_rate,
            bit_depth=args.bit_depth,
            file_format=args.file_format or "WAV",
            dither=args.dither or "auto",
        )
    if args.preset not in available:
        raise ValueError(f"unknown preset {args.preset!r}; available: {', '.join([presets.CUSTOM, *available])}")
    if custom_given:
        flags = ", ".join("--" + opt.replace("_", "-").replace("file-format", "format") for opt in custom_given)
        raise ValueError(f"{flags} only apply to --preset Custom (preset {args.preset!r} defines its own format)")
    return available[args.preset].spec


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns a process exit code (0 = every file succeeded)."""
    args = build_parser().parse_args(argv)

    try:
        spec = output_spec_from_args(args)
    except ValueError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2

    settings = pipeline.LoopSettings(
        crossfade_s=args.crossfade,
        curve=args.curve,
        safety_fade_ms=args.safety_fade,
        out_dir=args.out,
        report_window_ms=args.report_window,
        normalize_lufs=args.normalize,
        peak_ceiling_dbtp=args.peak_ceiling,
        output=spec,
        naming=naming.Naming(args.template, args.category, args.suffix),
        variations=args.variations,
        min_spacing=args.min_spacing / 100.0,
        short_warning_s=args.short_warning,
    )
    problems = settings.errors()
    if problems:
        for problem in problems:
            print(f"ERROR {problem}", file=sys.stderr)
        return 2
    for warning in settings.naming.warnings():
        print(f"WARNING {warning}", file=sys.stderr)

    files, skipped = audio_io.collect_inputs(args.inputs)
    for problem in skipped:
        print(f"SKIPPED {problem}", file=sys.stderr)
    if not files:
        print("No audio files to process.", file=sys.stderr)
        return 1

    # Preview the output names before anything is written.
    print(f"Output: {spec.describe()} -> {Path(args.out)}")
    taken: set[Path] = set()
    for index, path in enumerate(files, start=1):
        try:
            names = [p.name for p in pipeline.output_paths_for(path, settings, index, len(files), taken)]
        except ValueError as exc:
            names = [f"<{exc}>"]
        print(f"  {path.name}  ->  {', '.join(names)}")
    print()
    if args.dry_run:
        return 0

    failures = 0
    for path, result in pipeline.process_batch(files, settings):
        if isinstance(result, Exception):
            failures += 1
            print(f"FAILED  {path}: {result}\n", file=sys.stderr)
        else:
            print(result.format() + "\n")

    done = len(files) - failures
    print(f"Done: {done} written, {failures} failed, {len(skipped)} skipped.")
    return 0 if failures == 0 and not skipped else 1


if __name__ == "__main__":
    sys.exit(main())
