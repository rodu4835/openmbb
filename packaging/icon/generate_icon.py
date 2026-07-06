"""Regenerate the OpenMBB app icon (the "gauge bolt": a gauge ring with a
lightning-bolt needle, volt green on an ink tile).

    python packaging/icon/generate_icon.py        # requires pillow (dev-only)

Outputs, all committed to the repo so normal builds never need Pillow:
  packaging/icon/openmbb.ico        multi-size .ico for the .exe / installer
  src/openmbb/assets/icon_*.png     runtime window icons (Tk iconphoto)

Each size is drawn at 8x supersample and downscaled, with the glyph occupying
more of the tile at small sizes (matching how real taskbar icons are cut).
"""

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.abspath(os.path.join(HERE, "..", "..", "src", "openmbb", "assets"))

BG = (0x12, 0x17, 0x0C, 255)        # ink tile
FG = (0xA4, 0xE2, 0x3C, 255)        # volt green

# (icon size, glyph fraction of tile, canvas margin fraction)
SIZES = [(256, 0.70, 0.04), (64, 0.70, 0.03), (48, 0.70, 0.02),
         (32, 0.78, 0.00), (16, 0.88, 0.00)]

SS = 8                              # supersample factor

# Glyph geometry in a 48-unit box (center 24,24), from the approved concept.
RING_R, RING_W = 19.0, 3.4
TICKS = [((24, 6.5), (24, 10)), ((41.5, 24), (38, 24)),
         ((24, 41.5), (24, 38)), ((6.5, 24), (10, 24))]
TICK_W = 2.4
BOLT = [(26, 4), (12, 27), (21, 27), (19, 44), (36, 19), (27, 19)]
BOLT_SCALE = 0.66


def draw_tile(size, glyph_frac, margin_frac):
    c = size * SS
    img = Image.new("RGBA", (c, c), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    m = c * margin_frac
    d.rounded_rectangle([m, m, c - 1 - m, c - 1 - m],
                        radius=(c - 2 * m) * 0.225, fill=BG)

    # Map 48-unit glyph space onto the tile.
    g = (c - 2 * m) * glyph_frac
    k = g / 48.0
    off = (c - g) / 2.0

    def pt(x, y):
        return (off + x * k, off + y * k)

    r, w = RING_R * k, max(RING_W * k, 1)
    cx, cy = pt(24, 24)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=FG, width=round(w))

    for (a, b) in TICKS:
        d.line([pt(*a), pt(*b)], fill=FG, width=round(max(TICK_W * k, 1)))

    bcx, bcy = 24, 24
    bolt = [pt(bcx + (x - bcx) * BOLT_SCALE, bcy + (y - bcy) * BOLT_SCALE)
            for (x, y) in BOLT]
    d.polygon(bolt, fill=FG)

    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(ASSETS, exist_ok=True)
    tiles = {}
    for size, frac, margin in SIZES:
        tile = draw_tile(size, frac, margin)
        tiles[size] = tile
        out = os.path.join(ASSETS, "icon_%d.png" % size)
        tile.save(out)
        print("wrote", out)

    ico = os.path.join(HERE, "openmbb.ico")
    ordered = [tiles[s] for s, _, _ in SIZES]
    ordered[0].save(ico, format="ICO", append_images=ordered[1:],
                    sizes=[(s, s) for s, _, _ in SIZES])
    print("wrote", ico)


if __name__ == "__main__":
    main()
