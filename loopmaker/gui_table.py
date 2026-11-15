"""Tkinter widgets for the batch file table (no processing logic here).

``FileTable`` shows one row per input file::

    [x] File | Length | Format | Crossfade | Var. | Output | Status | Progress

* Crossfade / Var. are per-file overrides: leave empty to use the global
  setting. A filled-in (overridden) cell is highlighted; an unparsable one
  is shown in red and reported as a row error.
* Status is OK / warning / error, coloured, with a tooltip that explains
  every problem (translated through ``i18n``).
* The checkbox selects rows for "Remove selected" and for the "max
  crossfade" hint next to the global crossfade field.

A per-cell highlight is the reason this is a grid of real widgets rather
than a ttk.Treeview, which can only colour whole rows.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import ttk
from typing import Callable

from . import batch
from . import variations as variations_mod
from .i18n import Translator

STATUS_COLOURS = {batch.OK: "#0a7d0a", batch.WARNING: "#b4580f", batch.ERROR: "#d03b3b"}
OVERRIDE_BG = "#fff3c4"   # highlighted (overridden) cell
INVALID_BG = "#f8d7d7"    # unparsable override
NORMAL_BG = "white"

COLUMN_KEYS = ("", "col_file", "col_length", "col_format", "col_crossfade", "col_var",
               "col_output", "col_status", "col_progress")


class Tooltip:
    """Small hover tooltip; ``text`` is a callable so it is always current."""

    def __init__(self, widget: tk.Widget, text: Callable[[], str]) -> None:
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event=None) -> None:
        content = self.text()
        if not content or self.tip is not None:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=content, justify=tk.LEFT, background="#ffffe8", relief=tk.SOLID,
                 borderwidth=1, wraplength=520, padx=6, pady=4).pack()

    def _hide(self, _event=None) -> None:
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


@dataclass
class Row:
    """Widgets and state of one table row."""

    job: batch.FileJob
    selected: tk.BooleanVar
    crossfade: tk.StringVar
    variations: tk.StringVar
    widgets: list[tk.Widget] = field(default_factory=list)
    status_label: tk.Label | None = None
    format_label: ttk.Label | None = None
    output_label: ttk.Label | None = None
    progress_label: ttk.Label | None = None
    crossfade_entry: tk.Entry | None = None
    variations_entry: tk.Entry | None = None
    validation: batch.Validation | None = None
    progress: tuple[str, dict] | None = None   # (i18n key, params), re-rendered on language change
    progress_detail: str = ""
    override_errors: list[str] = field(default_factory=list)

    def parse_overrides(self) -> None:
        """Copy the override cells into the job; collect parse problems (English, translated on display)."""
        self.override_errors = []
        text = self.crossfade.get().strip()
        if not text:
            self.job.crossfade_s = None
        else:
            try:
                self.job.crossfade_s = float(text)
            except ValueError:
                self.job.crossfade_s = None
                self.override_errors.append(f"crossfade override {text!r} is not a number")
        text = self.variations.get().strip().lower()
        if not text:
            self.job.variations = None
        elif text in ("off", "0", "關閉"):
            self.job.variations = 0
        elif text in {str(n) for n in variations_mod.VARIATION_COUNTS}:
            self.job.variations = int(text)
        else:
            self.job.variations = None
            self.override_errors.append(f"variations override {text!r} must be off, 3, 4 or 5")
        self._paint_override(self.crossfade_entry, self.crossfade.get(), "crossfade")
        self._paint_override(self.variations_entry, self.variations.get(), "variations")

    def _paint_override(self, entry: tk.Entry | None, text: str, label: str) -> None:
        if entry is None:
            return
        bad = any(label in e for e in self.override_errors)
        entry.configure(background=INVALID_BG if bad else (OVERRIDE_BG if text.strip() else NORMAL_BG))


class FileTable(ttk.Frame):
    """Scrollable table of FileJobs. ``on_change`` fires when overrides/selection change."""

    def __init__(self, master: tk.Widget, tr: Translator, on_change: Callable[[], None]) -> None:
        super().__init__(master)
        self.tr = tr
        self.on_change = on_change
        self.rows: list[Row] = []
        self.dnd_available = True

        self.canvas = tk.Canvas(self, highlightthickness=0, background=NORMAL_BG, height=150)
        scroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.inner = tk.Frame(self.canvas, background=NORMAL_BG)
        self._window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        for widget in (self.canvas, self.inner):
            widget.bind("<Enter>", lambda _e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
            widget.bind("<Leave>", lambda _e: self.canvas.unbind_all("<MouseWheel>"))

        self.headers = []
        for col, key in enumerate(COLUMN_KEYS):
            header = tk.Label(self.inner, background="#ecebe7", anchor="w", padx=4, font=("Segoe UI", 9, "bold"))
            header.grid(row=0, column=col, sticky="ew")
            self.headers.append((header, key))
        self.inner.columnconfigure(1, weight=2)  # file name
        self.inner.columnconfigure(6, weight=3)  # output names

        self.empty_hint = tk.Label(self.inner, foreground="gray", background=NORMAL_BG, pady=16)
        self.empty_hint.grid(row=1, column=0, columnspan=len(COLUMN_KEYS))
        self.retranslate()

    # ------------------------------------------------------------ language

    def retranslate(self) -> None:
        """Re-render every translated text (after a language change)."""
        for header, key in self.headers:
            header.configure(text=self.tr.t(key) if key else "")
        self.empty_hint.configure(text=self.tr.t("table_empty" if self.dnd_available else "table_empty_no_dnd"))
        for row in self.rows:
            self._render_format(row)
            self.show_validation(row, row.validation)
            self._render_progress(row)

    def tooltip_for(self, validation: batch.Validation | None) -> str:
        if validation is None:
            return ""
        tr = self.tr
        lines = [tr.t("tip_error", msg=tr.message(e)) for e in validation.errors]
        lines += [tr.t("tip_warning", msg=tr.message(w)) for w in validation.warnings]
        lines += [tr.t("tip_clamp", msg=tr.message(c)) for c in validation.changes]
        return "\n".join(lines) or "OK"

    # ------------------------------------------------------------ rows

    def add(self, path: Path) -> None:
        job = batch.FileJob(Path(path))
        row = Row(job, tk.BooleanVar(value=False), tk.StringVar(), tk.StringVar())
        r = len(self.rows) + 1
        info = job.info()
        length = f"{info.duration_s:.1f} s" if info else "?"

        check = tk.Checkbutton(self.inner, variable=row.selected, background=NORMAL_BG, command=self.on_change)
        name = ttk.Label(self.inner, text=job.path.name, background=NORMAL_BG)
        Tooltip(name, lambda: str(job.path))
        row.format_label = ttk.Label(self.inner, background=NORMAL_BG)
        row.crossfade_entry = tk.Entry(self.inner, textvariable=row.crossfade, width=6, background=NORMAL_BG)
        row.variations_entry = tk.Entry(self.inner, textvariable=row.variations, width=4, background=NORMAL_BG)
        Tooltip(row.crossfade_entry, lambda: self.tr.t("tip_crossfade_override"))
        Tooltip(row.variations_entry, lambda: self.tr.t("tip_variations_override"))
        row.output_label = ttk.Label(self.inner, text="", background=NORMAL_BG, foreground="#52514e")
        Tooltip(row.output_label, lambda: "\n".join(p.name for p in (row.validation.output_paths if row.validation else [])))
        row.status_label = tk.Label(self.inner, text="", background=NORMAL_BG, anchor="w", font=("Segoe UI", 9, "bold"))
        Tooltip(row.status_label, lambda: self.tooltip_for(row.validation))
        row.progress_label = ttk.Label(self.inner, text="", background=NORMAL_BG)
        Tooltip(row.progress_label, lambda: row.progress_detail)

        row.widgets = [check, name, ttk.Label(self.inner, text=length, background=NORMAL_BG),
                       row.format_label, row.crossfade_entry, row.variations_entry, row.output_label,
                       row.status_label, row.progress_label]
        for col, widget in enumerate(row.widgets):
            widget.grid(row=r, column=col, sticky="w" if col not in (1, 6) else "ew", padx=3, pady=1)
        for var in (row.crossfade, row.variations):
            var.trace_add("write", lambda *_: self.on_change())
        self.rows.append(row)
        self._render_format(row)
        self.empty_hint.grid_remove()

    def _render_format(self, row: Row) -> None:
        info = row.job.info()
        text = (f"{info.sample_rate / 1000:g} kHz {info.channels} ch {info.subtype.replace('PCM_', '')}"
                if info else self.tr.t("unreadable"))
        row.format_label.configure(text=text)

    def remove_selected(self) -> int:
        keep = [row for row in self.rows if not row.selected.get()]
        removed = len(self.rows) - len(keep)
        self._rebuild(keep)
        return removed

    def clear(self) -> None:
        self._rebuild([])

    def _rebuild(self, keep: list[Row]) -> None:
        for row in self.rows:
            for widget in row.widgets:
                widget.destroy()
        paths = [(row.job.path, row.crossfade.get(), row.variations.get()) for row in keep]
        self.rows = []
        for path, crossfade, count in paths:
            self.add(path)
            self.rows[-1].crossfade.set(crossfade)
            self.rows[-1].variations.set(count)
        if not self.rows:
            self.empty_hint.grid()
        self.on_change()

    def paths(self) -> list[Path]:
        return [row.job.path for row in self.rows]

    def selected_rows(self) -> list[Row]:
        return [row for row in self.rows if row.selected.get()]

    # ------------------------------------------------------- display

    def show_validation(self, row: Row, validation: batch.Validation | None) -> None:
        row.validation = validation
        if validation is None:
            row.status_label.configure(text="-", foreground="gray")
            row.output_label.configure(text="")
            return
        row.status_label.configure(text=self.tr.t(f"status_{validation.status}"),
                                   foreground=STATUS_COLOURS[validation.status])
        names = [p.name for p in validation.output_paths]
        text = names[0] + (f"  (+{len(names) - 1})" if len(names) > 1 else "") if names else "-"
        row.output_label.configure(text=text)

    def show_progress(self, index: int, key: str | None, detail: str = "", **params) -> None:
        """Set a row's progress cell from an i18n key (None clears it)."""
        if 0 <= index < len(self.rows):
            row = self.rows[index]
            row.progress = (key, params) if key else None
            row.progress_detail = detail
            self._render_progress(row)

    def _render_progress(self, row: Row) -> None:
        if row.progress is None:
            row.progress_label.configure(text="")
        else:
            key, params = row.progress
            row.progress_label.configure(text=self.tr.t(key, **params))

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")
