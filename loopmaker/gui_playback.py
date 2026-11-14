"""The GUI's "Test playback" panel: output list, waveform and loop audition.

Waveform peaks and the looping player live in ``preview`` (UI-free); this
module only draws and wires up the widgets.
"""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from . import APP_NAME, pipeline, preview

# Waveform colours (light surface, blue wave, orange crossfade highlight).
WAVE_BG = "#fcfcfb"
WAVE_FG = "#2a78d6"
WAVE_XFADE = "#fbe1d6"
WAVE_MIDLINE = "#d6d5d0"
PLAYHEAD = "#d03b3b"
PEAK_LIMITED_FG = "#b4580f"  # dark orange text for outputs that missed the LUFS target

PLAYHEAD_REFRESH_MS = 30
LOOP_POINT_PREROLL_S = 2.0  # "Play loop point" starts this long before the wrap


class PlaybackPanel(ttk.LabelFrame):
    """Lists written outputs; select one to see its waveform and loop it."""

    def __init__(self, master: tk.Widget, output_folder: Callable[[], Path]) -> None:
        super().__init__(master, text="Test playback")
        self.output_folder = output_folder
        self.results: list[pipeline.ProcessResult] = []
        self.player = preview.LoopPlayer()
        self.preview_data = None
        self.preview_result: pipeline.ProcessResult | None = None
        self.status_var = tk.StringVar(value="Process some files, then pick an output to audition it.")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.output_list = tk.Listbox(self, height=4, exportselection=False, activestyle="none")
        self.output_list.grid(row=0, column=0, sticky="ew", padx=(6, 0), pady=6)
        self.output_list.bind("<<ListboxSelect>>", self._on_selected)

        buttons = ttk.Frame(self)
        buttons.grid(row=0, column=1, sticky="n", padx=6, pady=6)
        self.play_button = ttk.Button(buttons, text="Play  (Space)", command=self.toggle)
        self.play_button.pack(fill=tk.X, pady=2)
        ttk.Button(buttons, text="Play loop point", command=self._play_loop_point).pack(fill=tk.X, pady=2)
        ttk.Button(buttons, text="Open output folder", command=self._open_output_folder).pack(fill=tk.X, pady=2)

        # Waveform: click to play from that point.
        self.wave = tk.Canvas(self, height=110, background=WAVE_BG, highlightthickness=1,
                              highlightbackground=WAVE_MIDLINE, cursor="hand2")
        self.wave.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=6)
        self.wave.bind("<Configure>", lambda _e: self._draw_waveform())
        self.wave.bind("<Button-1>", self._on_wave_click)

        ttk.Label(self, textvariable=self.status_var, foreground="gray").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(2, 6)
        )
        self.after(PLAYHEAD_REFRESH_MS, self._update_playhead)

    # ------------------------------------------------------------- list

    def add_output(self, result: pipeline.ProcessResult) -> None:
        self.results.append(result)
        verdict = "SMOOTH" if result.quality.after.is_smooth else "CHECK BY EAR"
        parts = [result.output_path.name, f"{result.output_seconds:.2f} s", verdict,
                 f"seam {result.seam_score}", result.curve]
        norm = result.normalization
        if norm is not None:
            parts.append(f"{norm.final_lufs:.1f} LUFS")
            if norm.limited_by_peak:
                # Make it obvious without digging through the log.
                parts.append(f"PEAK-LIMITED ({norm.shortfall_db:.1f} dB short of {norm.target_lufs:g})")
        self.output_list.insert(tk.END, "    ".join(parts))
        if norm is not None and norm.limited_by_peak:
            self.output_list.itemconfigure(tk.END, foreground=PEAK_LIMITED_FG)

    def clear(self) -> None:
        self.player.close()
        self.results.clear()
        self.output_list.delete(0, tk.END)
        self.preview_data = None
        self.preview_result = None
        self._draw_waveform()
        self._refresh_play_button()

    def select(self, index: int) -> None:
        if not 0 <= index < len(self.results):
            return
        self.output_list.selection_clear(0, tk.END)
        self.output_list.selection_set(index)
        self.output_list.activate(index)
        self._on_selected()

    def has_selection(self) -> bool:
        return bool(self.output_list.curselection())

    def _on_selected(self, _event=None) -> None:
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

    # ------------------------------------------------------- transport

    def toggle(self) -> None:
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
        folder = self.output_folder()
        if folder.is_dir():
            os.startfile(folder)  # Windows: open in Explorer
        else:
            messagebox.showinfo(APP_NAME, f"The output folder does not exist yet:\n{folder}")

    def close(self) -> None:
        self.player.close()

    # ------------------------------------------------------- waveform

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
                f"{result.output_path.name}  |  {result.output_seconds:.2f} s  |  click the waveform to play "
                f"from there, Space = play/stop, orange = crossfade"
            )

    def _move_playhead(self) -> None:
        width, height = self.wave.winfo_width(), self.wave.winfo_height()
        duration = self.player.duration_s
        if duration > 0:
            x = self.player.position_s / duration * width
            self.wave.coords("playhead", x, 0, x, height)

    def _update_playhead(self) -> None:
        if self.player.is_playing:
            self._move_playhead()
        self.after(PLAYHEAD_REFRESH_MS, self._update_playhead)
