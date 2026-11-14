"""Small Tkinter front-end. Run with ``python -m loopmaker.gui``.

The GUI is deliberately thin: it collects settings, calls the same
``pipeline.process_batch`` the CLI uses, and shows the results. Waveform
peaks and loop playback come from ``preview`` (also UI-free).

Window layout, top to bottom:
    Input files  ->  Settings  ->  Process  ->  Test playback  ->  Log

Threading: processing runs on a worker thread so the window stays responsive.
The worker never touches widgets (Tkinter is not thread-safe); it posts
messages to a queue that the Tk main loop drains every 100 ms.

Optional extras, each degrading gracefully when missing:
- ``tkinterdnd2``: drag-and-drop onto the file list
- ``sounddevice``: playback in the Test playback area
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import APP_NAME, audio_io, core, loudness, pipeline, preview

try:  # Optional dependency: drag-and-drop support.
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # pragma: no cover - depends on the environment
    TkinterDnD = None

_AUDIO_FILETYPES = [
    ("Audio files", " ".join(f"*{ext}" for ext in audio_io.SUPPORTED_EXTENSIONS)),
    ("All files", "*.*"),
]

# Waveform colours (light surface, blue wave, orange crossfade highlight).
WAVE_BG = "#fcfcfb"
WAVE_FG = "#2a78d6"
WAVE_XFADE = "#fbe1d6"
WAVE_MIDLINE = "#d6d5d0"
PLAYHEAD = "#d03b3b"
PEAK_LIMITED_FG = "#b4580f"  # dark orange text for outputs that missed the LUFS target

PLAYHEAD_REFRESH_MS = 30
LOOP_POINT_PREROLL_S = 2.0  # "Play loop point" starts this long before the wrap


class LoopMakerApp:
    """Main window: file list, settings, Process button, test playback and log."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.files: list[Path] = []
        self.results: list[pipeline.ProcessResult] = []  # rows of the output list
        self.log_queue: queue.Queue[tuple] = queue.Queue()
        self.worker: threading.Thread | None = None

        self.player = preview.LoopPlayer()
        self.preview_data = None          # float32 (frames, channels) of the selected output
        self.preview_result: pipeline.ProcessResult | None = None

        root.title(APP_NAME)
        root.minsize(720, 760)

        # Tk variables bound to the settings widgets.
        self.crossfade_var = tk.StringVar(value=str(pipeline.DEFAULT_CROSSFADE_S))
        self.curve_var = tk.StringVar(value=core.DEFAULT_CURVE)
        self.safety_var = tk.StringVar(value=f"{core.DEFAULT_SAFETY_FADE_S * 1000:g}")
        self.out_var = tk.StringVar(value=str(pipeline.DEFAULT_OUT_DIR.resolve()))
        self.normalize_var = tk.BooleanVar(value=False)
        self.target_lufs_var = tk.StringVar(value=f"{loudness.DEFAULT_TARGET_LUFS:g}")
        self.ceiling_var = tk.StringVar(value=f"{loudness.DEFAULT_PEAK_CEILING_DBTP:g}")
        self.status_var = tk.StringVar(value="Process some files, then pick an output to audition it.")

        self._build_layout()
        self._enable_drag_and_drop()
        self._bind_shortcuts()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_log_queue)
        self.root.after(PLAYHEAD_REFRESH_MS, self._update_playhead)

    # ------------------------------------------------------------------ layout

    def _build_layout(self) -> None:
        pad = {"padx": 8, "pady": 4}
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)  # file list
        self.root.rowconfigure(3, weight=2)  # test playback (waveform)
        self.root.rowconfigure(4, weight=1)  # log

        self._build_file_list(row=0, pad=pad)
        self._build_settings(row=1, pad=pad)

        self.process_button = ttk.Button(self.root, text="Process", command=self._start_processing)
        self.process_button.grid(row=2, column=0, sticky="ew", **pad)

        self._build_test_area(row=3, pad=pad)
        self._build_log(row=4, pad=pad)

    def _build_file_list(self, row: int, pad: dict) -> None:
        frame = ttk.LabelFrame(self.root, text="Input files")
        frame.grid(row=row, column=0, sticky="nsew", **pad)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.file_list = tk.Listbox(frame, selectmode=tk.EXTENDED, height=5, exportselection=False)
        self.file_list.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.file_list.yview)
        scroll.grid(row=0, column=1, sticky="ns", pady=6)
        self.file_list.configure(yscrollcommand=scroll.set)

        buttons = ttk.Frame(frame)
        buttons.grid(row=0, column=2, sticky="n", padx=6, pady=6)
        for text, command in [
            ("Add files...", self._add_files_dialog),
            ("Add folder...", self._add_folder_dialog),
            ("Remove selected", self._remove_selected),
            ("Clear", self._clear_files),
        ]:
            ttk.Button(buttons, text=text, command=command).pack(fill=tk.X, pady=2)

        self.drop_hint = ttk.Label(frame, foreground="gray")
        self.drop_hint.grid(row=1, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 6))

    def _build_settings(self, row: int, pad: dict) -> None:
        frame = ttk.LabelFrame(self.root, text="Settings")
        frame.grid(row=row, column=0, sticky="ew", **pad)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Crossfade (s)").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.crossfade_var, width=10).grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Curve").grid(row=1, column=0, sticky="w", **pad)
        ttk.Combobox(
            frame, textvariable=self.curve_var, values=core.CURVES, state="readonly", width=14
        ).grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Safety fade (ms)").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.safety_var, width=10).grid(row=2, column=1, sticky="w", **pad)

        # Loudness normalisation: checkbox + target + true-peak ceiling on one row.
        norm_row = ttk.Frame(frame)
        norm_row.grid(row=3, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(norm_row, text="Normalize loudness to", variable=self.normalize_var).pack(side=tk.LEFT, **pad)
        ttk.Entry(norm_row, textvariable=self.target_lufs_var, width=7).pack(side=tk.LEFT)
        ttk.Label(norm_row, text="LUFS     true-peak ceiling").pack(side=tk.LEFT, **pad)
        ttk.Entry(norm_row, textvariable=self.ceiling_var, width=7).pack(side=tk.LEFT)
        ttk.Label(norm_row, text="dBTP").pack(side=tk.LEFT, **pad)

        ttk.Label(frame, text="Output folder").grid(row=4, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.out_var).grid(row=4, column=1, sticky="ew", **pad)
        ttk.Button(frame, text="Browse...", command=self._choose_output).grid(row=4, column=2, **pad)

    def _build_test_area(self, row: int, pad: dict) -> None:
        frame = ttk.LabelFrame(self.root, text="Test playback")
        frame.grid(row=row, column=0, sticky="nsew", **pad)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        # Output list + transport buttons.
        self.output_list = tk.Listbox(frame, height=4, exportselection=False, activestyle="none")
        self.output_list.grid(row=0, column=0, sticky="ew", padx=(6, 0), pady=6)
        self.output_list.bind("<<ListboxSelect>>", self._on_output_selected)

        buttons = ttk.Frame(frame)
        buttons.grid(row=0, column=1, sticky="n", padx=6, pady=6)
        self.play_button = ttk.Button(buttons, text="Play  (Space)", command=self._toggle_playback)
        self.play_button.pack(fill=tk.X, pady=2)
        ttk.Button(buttons, text="Play loop point", command=self._play_loop_point).pack(fill=tk.X, pady=2)
        ttk.Button(buttons, text="Open output folder", command=self._open_output_folder).pack(fill=tk.X, pady=2)

        # Waveform: click to play from that point.
        self.wave = tk.Canvas(frame, height=130, background=WAVE_BG, highlightthickness=1,
                              highlightbackground=WAVE_MIDLINE, cursor="hand2")
        self.wave.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=6)
        self.wave.bind("<Configure>", lambda _e: self._draw_waveform())
        self.wave.bind("<Button-1>", self._on_wave_click)

        ttk.Label(frame, textvariable=self.status_var, foreground="gray").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(2, 6)
        )

    def _build_log(self, row: int, pad: dict) -> None:
        frame = ttk.LabelFrame(self.root, text="Log / loop quality report")
        frame.grid(row=row, column=0, sticky="nsew", **pad)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.log = ScrolledText(frame, height=7, font=("Consolas", 9), state=tk.DISABLED, wrap=tk.NONE)
        self.log.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 0))
        log_xscroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.log.xview)
        log_xscroll.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        self.log.configure(xscrollcommand=log_xscroll.set)

    def _enable_drag_and_drop(self) -> None:
        if TkinterDnD is None or not hasattr(self.root, "drop_target_register"):
            self.drop_hint.configure(text="Tip: install tkinterdnd2 to drag and drop files here.")
            return
        self.file_list.drop_target_register(DND_FILES)
        self.file_list.dnd_bind("<<Drop>>", self._on_drop)
        self.drop_hint.configure(text="Drag and drop audio files or folders onto the list.")

    def _bind_shortcuts(self) -> None:
        # bind_all so Space works wherever the focus is ...
        self.root.bind_all("<space>", self._on_space)

    def _on_space(self, event) -> str | None:
        # ... except where Space already means something: typing in a text
        # field (Entry/Combobox) or pressing a focused button.
        if isinstance(event.widget, (tk.Entry, ttk.Button, tk.Button, tk.Text)):
            return None
        self._toggle_playback()
        return "break"

    # ------------------------------------------------------------- file list

    def _add_paths(self, paths: list[str]) -> None:
        """Add files/folders (folders are expanded) and report anything skipped."""
        found, problems = audio_io.collect_inputs(paths)
        known = {p.resolve() for p in self.files}
        for path in found:
            if path.resolve() not in known:
                self.files.append(path)
                # File name first so it stays visible even with long folder paths.
                self.file_list.insert(tk.END, f"{path.name}    ({path.parent})")
        for problem in problems:
            self._log(f"SKIPPED {problem}")

    def _on_drop(self, event) -> None:
        # tkinterdnd2 delivers a Tcl list; splitlist handles paths with spaces/braces.
        self._add_paths(list(self.root.tk.splitlist(event.data)))

    def _add_files_dialog(self) -> None:
        self._add_paths(list(filedialog.askopenfilenames(title="Add audio files", filetypes=_AUDIO_FILETYPES)))

    def _add_folder_dialog(self) -> None:
        folder = filedialog.askdirectory(title="Add a folder of audio files")
        if folder:
            self._add_paths([folder])

    def _remove_selected(self) -> None:
        for index in reversed(self.file_list.curselection()):
            self.file_list.delete(index)
            del self.files[index]

    def _clear_files(self) -> None:
        self.file_list.delete(0, tk.END)
        self.files.clear()

    def _choose_output(self) -> None:
        folder = filedialog.askdirectory(title="Choose output folder", initialdir=self.out_var.get())
        if folder:
            self.out_var.set(folder)

    # ------------------------------------------------------------ processing

    def _read_settings(self) -> pipeline.LoopSettings | None:
        """Parse the form; show an error dialog and return None if invalid."""
        try:
            crossfade = float(self.crossfade_var.get())
            safety = float(self.safety_var.get())
            target = float(self.target_lufs_var.get())
            ceiling = float(self.ceiling_var.get())
        except ValueError:
            messagebox.showerror(APP_NAME, "Crossfade, safety fade, LUFS and dBTP fields must be numbers.")
            return None
        if crossfade <= 0 or safety < 0:
            messagebox.showerror(APP_NAME, "Crossfade must be > 0 and safety fade must be >= 0.")
            return None
        return pipeline.LoopSettings(
            crossfade_s=crossfade,
            curve=self.curve_var.get(),
            safety_fade_ms=safety,
            out_dir=Path(self.out_var.get()),
            normalize_lufs=target if self.normalize_var.get() else None,
            peak_ceiling_dbtp=ceiling,
        )

    def _start_processing(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            messagebox.showinfo(APP_NAME, "Add some audio files first.")
            return
        settings = self._read_settings()
        if settings is None:
            return

        # Outputs may be overwritten, so release the current preview first.
        self._clear_outputs()
        self.process_button.configure(state=tk.DISABLED, text="Processing...")
        files = list(self.files)  # snapshot: the worker must not see later list edits
        self.worker = threading.Thread(target=self._process_worker, args=(files, settings), daemon=True)
        self.worker.start()

    def _process_worker(self, files: list[Path], settings: pipeline.LoopSettings) -> None:
        """Runs off the UI thread. Communicates only through ``log_queue``."""
        failures = 0
        for path, result in pipeline.process_batch(files, settings):
            if isinstance(result, Exception):
                failures += 1
                self.log_queue.put(("log", f"FAILED  {path.name}: {result}\n"))
            else:
                self.log_queue.put(("result", result))
                self.log_queue.put(("log", result.format() + "\n"))
        self.log_queue.put(("log", f"Done: {len(files) - failures} written, {failures} failed.\n"))
        self.log_queue.put(("done",))

    def _drain_log_queue(self) -> None:
        try:
            while True:
                kind, *payload = self.log_queue.get_nowait()
                if kind == "log":
                    self._log(payload[0])
                elif kind == "result":
                    self._add_output(payload[0])
                elif kind == "done":
                    self.process_button.configure(state=tk.NORMAL, text="Process")
                    if self.results and not self.output_list.curselection():
                        self._select_output(0)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log_queue)

    def _log(self, text: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    # -------------------------------------------------------- test playback

    def _add_output(self, result: pipeline.ProcessResult) -> None:
        self.results.append(result)
        verdict = "SMOOTH" if result.quality.after.is_smooth else "CHECK BY EAR"
        parts = [result.output_path.name, f"{result.output_seconds:.2f} s", verdict, result.curve]
        norm = result.normalization
        if norm is not None:
            parts.append(f"{norm.final_lufs:.1f} LUFS")
            if norm.limited_by_peak:
                # Make it obvious without digging through the log.
                parts.append(f"PEAK-LIMITED ({norm.shortfall_db:.1f} dB short of {norm.target_lufs:g})")
        self.output_list.insert(tk.END, "    ".join(parts))
        if norm is not None and norm.limited_by_peak:
            self.output_list.itemconfigure(tk.END, foreground=PEAK_LIMITED_FG)

    def _clear_outputs(self) -> None:
        self.player.close()
        self.results.clear()
        self.output_list.delete(0, tk.END)
        self.preview_data = None
        self.preview_result = None
        self._draw_waveform()
        self._refresh_play_button()

    def _select_output(self, index: int) -> None:
        self.output_list.selection_clear(0, tk.END)
        self.output_list.selection_set(index)
        self.output_list.activate(index)
        self._on_output_selected()

    def _on_output_selected(self, _event=None) -> None:
        selection = self.output_list.curselection()
        if not selection:
            return
        result = self.results[selection[0]]
        if result is self.preview_result:
            return  # already loaded (e.g. Space re-selected the same row)

        self.player.stop()
        try:
            self.preview_data, sample_rate = preview.load_for_preview(result.output_path)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            messagebox.showerror(APP_NAME, f"Could not open {result.output_path.name}:\n{exc}")
            return
        self.preview_result = result
        try:
            self.player.load(self.preview_data, sample_rate)
        except preview.PlaybackUnavailable as exc:
            self.status_var.set(f"Waveform only: {exc}")
        self._draw_waveform()
        self._refresh_play_button()

    def _toggle_playback(self) -> None:
        if not self.player.is_loaded:
            return
        if self.player.is_playing:
            self.player.stop()
        else:
            self._safe_play()
        self._refresh_play_button()

    def _play_loop_point(self) -> None:
        """Start shortly before the end so you hear the wrap (end -> start) right away."""
        if self.player.is_loaded:
            self._safe_play(max(0.0, self.player.duration_s - LOOP_POINT_PREROLL_S))
            self._refresh_play_button()

    def _on_wave_click(self, event) -> None:
        """Left click: play from the clicked point (jumps there if already playing)."""
        width = self.wave.winfo_width()
        if not self.player.is_loaded or width <= 1:
            return
        fraction = min(max(event.x / width, 0.0), 1.0)
        self._safe_play(fraction * self.player.duration_s)
        self._refresh_play_button()

    def _safe_play(self, start_s: float | None = None) -> None:
        try:
            self.player.play(start_s)
        except Exception as exc:  # noqa: BLE001 - e.g. device busy / unsupported rate
            messagebox.showerror(APP_NAME, f"Playback failed:\n{exc}")

    def _refresh_play_button(self) -> None:
        self.play_button.configure(text="Stop  (Space)" if self.player.is_playing else "Play  (Space)")

    def _open_output_folder(self) -> None:
        folder = Path(self.out_var.get())
        if folder.is_dir():
            os.startfile(folder)  # Windows: open in Explorer
        else:
            messagebox.showinfo(APP_NAME, f"The output folder does not exist yet:\n{folder}")

    # ------------------------------------------------------------- waveform

    def _draw_waveform(self) -> None:
        """Redraw the selected output's waveform to fit the canvas."""
        canvas = self.wave
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        mid = height / 2
        canvas.create_line(0, mid, width, mid, fill=WAVE_MIDLINE)

        if self.preview_data is None or width <= 1:
            return

        result = self.preview_result
        duration = result.output_seconds if result is not None else 0.0

        # Crossfade region: the only part of the loop that was edited.
        if result is not None and result.output_seconds > 0:
            x0 = result.crossfade_start_s / result.output_seconds * width
            x1 = result.crossfade_end_s / result.output_seconds * width
            canvas.create_rectangle(x0, 0, x1, height, fill=WAVE_XFADE, outline="")
            canvas.create_text(x0 + 4, height - 4, text="crossfade", anchor="sw", fill="gray", font=("Segoe UI", 8))

        # Envelope as one polygon: top edge left->right, bottom edge right->left.
        lows, highs = preview.compute_peaks(self.preview_data, width)
        scale = mid * 0.95
        xs = [i * width / len(highs) for i in range(len(highs))]
        top = [(x, mid - h * scale) for x, h in zip(xs, highs)]
        bottom = [(x, mid - lo * scale) for x, lo in zip(reversed(xs), reversed(lows))]
        canvas.create_polygon(*top, *bottom, fill=WAVE_FG, outline=WAVE_FG)

        canvas.create_line(0, 0, 0, height, fill=PLAYHEAD, width=2, tags="playhead")
        self._move_playhead()
        if result is not None:
            self.status_var.set(
                f"{result.output_path.name}  |  {duration:.2f} s  |  click the waveform to play from there, "
                f"Space = play/stop, orange = crossfade"
            )

    def _move_playhead(self) -> None:
        width, height = self.wave.winfo_width(), self.wave.winfo_height()
        duration = self.player.duration_s
        if duration <= 0:
            return
        x = self.player.position_s / duration * width
        self.wave.coords("playhead", x, 0, x, height)

    def _update_playhead(self) -> None:
        if self.player.is_playing:
            self._move_playhead()
        self.root.after(PLAYHEAD_REFRESH_MS, self._update_playhead)

    # --------------------------------------------------------------- closing

    def _on_close(self) -> None:
        self.player.close()  # stop the audio stream before the window goes away
        self.root.destroy()


def main() -> None:
    try:  # Crisp text on high-DPI Windows displays.
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass

    root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    LoopMakerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
