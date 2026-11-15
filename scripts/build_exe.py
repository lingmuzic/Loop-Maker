"""Build the standalone Windows app with PyInstaller.

    .venv\\Scripts\\python.exe scripts\\build_exe.py

Produces:
    dist/Loop Maker/                      the app folder (Loop Maker.exe + _internal/)
    dist/LoopMaker-<version>-win64.zip    the same folder, zipped for sharing

Choices:
* "one-folder" build, not "one-file": a one-file exe unpacks ~150 MB to a
  temp folder on every launch (slow start) and is flagged by antivirus
  software more often.
* Windowed (no console). The GUI is the only entry point; the CLI still
  needs Python.
* presets.json is placed next to the exe so users can edit it.
* After building, the exe's own ``--selftest`` runs; the build fails if it
  does not pass, so a broken bundle is never zipped.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from loopmaker import APP_NAME, __version__  # noqa: E402

DIST = ROOT / "dist"
WORK = ROOT / "build"
APP_FOLDER = DIST / APP_NAME
EXE = APP_FOLDER / f"{APP_NAME}.exe"
ICON = ROOT / "assets" / "loop_maker.ico"

USER_README = f"""{APP_NAME} {__version__}
{'=' * (len(APP_NAME) + len(__version__) + 1)}

Turns ambience recordings into seamlessly loopable files.

START
  Double-click "{APP_NAME}.exe". No Python installation is needed.
  Keep the whole folder together: the exe needs the "_internal" folder next to it.

FIRST LAUNCH ON A NEW PC
  The app is not code-signed, so Windows SmartScreen may say
  "Windows protected your PC". Click "More info" -> "Run anyway".

FILES IN THIS FOLDER
  presets.json   output presets (Wwise, Unreal, ...). You can edit it;
                 if it is missing or invalid, built-in presets are used.
  output\\        default output folder (created on first use; you can pick
                 another one in the app). Put this folder somewhere you can
                 write to (e.g. Documents or Desktop, not Program Files).

TROUBLESHOOTING
  Run this in a terminal inside this folder and send the result:
      "{APP_NAME}.exe" --selftest selftest.txt

Requires Windows 10 or 11, 64-bit.
"""


def build() -> None:
    PyInstaller.__main__.run([
        str(ROOT / "scripts" / "loop_maker_app.py"),
        "--name", APP_NAME,
        "--windowed",
        "--onedir",
        "--noconfirm",
        "--clean",
        "--icon", str(ICON),
        "--add-data", f"{ICON};assets",
        "--paths", str(ROOT),
        "--distpath", str(DIST),
        "--workpath", str(WORK / "pyinstaller"),
        "--specpath", str(WORK),
        # Only used by the README figure script and the tests: keep them out.
        "--exclude-module", "matplotlib",
        "--exclude-module", "pytest",
        "--exclude-module", "IPython",
    ])


def add_user_files() -> None:
    shutil.copy2(ROOT / "presets.json", APP_FOLDER / "presets.json")
    shutil.copy2(ROOT / "LICENSE", APP_FOLDER / "LICENSE.txt")
    (APP_FOLDER / "READ ME.txt").write_text(USER_README, encoding="utf-8")


def self_test() -> None:
    report = WORK / "selftest.txt"
    result = subprocess.run([str(EXE), "--selftest", str(report)], timeout=300)
    text = report.read_text(encoding="utf-8") if report.exists() else "(no report written)"
    print(text)
    if result.returncode != 0:
        raise SystemExit(f"Self-test of the built exe failed (exit code {result.returncode}).")


def make_zip() -> Path:
    archive = DIST / f"LoopMaker-{__version__}-win64.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(APP_FOLDER.rglob("*")):
            if path.is_file():
                zf.write(path, Path(APP_NAME) / path.relative_to(APP_FOLDER))
    return archive


def folder_size_mb(folder: Path) -> float:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()) / 1e6


def remove_work_folder() -> None:
    """Delete PyInstaller's intermediate folder.

    It contains a half-built "Loop Maker.exe" without ``_internal``, which
    fails with "Failed to load Python DLL" if someone double-clicks it by
    mistake. Only dist/ should hold a runnable exe.
    """
    shutil.rmtree(WORK / "pyinstaller", ignore_errors=True)


if __name__ == "__main__":
    build()
    add_user_files()
    self_test()
    archive = make_zip()
    remove_work_folder()
    print(f"\nApp folder: {APP_FOLDER} ({folder_size_mb(APP_FOLDER):.0f} MB)")
    print(f"Zip:        {archive} ({archive.stat().st_size / 1e6:.0f} MB)")
