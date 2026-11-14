"""Command-line interface.

Example::

    python -m loopmaker rain.wav forest.flac --crossfade 3.0 --curve equal_power --out output
    python -m loopmaker path/to/folder
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import APP_NAME, __version__, audio_io, core, pipeline, quality


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loopmaker",
        description=(
            f"{APP_NAME}: make ambience recordings seamlessly loopable: split in half, swap the halves "
            "and crossfade across the middle join."
        ),
    )
    parser.add_argument("inputs", nargs="+", help="audio files and/or folders (folders are scanned non-recursively)")
    parser.add_argument(
        "--crossfade", type=float, default=pipeline.DEFAULT_CROSSFADE_S, metavar="SECONDS",
        help=f"crossfade length in seconds (default: {pipeline.DEFAULT_CROSSFADE_S})",
    )
    parser.add_argument(
        "--curve", choices=core.CURVES, default=core.DEFAULT_CURVE,
        help="crossfade curve; equal_power suits uncorrelated ambience (default: %(default)s)",
    )
    parser.add_argument(
        "--safety-fade", type=float, default=core.DEFAULT_SAFETY_FADE_S * 1000, metavar="MS",
        help="tiny fade at the very start/end of the output in ms, 0 to disable (default: %(default)g)",
    )
    parser.add_argument(
        "--out", type=Path, default=pipeline.DEFAULT_OUT_DIR, metavar="FOLDER",
        help="output folder (default: %(default)s)",
    )
    parser.add_argument(
        "--report-window", type=float, default=quality.DEFAULT_WINDOW_MS, metavar="MS",
        help="window used by the loop-quality report in ms (default: %(default)g)",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns a process exit code (0 = every file succeeded)."""
    args = build_parser().parse_args(argv)

    files, problems = audio_io.collect_inputs(args.inputs)
    for problem in problems:
        print(f"SKIPPED {problem}", file=sys.stderr)
    if not files:
        print("No audio files to process.", file=sys.stderr)
        return 1

    settings = pipeline.LoopSettings(
        crossfade_s=args.crossfade,
        curve=args.curve,
        safety_fade_ms=args.safety_fade,
        out_dir=args.out,
        report_window_ms=args.report_window,
    )

    failures = 0
    for path, result in pipeline.process_batch(files, settings):
        if isinstance(result, Exception):
            failures += 1
            print(f"FAILED  {path}: {result}\n", file=sys.stderr)
        else:
            print(result.format() + "\n")

    done = len(files) - failures
    print(f"Done: {done} written, {failures} failed, {len(problems)} skipped.")
    return 0 if failures == 0 and not problems else 1


if __name__ == "__main__":
    sys.exit(main())
