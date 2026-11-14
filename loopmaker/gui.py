"""Small Tkinter front-end. Run with ``python -m loopmaker.gui``.

The GUI is deliberately thin: it only collects settings, then calls the same
``pipeline.process_batch`` the CLI uses and prints each result's report.

Processing runs on a worker thread so the window stays responsive; the worker
never touches widgets directly, it posts log lines to a queue that the Tk
main loop drains every 100 ms (Tkinter is not thread-safe).

Drag-and-drop uses ``tkinterdnd2`` when it is installed; otherwise the window
still works with the "Add files" / "Add folder" buttons.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import audio_io, core, pipeline

try:  # Optional dependency: drag-and-drop support.
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # pragma: no cover - depends on the environment
    TkinterDnD = None

_AUDIO_FILETYPES = [
    ("Audio files", " ".join(f"*{ext}" for ext in audio_io.SUPPORTED_EXTENSIONS)),
    ("All files", "*.*"),
]


class LoopMakerApp:
    """Main window: file list, settings, Process button and log."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.files: list[Path] = []
        self.log_queue: queue.Queue[object] = queue.Queue()  # str lines + a finish sentinel
        self.worker: threading.Thread | None = None

        root.title("LoopMaker")
        root.minsize(640, 520)

        # Tk variables bound to the settings widgets.
        self.crossfade_var = tk.StringVar(value=str(pipeline.DEFAULT_CROSSFADE_S))
        self.curve_var = tk.StringVar(value=core.DEFAULT_CURVE)
        self.safety_var = tk.StringVar(value=f"{core.DEFAULT_SAFETY_FADE_S * 1000:g}")
        self.out_var = tk.StringVar(value=str(pipeline.DEFAULT_OUT_DIR.resolve()))

        self._build_layout()
        self._enable_drag_and_drop()
        self.root.after(100, self._drain_log_queue)

    # ------------------------------------------------------------------ layout

    def _build_layout(self) -> None:
        pad = {"padx": 8, "pady": 4}
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)  # file list grows
        self.root.rowconfigure(3, weight=1)  # log grows

        # --- File list ---
        files_frame = ttk.LabelFrame(self.root, text="Input files")
        files_frame.grid(row=0, column=0, sticky="nsew", **pad)
        files_frame.columnconfigure(0, weight=1)
        files_frame.rowconfigure(0, weight=1)

        self.file_list = tk.Listbox(files_frame, selectmode=tk.EXTENDED, height=8)
        self.file_list.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        scroll = ttk.Scrollbar(files_frame, orient=tk.VERTICAL, command=self.file_list.yview)
        scroll.grid(row=0, column=1, sticky="ns", pady=6)
        self.file_list.configure(yscrollcommand=scroll.set)

        buttons = ttk.Frame(files_frame)
        buttons.grid(row=0, column=2, sticky="n", padx=6, pady=6)
        for text, command in [
            ("Add files...", self._add_files_dialog),
            ("Add folder...", self._add_folder_dialog),
            ("Remove selected", self._remove_selected),
            ("Clear", self._clear_files),
        ]:
            ttk.Button(buttons, text=text, command=command).pack(fill=tk.X, pady=2)

        self.drop_hint = ttk.Label(files_frame, foreground="gray")
        self.drop_hint.grid(row=1, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 6))

        # --- Settings ---
        settings = ttk.LabelFrame(self.root, text="Settings")
        settings.grid(row=1, column=0, sticky="ew", **pad)
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="Crossfade (s)").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(settings, textvariable=self.crossfade_var, width=10).grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(settings, text="Curve").grid(row=1, column=0, sticky="w", **pad)
        ttk.Combobox(
            settings, textvariable=self.curve_var, values=core.CURVES, state="readonly", width=14
        ).grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(settings, text="Safety fade (ms)").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(settings, textvariable=self.safety_var, width=10).grid(row=2, column=1, sticky="w", **pad)

        ttk.Label(settings, text="Output folder").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(settings, textvariable=self.out_var).grid(row=3, column=1, sticky="ew", **pad)
        ttk.Button(settings, text="Browse...", command=self._choose_output).grid(row=3, column=2, **pad)

        # --- Process button ---
        self.process_button = ttk.Button(self.root, text="Process", command=self._start_processing)
        self.process_button.grid(row=2, column=0, sticky="ew", **pad)

        # --- Log ---
        log_frame = ttk.LabelFrame(self.root, text="Log / loop quality report")
        log_frame.grid(row=3, column=0, sticky="nsew", **pad)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = ScrolledText(log_frame, height=12, font=("Consolas", 9), state=tk.DISABLED, wrap=tk.NONE)
        self.log.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 0))
        log_xscroll = ttk.Scrollbar(log_frame, orient=tk.HORIZONTAL, command=self.log.xview)
        log_xscroll.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        self.log.configure(xscrollcommand=log_xscroll.set)

    def _enable_drag_and_drop(self) -> None:
        if TkinterDnD is None or not hasattr(self.root, "drop_target_register"):
            self.drop_hint.configure(text="Tip: install tkinterdnd2 to drag and drop files here.")
            return
        self.file_list.drop_target_register(DND_FILES)
        self.file_list.dnd_bind("<<Drop>>", self._on_drop)
        self.drop_hint.configure(text="Drag and drop audio files or folders onto the list.")

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
        except ValueError:
            messagebox.showerror("LoopMaker", "Crossfade and safety fade must be numbers.")
            return None
        if crossfade <= 0 or safety < 0:
            messagebox.showerror("LoopMaker", "Crossfade must be > 0 and safety fade must be >= 0.")
            return None
        return pipeline.LoopSettings(
            crossfade_s=crossfade,
            curve=self.curve_var.get(),
            safety_fade_ms=safety,
            out_dir=Path(self.out_var.get()),
        )

    def _start_processing(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            messagebox.showinfo("LoopMaker", "Add some audio files first.")
            return
        settings = self._read_settings()
        if settings is None:
            return

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
                self.log_queue.put(f"FAILED  {path.name}: {result}\n")
            else:
                self.log_queue.put(result.format() + "\n")
        self.log_queue.put(f"Done: {len(files) - failures} written, {failures} failed.\n")
        self.log_queue.put(_WORKER_FINISHED)

    def _drain_log_queue(self) -> None:
        try:
            while True:
                message = self.log_queue.get_nowait()
                if message is _WORKER_FINISHED:
                    self.process_button.configure(state=tk.NORMAL, text="Process")
                else:
                    self._log(message)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log_queue)

    def _log(self, text: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)


# Sentinel object the worker puts on the queue when it is finished.
_WORKER_FINISHED = object()


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
