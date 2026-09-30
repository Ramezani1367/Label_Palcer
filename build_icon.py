"""Build the app icon used by the Windows exe and the browser tab.

Run from the repository root:

    python build_icon.py

Outputs:
    assets/icon-256.png   source image
    assets/icon.ico       multi-size icon embedded in LabelPlacer.exe
    static/favicon.png    browser tab
    static/favicon.ico    browser tab, older browsers
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
STATIC = ROOT / "static"
SIZES = (16, 24, 32, 48, 64, 128, 256)

INK = (22, 19, 16, 255)
PAPER = (243, 239, 228, 255)
BRASS = (198, 161, 90, 255)
MARK = (210, 74, 54, 255)


def draw_icon(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = max(1, round(size * 0.06))
    radius = max(2, round(size * 0.18))
    draw.rounded_rectangle([pad, pad, size - pad - 1, size - pad - 1], radius=radius, fill=INK)

    inset = round(size * 0.18)
    arm = round(size * 0.11)
    stroke = max(1, round(size * 0.04))
    corners = [
        (inset, inset, 1, 1),
        (size - inset - 1, inset, -1, 1),
        (size - inset - 1, size - inset - 1, -1, -1),
        (inset, size - inset - 1, 1, -1),
    ]
    for x, y, sx, sy in corners:
        draw.line([(x, y), (x + sx * arm, y)], fill=BRASS, width=stroke)
        draw.line([(x, y), (x, y + sy * arm)], fill=BRASS, width=stroke)

    label = round(size * 0.32)
    box = [label, label, size - label - 1, size - label - 1]
    draw.rounded_rectangle(box, radius=max(1, size // 32), fill=PAPER)
    draw.rounded_rectangle(box, radius=max(1, size // 32), outline=MARK, width=max(1, stroke - 1))
    return image


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    master = draw_icon(256)
    master.save(ASSETS / "icon-256.png")
    icons = [master.resize((size, size), Image.Resampling.LANCZOS) for size in SIZES if size != 256]
    master.save(ASSETS / "icon.ico", format="ICO", sizes=[(size, size) for size in SIZES], append_images=icons)
    master.resize((32, 32), Image.Resampling.LANCZOS).save(STATIC / "favicon.png")
    master.save(STATIC / "favicon.ico", format="ICO", sizes=[(size, size) for size in (16, 32, 48)])
    print(f"icon: {ASSETS / 'icon.ico'}")


if __name__ == "__main__":
    main()
