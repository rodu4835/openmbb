"""Regenerate the OpenMBB app icon (vaporwave: a retro scanline sun and a
synthwave perspective grid on a sunset gradient, with a neon lightning bolt —
the app's energy / electric-console mark).

    python packaging/icon/generate_icon.py        # requires pillow (dev-only)

Outputs, all committed to the repo so normal builds never need Pillow:
  packaging/icon/openmbb.ico        multi-size .ico for the .exe / installer
  src/openmbb/assets/icon_*.png     runtime window icons (Tk iconphoto)

Each size is rendered at 4x supersample then downscaled (LANCZOS) for clean
anti-aliasing. The perspective grid is dropped below 48 px (it becomes noise at
small sizes); the sun + bolt still read at 16 px.
"""

import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.abspath(os.path.join(HERE, "..", "..", "src", "openmbb", "assets"))

# sunset gradient (top -> bottom), retro-sun gradient, neon grid colour
BG = [(0.0, (38, 15, 82)), (0.46, (255, 46, 151)),
      (0.70, (255, 118, 120)), (1.0, (255, 183, 104))]
SUN = [(0.0, (255, 239, 150)), (0.5, (255, 150, 120)), (1.0, (255, 62, 150))]
GRID = (58, 232, 255)


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def grad(t, stops):
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if t <= t1:
            f = 0 if t1 == t0 else (t - t0) / (t1 - t0)
            return lerp(c0, c1, f)
    return stops[-1][1]


def render(S, grid=True):
    R = S * 4
    img = Image.new("RGBA", (R, R), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(R):                       # vertical sunset gradient
        d.line([(0, y), (R, y)], fill=grad(y / (R - 1), BG) + (255,))

    horizon = int(R * 0.66)
    cx, cy, rad = R // 2, int(R * 0.40), int(R * 0.26)

    # retro sun on its own layer (so scanline cuts reveal the gradient behind)
    sun = Image.new("RGBA", (R, R), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sun)
    for y in range(cy - rad, cy + rad + 1):
        dy = y - cy
        hw = int((rad * rad - dy * dy) ** 0.5)
        t = (y - (cy - rad)) / (2 * rad)
        sd.line([(cx - hw, y), (cx + hw, y)], fill=grad(t, SUN) + (255,))
    yy, gap, step = cy - int(rad * 0.05), max(1, int(R * 0.012)), int(R * 0.030)
    while yy < cy + rad:                      # widening scanline cuts (lower half)
        sd.rectangle([cx - rad - 2, yy, cx + rad + 2, yy + gap], fill=(0, 0, 0, 0))
        step = int(step * 1.18)
        gap = int(gap * 1.18)
        yy += step
    img.alpha_composite(sun)

    if grid:                                  # synthwave perspective grid
        g = Image.new("RGBA", (R, R), (0, 0, 0, 0))
        gd = ImageDraw.Draw(g)
        N = 7
        for i in range(1, N + 1):
            y = horizon + int((R - horizon) * (i / N) ** 1.7)
            gd.line([(0, y), (R, y)], fill=GRID + (170,), width=max(1, int(R * 0.006)))
        span = int(R * 0.95)
        for i in range(-6, 7):
            xb = cx + i * (span // 6)
            gd.line([(xb, R), (cx, horizon)], fill=GRID + (135,),
                    width=max(1, int(R * 0.005)))
        m = Image.new("L", (R, R), 0)
        ImageDraw.Draw(m).rectangle([0, horizon, R, R], fill=255)
        g.putalpha(Image.composite(g.getchannel("A"), Image.new("L", (R, R), 0), m))
        img.alpha_composite(g)

    # neon lightning bolt (energy / electric-console mark)
    pts_n = [(0.52, 0.02), (0.17, 0.55), (0.45, 0.55), (0.31, 0.98),
             (0.86, 0.42), (0.57, 0.42), (0.81, 0.02)]
    bw, bh = int(R * 0.36), int(R * 0.56)
    bx, by = cx - bw // 2, int(R * 0.22)
    pts = [(bx + p[0] * bw, by + p[1] * bh) for p in pts_n]
    glow = Image.new("RGBA", (R, R), (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon(pts, fill=(70, 240, 255, 255))
    glow = glow.filter(ImageFilter.GaussianBlur(R * 0.018))
    img.alpha_composite(glow)
    img.alpha_composite(glow)
    d.polygon(pts, fill=(150, 246, 255, 255))
    d.line(pts + [pts[0]], fill=(236, 255, 255, 235),
           width=max(1, int(R * 0.006)), joint="curve")

    # rounded-square mask
    m = Image.new("L", (R, R), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, R - 1, R - 1],
                                        radius=int(R * 0.22), fill=255)
    img.putalpha(Image.composite(img.getchannel("A"), Image.new("L", (R, R), 0), m))
    return img.resize((S, S), Image.LANCZOS)


def main():
    os.makedirs(ASSETS, exist_ok=True)
    for s in (16, 32, 48, 64, 256):
        out = os.path.join(ASSETS, "icon_%d.png" % s)
        render(s, grid=(s >= 48)).save(out)
        print("wrote", out)
    ico = os.path.join(HERE, "openmbb.ico")
    base = render(256, grid=True)
    others = [render(s, grid=(s >= 48)) for s in (16, 32, 48, 64, 128)]
    base.save(ico, format="ICO", append_images=others)
    print("wrote", ico)


if __name__ == "__main__":
    main()
