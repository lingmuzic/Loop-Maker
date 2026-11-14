"""Draw the app icon (assets/loop_maker.ico) with Pillow. No external artwork.

A blue rounded square with a white waveform that runs into a loop arrow.
Run from the repo root:  python scripts/make_icon.py
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SIZE = 512
BLUE = (42, 120, 214, 255)
WHITE = (255, 255, 255, 255)


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([16, 16, SIZE - 16, SIZE - 16], radius=110, fill=BLUE)

    # Loop arrow: a ring with a gap, plus an arrow head at the gap.
    box = [96, 96, SIZE - 96, SIZE - 96]
    d.arc(box, start=-60, end=250, fill=WHITE, width=34)
    cx, cy, r = SIZE / 2, SIZE / 2, (SIZE - 192) / 2
    angle = np.radians(-60)
    tip = (cx + r * np.cos(angle), cy + r * np.sin(angle))
    d.polygon([(tip[0] - 52, tip[1] - 6), (tip[0] + 30, tip[1] - 30), (tip[0] + 6, tip[1] + 52)], fill=WHITE)

    # Waveform across the middle (a decaying burst, then steady texture).
    xs = np.linspace(150, SIZE - 150, 15)
    heights = [20, 45, 80, 60, 95, 70, 40, 75, 55, 85, 50, 65, 35, 50, 25]
    for x, h in zip(xs, heights):
        d.rounded_rectangle([x - 7, cy - h, x + 7, cy + h], radius=7, fill=WHITE)
    return img


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "assets" / "loop_maker.ico"
    out.parent.mkdir(exist_ok=True)
    draw().save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Wrote {out}")
