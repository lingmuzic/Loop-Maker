"""Generate docs/before_after.png for the README.

Everything is synthetic (filtered noise with a slow level drift), so the
figure contains no third-party audio. Run from the repo root:

    python scripts/make_readme_figure.py
"""

from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")  # no window needed
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loopmaker.core import make_loop  # noqa: E402

SR = 48_000
DURATION_S = 8.0
CROSSFADE_S = 2.0
ZOOM_MS = 15  # how much to show on each side of the wrap point

# Palette (light mode): one hue for the waveform, a second for the crossfade.
WAVE = "#2a78d6"
XFADE = "#eb6834"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"


def synthetic_ambience() -> np.ndarray:
    """Low-passed noise ('wind') whose level drifts up over the file, plus a slow swell."""
    # Seed chosen so the raw file's end and start land far apart, which makes
    # the click of a naive loop easy to see in the zoom panel.
    rng = np.random.default_rng(14)
    n = int(SR * DURATION_S)
    noise = rng.standard_normal(n + 400)
    smooth = np.convolve(noise, np.hanning(400) / np.hanning(400).sum(), mode="valid")[:n]
    t = np.arange(n) / SR
    level = np.linspace(0.25, 1.0, n) * (1 + 0.3 * np.sin(2 * np.pi * 0.4 * t))
    sig = smooth * level
    return 0.8 * sig / np.max(np.abs(sig))


def style(ax, title):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=10, color=INK, pad=6)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(INK_2)
    ax.tick_params(colors=INK_2, labelsize=8)
    ax.set_ylim(-1, 1)


def wrap_view(signal: np.ndarray):
    """Tail followed by head: what you hear when the file loops."""
    k = int(SR * ZOOM_MS / 1000)
    joined = np.concatenate([signal[-k:], signal[:k]])
    t_ms = (np.arange(len(joined)) - k) / SR * 1000
    return t_ms, joined


def main() -> None:
    original = synthetic_ambience()
    # Safety fade off so the figure shows the raw continuity of the algorithm.
    looped = make_loop(original, SR, CROSSFADE_S, curve="equal_power", safety_fade_s=0)

    fig = plt.figure(figsize=(10, 7.2), facecolor=SURFACE)
    grid = fig.add_gridspec(3, 2, height_ratios=[1, 1, 1.1], hspace=0.6, wspace=0.18)

    # --- 1. Original file -------------------------------------------------
    ax = fig.add_subplot(grid[0, :])
    style(ax, f"Before: original recording ({DURATION_S:g} s), split into halves A | B")
    t = np.arange(len(original)) / SR
    ax.plot(t, original, color=WAVE, linewidth=0.4)
    ax.axvline(DURATION_S / 2, color=INK, linewidth=1, linestyle="--")
    for x, label in [(DURATION_S / 4, "A"), (3 * DURATION_S / 4, "B")]:
        ax.text(x, 0.82, label, ha="center", fontsize=11, color=INK, weight="bold")
    ax.set_xlim(0, DURATION_S)
    ax.set_xlabel("seconds", fontsize=8, color=INK_2)

    # --- 2. Looped file ---------------------------------------------------
    ax = fig.add_subplot(grid[1, :])
    out_len = len(looped) / SR
    style(ax, f"After: halves swapped to B | A, {CROSSFADE_S:g} s equal-power crossfade in the middle ({out_len:g} s)")
    t = np.arange(len(looped)) / SR
    xf_start = DURATION_S / 2 - CROSSFADE_S
    ax.axvspan(xf_start, xf_start + CROSSFADE_S, color=XFADE, alpha=0.18, linewidth=0)
    ax.plot(t, looped, color=WAVE, linewidth=0.4)
    ax.text(xf_start + CROSSFADE_S / 2, -0.93, "crossfade", ha="center", fontsize=8, color=INK_2)
    for x, label in [(xf_start / 2, "B"), (xf_start + CROSSFADE_S + (out_len - xf_start - CROSSFADE_S) / 2, "A")]:
        ax.text(x, 0.82, label, ha="center", fontsize=11, color=INK, weight="bold")
    ax.set_xlim(0, DURATION_S)  # same scale as the original, so the shortening is visible
    ax.set_xlabel("seconds", fontsize=8, color=INK_2)

    # --- 3. Wrap point zooms ----------------------------------------------
    for col, (sig, title) in enumerate(
        [(original, "Loop point of the original: click + level jump"),
         (looped, "Loop point after Loop Maker: continuous")]
    ):
        ax = fig.add_subplot(grid[2, col])
        style(ax, title)
        t_ms, joined = wrap_view(sig)
        ax.axvline(0, color=INK_2, linewidth=1, linestyle=":")
        ax.plot(t_ms, joined, color=WAVE, linewidth=1.2)
        ax.set_xlim(-ZOOM_MS, ZOOM_MS)
        ax.set_xlabel("ms around the wrap (end -> start)", fontsize=8, color=INK_2)

    out = Path(__file__).resolve().parents[1] / "docs" / "before_after.png"
    out.parent.mkdir(exist_ok=True)
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor=SURFACE)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
