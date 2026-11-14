"""Self-test for the standalone build: ``"Loop Maker.exe" --selftest report.txt``.

The windowed .exe has no console, so results go to a text file. It checks
that every bundled native/heavy dependency really works inside the frozen
app: libsndfile (WAV/FLAC/OGG), scipy (resampling), pyloudnorm, PortAudio
(sounddevice), Tk + tkdnd (drag and drop), and a full batch run.
Exit code 0 = all critical checks passed.
"""

from __future__ import annotations

import platform
import sys
import tempfile
import traceback
from pathlib import Path


def run(report_path: Path) -> int:
    lines: list[str] = []
    failures = 0

    def check(name: str, func, critical: bool = True) -> None:
        nonlocal failures
        try:
            detail = func()
            lines.append(f"PASS  {name}: {detail}")
        except Exception as exc:  # noqa: BLE001 - everything is reported
            failures += critical
            tag = "FAIL" if critical else "WARN"
            lines.append(f"{tag}  {name}: {exc}")
            lines.extend("        " + l for l in traceback.format_exc().splitlines()[-4:])

    from . import APP_NAME, __version__
    from .paths import app_dir, is_frozen

    lines.append(f"{APP_NAME} {__version__} self-test")
    lines.append(f"python {sys.version.split()[0]}, {platform.platform()}, frozen={is_frozen()}")
    lines.append(f"app folder: {app_dir()}")

    def libsndfile():
        import numpy as np
        import soundfile as sf

        from .presets import available_formats
        with tempfile.TemporaryDirectory() as tmp:
            for fmt, ext in (("WAV", "wav"), ("FLAC", "flac"), ("OGG", "ogg")):
                if fmt not in available_formats():
                    continue
                path = Path(tmp) / f"t.{ext}"
                sf.write(str(path), np.zeros(4800), 48000, format=fmt)
                sf.read(str(path))
        return f"{sf.__libsndfile_version__}, formats {available_formats()}"

    def presets_file():
        from .presets import DEFAULT_PRESETS_PATH, load_presets
        loaded, warnings = load_presets()
        return f"{DEFAULT_PRESETS_PATH} -> {list(loaded)}" + (f" (warnings: {warnings})" if warnings else "")

    def full_batch():
        import numpy as np
        import soundfile as sf

        from . import batch, pipeline, presets
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "Self Test.wav"
            sf.write(str(src), np.random.default_rng(0).standard_normal((44100 * 20, 2)) * 0.1, 44100, subtype="PCM_24")
            settings = pipeline.LoopSettings(
                crossfade_s=2.0, out_dir=Path(tmp) / "out", normalize_lufs=-23.0, variations=3,
                output=presets.BUILTIN_PRESETS["Wwise"].spec,
            )
            validations = batch.validate_batch([batch.FileJob(src)], settings)
            report = batch.run_batch(validations, settings)
            out = report.files[0]
            if out.status != batch.WRITTEN:
                raise RuntimeError(out.reason)
            info = sf.info(str(out.outputs[0].output_path))
            return (f"{len(out.outputs)} variations, {info.samplerate} Hz {info.subtype}, "
                    f"{out.outputs[0].normalization.final_lufs:.1f} LUFS, seam {out.outputs[0].seam_score}")

    def audio_device():
        import sounddevice as sd
        device = sd.query_devices(kind="output")
        return f"PortAudio {sd.get_portaudio_version()[1]}; default output: {device['name']}"

    def tk_dnd():
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
        root.withdraw()
        version = root.tk.call("package", "require", "tkdnd")
        root.destroy()
        return f"tkdnd {version}"

    check("libsndfile read/write", libsndfile)
    check("presets.json", presets_file)
    check("full batch (resample 44.1->48k, 3 variations, loudness, dither)", full_batch)
    check("tkinter + drag-and-drop", tk_dnd)
    check("audio output (playback)", audio_device, critical=False)  # no device is not a build bug

    lines.append(f"RESULT: {'OK' if failures == 0 else f'{failures} critical check(s) failed'}")
    Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if failures == 0 else 1
