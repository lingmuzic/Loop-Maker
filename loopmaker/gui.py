"""Loop Maker main window. Run with ``python -m loopmaker.gui``.

The GUI is deliberately thin. It collects settings and per-file overrides,
then uses the same UI-free modules as the CLI:

* ``batch.validate_batch``  live per-file validation (re-run on every change)
* ``batch.run_batch``       processing with the "On problem" policy + report
* ``preview``               waveform peaks and loop playback (via gui_playback)

Window layout, top to bottom:
    Files (table) -> Settings -> summary + Process -> Test playback -> Log

Language: every visible text comes from ``i18n`` (English / Traditional
Chinese). Widgets that show text are registered with :meth:`_text`, so a
language change re-renders the whole window in place.

Threading: processing runs on a worker thread so the window stays
responsive. The worker never touches widgets (Tkinter is not thread-safe);
it posts messages to a queue that the Tk main loop drains every 100 ms.

Optional extras, each degrading gracefully when missing:
- ``tkinterdnd2``: drag-and-drop onto the file table
- ``sounddevice``: playback in the Test playback panel
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import APP_NAME, audio_io, batch, core, i18n, loudness, naming, pipeline, presets, variations
from .gui_playback import PlaybackPanel
from .gui_table import STATUS_COLOURS, FileTable
from .paths import app_dir, resource_path

try:  # Optional dependency: drag-and-drop support.
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # pragma: no cover - depends on the environment
    TkinterDnD = None

ICON_FILE = "assets/loop_maker.ico"
VALIDATE_DELAY_MS = 150  # debounce: re-validate shortly after the last keystroke
SETTINGS_ERROR_FG = STATUS_COLOURS[batch.ERROR]


class LoopMakerApp:
    """Main window: file table, global settings, batch run, playback and log."""

    def __init__(self, root: tk.Tk, language: str | None = None) -> None:
        self.root = root
        self.tr = i18n.Translator(language or i18n.load_language())
        self.log_queue: queue.Queue[tuple] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.validations: list[batch.Validation] = []
        self.last_report: batch.BatchReport | None = None
        self._validate_job: str | None = None
        self._syncing = False
        self._texts: list[tuple[tk.Widget, str]] = []      # (widget, i18n key) for re-translation
        self._combos: list[tuple[ttk.Combobox, str]] = []  # translated comboboxes by kind

        root.title(APP_NAME)
        root.minsize(960, 820)
        icon = resource_path(ICON_FILE)
        if icon.is_file():
            try:
                root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass  # cosmetic only

        self.presets, preset_warnings = presets.load_presets()
        self._create_variables()
        self._build_layout()
        self._enable_drag_and_drop()
        self.root.bind_all("<space>", self._on_space)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        for warning in preset_warnings:
            self._log(f"WARNING {warning}")
        self._on_settings_changed()
        self.root.after(100, self._drain_queue)

    # ---------------------------------------------------- translation helpers

    def _text(self, widget: tk.Widget, key: str) -> tk.Widget:
        """Give ``widget`` the translated text for ``key`` and keep it updated."""
        widget.configure(text=self.tr.t(key))
        self._texts.append((widget, key))
        return widget

    def _choices(self, kind: str) -> list[tuple[str, str]]:
        """(internal value, displayed text) pairs for a translated combobox."""
        if kind == "variations":
            return [("0", self.tr.t("off"))] + [(str(n), str(n)) for n in variations.VARIATION_COUNTS]
        if kind == "policy":
            return [(p, self.tr.t(f"policy_{p}")) for p in batch.POLICIES]
        if kind == "rate":
            return [(presets.SOURCE, self.tr.t("source"))] + [(str(r), str(r)) for r in presets.SAMPLE_RATES]
        if kind == "depth":
            return [(presets.SOURCE, self.tr.t("source"))] + [(d, d) for d in presets.BIT_DEPTHS]
        raise ValueError(kind)

    def _internal(self, kind: str, var: tk.StringVar) -> str:
        """Internal value behind a combobox's displayed text (any language)."""
        shown = var.get()
        for language in i18n.LANGUAGES:
            saved, self.tr.language = self.tr.language, language
            try:
                for internal, display in self._choices(kind):
                    if display == shown:
                        return internal
            finally:
                self.tr.language = saved
        return shown

    def _display(self, kind: str, internal: str) -> str:
        return dict(self._choices(kind)).get(internal, internal)

    def _set_language(self, language: str) -> None:
        if language == self.tr.language:
            return
        kinds = {"variations": self.variations_var, "policy": self.policy_var,
                 "rate": self.rate_var, "depth": self.depth_var}
        values = {kind: self._internal(kind, var) for kind, var in kinds.items()}
        self.tr.language = language
        i18n.save_language(language)

        self._syncing = True  # the settings themselves do not change
        try:
            for kind, var in kinds.items():
                var.set(self._display(kind, values[kind]))
        finally:
            self._syncing = False
        for widget, key in self._texts:
            widget.configure(text=self.tr.t(key))
        for box, kind in self._combos:
            box.configure(values=[display for _, display in self._choices(kind)])
        running = self.worker is not None and self.worker.is_alive()
        self.process_button.configure(text=self.tr.t("processing" if running else "process"))
        self.table.retranslate()
        self.playback.retranslate()
        self._validate_now()

    # --------------------------------------------------------------- state

    def _create_variables(self) -> None:
        """Tk variables bound to the global settings widgets."""
        sv = tk.StringVar
        self.crossfade_var = sv(value=str(pipeline.DEFAULT_CROSSFADE_S))
        self.curve_var = sv(value=core.DEFAULT_CURVE)
        self.safety_var = sv(value=f"{core.DEFAULT_SAFETY_FADE_S * 1000:g}")
        self.variations_var = sv(value=self._display("variations", "0"))
        self.spacing_var = sv(value=f"{variations.DEFAULT_MIN_SPACING * 100:g}")
        self.short_warn_var = sv(value=f"{variations.DEFAULT_SHORT_WARNING_S:g}")
        self.policy_var = sv(value=self._display("policy", batch.DEFAULT_POLICY))
        self.preset_var = sv(value=presets.CUSTOM)
        self.rate_var = sv(value=self._display("rate", presets.SOURCE))
        self.depth_var = sv(value=self._display("depth", presets.SOURCE))
        self.format_var = sv(value="WAV")
        self.normalize_var = tk.BooleanVar(value=False)
        self.target_lufs_var = sv(value=f"{loudness.DEFAULT_TARGET_LUFS:g}")
        self.ceiling_var = sv(value=f"{loudness.DEFAULT_PEAK_CEILING_DBTP:g}")
        self.template_var = sv(value=naming.DEFAULT_TEMPLATE)
        self.category_var = sv(value=naming.DEFAULT_CATEGORY)
        self.suffix_var = sv(value=naming.DEFAULT_SUFFIX)
        # Default output: next to the project / next to Loop Maker.exe, so it
        # does not depend on the working directory a shortcut happens to use.
        self.out_var = sv(value=str(app_dir() / pipeline.DEFAULT_OUT_DIR))
        self.language_var = sv(value=i18n.LANGUAGES[self.tr.language])
        self.max_xfade_var = sv()
        self.template_hint_var = sv()
        self.summary_var = sv()

        for var in (self.crossfade_var, self.curve_var, self.safety_var, self.variations_var, self.spacing_var,
                    self.short_warn_var, self.policy_var, self.preset_var, self.rate_var, self.depth_var,
                    self.format_var, self.normalize_var, self.target_lufs_var, self.ceiling_var,
                    self.template_var, self.category_var, self.suffix_var, self.out_var):
            var.trace_add("write", lambda *_: self._on_settings_changed())

    # -------------------------------------------------------------- layout

    def _build_layout(self) -> None:
        pad = {"padx": 8, "pady": 4}
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=2)  # file table
        self.root.rowconfigure(3, weight=2)  # test playback
        self.root.rowconfigure(4, weight=1)  # log

        self._build_files(row=0, pad=pad)
        self._build_settings(row=1, pad=pad)
        self._build_run_bar(row=2, pad=pad)
        self.playback = PlaybackPanel(self.root, self.tr, lambda: Path(self.out_var.get()))
        self.playback.grid(row=3, column=0, sticky="nsew", **pad)
        self._build_log(row=4, pad=pad)

    def _build_files(self, row: int, pad: dict) -> None:
        frame = self._text(ttk.LabelFrame(self.root), "files_frame")
        frame.grid(row=row, column=0, sticky="nsew", **pad)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.table = FileTable(frame, self.tr, on_change=self._schedule_validate)
        self.table.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)

        buttons = ttk.Frame(frame)
        buttons.grid(row=0, column=1, sticky="n", padx=6, pady=6)
        for key, command in [
            ("add_files", self._add_files_dialog),
            ("add_folder", self._add_folder_dialog),
            ("remove_selected", self._remove_selected),
            ("clear", self._clear_files),
        ]:
            self._text(ttk.Button(buttons, command=command), key).pack(fill=tk.X, pady=2)

    def _build_settings(self, row: int, pad: dict) -> None:
        frame = self._text(ttk.LabelFrame(self.root), "settings_frame")
        frame.grid(row=row, column=0, sticky="ew", **pad)
        frame.columnconfigure(0, weight=1)

        def line(r: int) -> ttk.Frame:
            f = ttk.Frame(frame)
            f.grid(row=r, column=0, sticky="ew")
            return f

        def label(parent, key):
            self._text(ttk.Label(parent), key).pack(side=tk.LEFT, **pad)

        def entry(parent, var, width):
            ttk.Entry(parent, textvariable=var, width=width).pack(side=tk.LEFT)

        def combo(parent, var, values, width, kind=None):
            if kind:
                values = [display for _, display in self._choices(kind)]
            box = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=width)
            box.pack(side=tk.LEFT)
            if kind:
                self._combos.append((box, kind))
            return box

        r0 = line(0)
        label(r0, "crossfade_s")
        entry(r0, self.crossfade_var, 7)
        ttk.Label(r0, textvariable=self.max_xfade_var, foreground="gray").pack(side=tk.LEFT, **pad)
        label(r0, "curve")
        combo(r0, self.curve_var, core.CURVES, 11)
        label(r0, "safety_fade_ms")
        entry(r0, self.safety_var, 5)

        r1 = line(1)
        label(r1, "variations")
        combo(r1, self.variations_var, None, 6, kind="variations")
        label(r1, "min_spacing")
        entry(r1, self.spacing_var, 5)
        label(r1, "warn_below")
        entry(r1, self.short_warn_var, 5)
        label(r1, "on_problem")
        combo(r1, self.policy_var, None, 14, kind="policy")

        r2 = line(2)
        label(r2, "preset")
        combo(r2, self.preset_var, [presets.CUSTOM, *self.presets], 10)
        self.custom_widgets = []
        for key, var, values, kind in [
            ("sample_rate", self.rate_var, None, "rate"),
            ("bit_depth", self.depth_var, None, "depth"),
            ("format", self.format_var, list(presets.available_formats()), None),
        ]:
            label(r2, key)
            self.custom_widgets.append(combo(r2, var, values, 9, kind=kind))
        self._text(ttk.Checkbutton(r2, variable=self.normalize_var), "normalize_to").pack(side=tk.LEFT, padx=(16, 4))
        entry(r2, self.target_lufs_var, 6)
        label(r2, "lufs_ceiling")
        entry(r2, self.ceiling_var, 5)
        label(r2, "dbtp")

        r3 = line(3)
        label(r3, "name_template")
        entry(r3, self.template_var, 30)
        ttk.Label(r3, text="{category}").pack(side=tk.LEFT, **pad)
        entry(r3, self.category_var, 8)
        ttk.Label(r3, text="{suffix}").pack(side=tk.LEFT, **pad)
        entry(r3, self.suffix_var, 8)
        self.template_hint = ttk.Label(r3, textvariable=self.template_hint_var, foreground="gray")
        self.template_hint.pack(side=tk.LEFT, **pad)

        r4 = line(4)
        label(r4, "output_folder")
        ttk.Entry(r4, textvariable=self.out_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._text(ttk.Button(r4, command=self._choose_output), "browse").pack(side=tk.LEFT, **pad)

    def _build_run_bar(self, row: int, pad: dict) -> None:
        bar = ttk.Frame(self.root)
        bar.grid(row=row, column=0, sticky="ew", **pad)
        bar.columnconfigure(0, weight=1)
        self.summary_label = ttk.Label(bar, textvariable=self.summary_var, font=("Segoe UI", 10, "bold"))
        self.summary_label.grid(row=0, column=0, sticky="w")
        self.process_button = self._text(ttk.Button(bar, command=self._start_processing, width=16), "process")
        self.process_button.grid(row=0, column=1, padx=4)
        self.export_button = self._text(ttk.Button(bar, command=self._export_report, state=tk.DISABLED), "export_report")
        self.export_button.grid(row=0, column=2)
        self._text(ttk.Label(bar), "language").grid(row=0, column=3, padx=(24, 4))
        language_box = ttk.Combobox(bar, textvariable=self.language_var, values=list(i18n.LANGUAGES.values()),
                                    state="readonly", width=10)
        language_box.grid(row=0, column=4)
        language_box.bind("<<ComboboxSelected>>", lambda _e: self._set_language(
            next(code for code, name in i18n.LANGUAGES.items() if name == self.language_var.get())))

    def _build_log(self, row: int, pad: dict) -> None:
        frame = self._text(ttk.LabelFrame(self.root), "log_frame")
        frame.grid(row=row, column=0, sticky="nsew", **pad)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.log = ScrolledText(frame, height=6, font=("Consolas", 9), state=tk.DISABLED, wrap=tk.NONE)
        self.log.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 0))
        xscroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.log.xview)
        xscroll.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        self.log.configure(xscrollcommand=xscroll.set)

    def _enable_drag_and_drop(self) -> None:
        if TkinterDnD is None or not hasattr(self.root, "drop_target_register"):
            self.table.dnd_available = False
            self.table.retranslate()
            return
        for widget in (self.table.canvas, self.table.inner, self.table.empty_hint):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_drop)

    def _on_space(self, event) -> str | None:
        # Space toggles playback, except where it already means something:
        # typing in a text field, pressing a focused button or checkbox.
        if isinstance(event.widget, (tk.Entry, ttk.Button, tk.Button, tk.Text, tk.Checkbutton, ttk.Checkbutton)):
            return None
        self.playback.toggle()
        return "break"

    # ----------------------------------------------------------- file table

    def _filetypes(self) -> list[tuple[str, str]]:
        return [
            (self.tr.t("ft_audio"), " ".join(f"*{ext}" for ext in audio_io.SUPPORTED_EXTENSIONS)),
            (self.tr.t("ft_all"), "*.*"),
        ]

    def _add_paths(self, paths: list[str]) -> None:
        """Add files/folders (folders are expanded) and report anything skipped."""
        found, problems = audio_io.collect_inputs(paths)
        known = {p.resolve() for p in self.table.paths()}
        for path in found:
            if path.resolve() not in known:
                self.table.add(path)
        for problem in problems:
            self._log(f"SKIPPED {problem}")
        self._schedule_validate()

    def _on_drop(self, event) -> None:
        # tkinterdnd2 delivers a Tcl list; splitlist handles paths with spaces/braces.
        self._add_paths(list(self.root.tk.splitlist(event.data)))

    def _add_files_dialog(self) -> None:
        self._add_paths(list(filedialog.askopenfilenames(title=self.tr.t("dlg_add_files"),
                                                         filetypes=self._filetypes())))

    def _add_folder_dialog(self) -> None:
        folder = filedialog.askdirectory(title=self.tr.t("dlg_add_folder"))
        if folder:
            self._add_paths([folder])

    def _remove_selected(self) -> None:
        if not self.table.remove_selected():
            messagebox.showinfo(APP_NAME, self.tr.t("dlg_tick_rows"))

    def _clear_files(self) -> None:
        self.table.clear()

    def _choose_output(self) -> None:
        folder = filedialog.askdirectory(title=self.tr.t("dlg_choose_output"), initialdir=self.out_var.get())
        if folder:
            self.out_var.set(folder)

    # ------------------------------------------------------------ settings

    def _policy(self) -> str:
        return self._internal("policy", self.policy_var)

    def _current_output_spec(self) -> presets.OutputSpec:
        name = self.preset_var.get()
        if name != presets.CUSTOM and name in self.presets:
            return self.presets[name].spec
        rate, depth = self._internal("rate", self.rate_var), self._internal("depth", self.depth_var)
        return presets.OutputSpec(
            sample_rate=None if rate == presets.SOURCE else int(rate),
            bit_depth=None if depth == presets.SOURCE else depth,
            file_format=self.format_var.get(),
        )

    def _collect_settings(self) -> tuple[pipeline.LoopSettings | None, list[str]]:
        """Build LoopSettings from the form. Returns (settings, problems in English); no dialogs."""
        numbers = {}
        problems = []
        for key, var, label in [
            ("crossfade", self.crossfade_var, "crossfade"), ("safety", self.safety_var, "safety fade"),
            ("spacing", self.spacing_var, "min spacing"), ("warn", self.short_warn_var, "warn-below"),
            ("target", self.target_lufs_var, "LUFS target"), ("ceiling", self.ceiling_var, "true-peak ceiling"),
        ]:
            try:
                numbers[key] = float(var.get())
            except ValueError:
                problems.append(f"{label} must be a number")
        if problems:
            return None, problems
        if numbers["crossfade"] <= 0:
            problems.append("crossfade must be greater than 0")
        if numbers["safety"] < 0:
            problems.append("safety fade cannot be negative")
        settings = pipeline.LoopSettings(
            crossfade_s=numbers["crossfade"],
            curve=self.curve_var.get(),
            safety_fade_ms=numbers["safety"],
            out_dir=Path(self.out_var.get()),
            normalize_lufs=numbers["target"] if self.normalize_var.get() else None,
            peak_ceiling_dbtp=numbers["ceiling"],
            output=self._current_output_spec(),
            naming=naming.Naming(self.template_var.get(), self.category_var.get(), self.suffix_var.get()),
            variations=int(self._internal("variations", self.variations_var)),
            min_spacing=numbers["spacing"] / 100.0,
            short_warning_s=numbers["warn"],
        )
        problems += settings.errors()
        return (None if problems else settings), problems

    def _on_settings_changed(self) -> None:
        """Keep the Custom fields in sync with the preset, then re-validate."""
        if self._syncing:
            return  # writing the fields below re-triggers this callback
        self._syncing = True
        try:
            name = self.preset_var.get()
            is_custom = name == presets.CUSTOM or name not in self.presets
            for box in getattr(self, "custom_widgets", []):
                box.configure(state="readonly" if is_custom else tk.DISABLED)
            if not is_custom:  # show what the preset will do
                spec = self.presets[name].spec
                self.rate_var.set(self._display("rate", str(spec.sample_rate) if spec.sample_rate else presets.SOURCE))
                self.depth_var.set(self._display("depth", spec.bit_depth or presets.SOURCE))
                self.format_var.set(spec.file_format)
        finally:
            self._syncing = False
        self._schedule_validate()

    # ---------------------------------------------------------- validation

    def _schedule_validate(self) -> None:
        """Debounced live validation (every settings change or table edit)."""
        if self._validate_job is not None:
            self.root.after_cancel(self._validate_job)
        self._validate_job = self.root.after(VALIDATE_DELAY_MS, self._validate_now)

    def _summary(self, validations: list[batch.Validation]) -> str:
        return self.tr.t(
            "summary", files=len(validations),
            ok=sum(v.status == batch.OK for v in validations),
            warn=sum(v.status == batch.WARNING for v in validations),
            bad=sum(v.status == batch.ERROR for v in validations),
        )

    def _validate_now(self) -> None:
        self._validate_job = None
        settings, problems = self._collect_settings()
        rows = self.table.rows

        if settings is None:
            self.validations = []
            for row in rows:
                self.table.show_validation(row, None)
            self.template_hint_var.set("")
            self.summary_label.configure(foreground=SETTINGS_ERROR_FG)
            self.summary_var.set(self.tr.t("settings_problem",
                                           problems="; ".join(self.tr.message(p) for p in problems)))
            self.max_xfade_var.set("")
            self._update_process_button()
            return

        warnings = settings.naming.warnings()
        self.template_hint.configure(foreground=STATUS_COLOURS[batch.WARNING] if warnings else "gray")
        example = settings.naming.render("Example Recording", 1) + audio_io.EXTENSIONS[settings.output.file_format]
        self.template_hint_var.set(self.tr.message(warnings[0]) if warnings else self.tr.t("example_name", name=example))

        for row in rows:
            row.parse_overrides()
        self.validations = batch.validate_batch([row.job for row in rows], settings, self._policy())
        for row, validation in zip(rows, self.validations):
            if row.override_errors:  # unparsable override cells make the row an error
                validation.errors = row.override_errors + validation.errors
                validation.status = batch.ERROR
                validation.output_paths = []
            self.table.show_validation(row, validation)

        self.summary_label.configure(foreground="")
        self.summary_var.set(self._summary(self.validations) if rows else self.tr.t("add_files_to_begin"))
        self._update_max_crossfade()
        self._update_process_button()

    def _update_max_crossfade(self) -> None:
        selected = [row.validation for row in self.table.selected_rows() if row.validation]
        pool = selected or self.validations
        value = batch.batch_max_crossfade(pool)
        if value is None:
            self.max_xfade_var.set("")
        elif selected:
            self.max_xfade_var.set(self.tr.t("max_for_selected", value=value, n=len(selected)))
        else:
            self.max_xfade_var.set(self.tr.t("max_for_all", value=value, n=len(pool)))

    def _update_process_button(self) -> None:
        running = self.worker is not None and self.worker.is_alive()
        ready = any(v.processable for v in self.validations)
        self.process_button.configure(state=tk.NORMAL if ready and not running else tk.DISABLED)

    # ---------------------------------------------------------- processing

    def _start_processing(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self._validate_now()  # make sure we run exactly what is shown
        settings, problems = self._collect_settings()
        if settings is None:
            messagebox.showerror(APP_NAME, self.tr.t("dlg_fix_settings") + "\n\n"
                                 + "\n".join(self.tr.message(p) for p in problems))
            return
        validations, policy = list(self.validations), self._policy()
        if not any(v.processable for v in validations):
            return

        self.playback.clear()  # outputs may be overwritten: release the preview first
        for i, v in enumerate(validations):
            self.table.show_progress(i, "progress_queued" if v.processable else None)
        # The log stays in English, like the CLI output and the exported report.
        self._log(f"Starting batch. {batch.summary_line(validations)}. On problem: {batch.POLICY_LABELS[policy]}.")
        for v in validations:
            for change in v.changes:
                self._log(f"AUTO-CLAMP {change}")
        self.process_button.configure(state=tk.DISABLED, text=self.tr.t("processing"))
        self.export_button.configure(state=tk.DISABLED)
        self.worker = threading.Thread(target=self._worker, args=(validations, settings, policy), daemon=True)
        self.worker.start()

    def _worker(self, validations, settings, policy) -> None:
        """Runs off the UI thread. Communicates only through ``log_queue``."""
        def on_event(kind: str, index: int, payload) -> None:
            self.log_queue.put((kind, index, payload))

        try:
            report = batch.run_batch(validations, settings, policy, on_event)
        except Exception as exc:  # noqa: BLE001 - never leave the UI stuck in "Processing..."
            self.log_queue.put(("crash", -1, exc))
        else:
            self.log_queue.put(("report", -1, report))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, index, payload = self.log_queue.get_nowait()
                if kind == "start":
                    self.table.show_progress(index, "progress_processing")
                elif kind == "done":
                    self._show_file_done(index, payload)
                elif kind == "report":
                    self._finish_batch(payload)
                elif kind == "crash":
                    self._log(f"ERROR batch crashed: {payload}")
                    self._finish_batch(None)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    def _show_file_done(self, index: int, report: batch.FileReport) -> None:
        if report.status == batch.WRITTEN:
            self.table.show_progress(index, "progress_done", report.format(), n=len(report.outputs))
            for output in report.outputs:
                self.playback.add_output(output)
                self._log(output.format() + "\n")
        else:
            self.table.show_progress(index, f"progress_{report.status}", self.tr.message(report.reason))
            self._log(f"{report.status.upper()} {report.input_path.name}: {report.reason}\n")

    def _finish_batch(self, report: batch.BatchReport | None) -> None:
        self.process_button.configure(text=self.tr.t("process"))
        self._update_process_button()
        if report is not None:
            self.last_report = report
            self.export_button.configure(state=tk.NORMAL)
            self._log("=" * 72 + "\n" + report.format())
        if self.playback.results and not self.playback.has_selection():
            self.playback.select(0)

    def _export_report(self) -> None:
        if self.last_report is None:
            return
        path = filedialog.asksaveasfilename(
            title=self.tr.t("dlg_export_report"), defaultextension=".txt", initialdir=self.out_var.get(),
            initialfile=f"loop_maker_report_{self.last_report.created:%Y%m%d_%H%M%S}.txt",
            filetypes=[(self.tr.t("ft_text"), "*.txt"), (self.tr.t("ft_all"), "*.*")],
        )
        if path:
            self.last_report.export(Path(path))
            self._log(f"Report exported to {path}")

    def _log(self, text: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _on_close(self) -> None:
        self.playback.close()  # stop the audio stream before the window goes away
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
