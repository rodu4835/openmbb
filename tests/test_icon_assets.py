"""Icon assets: the runtime PNGs must ship inside the package and the .ico
must carry every size Windows pulls from (taskbar 16 up to Explorer 256).
Deliberately dependency-free (no Pillow) — parses headers by hand."""

import struct
from importlib.resources import files
from pathlib import Path

ICON_SIZES = (16, 32, 48, 64, 256)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
REPO = Path(__file__).resolve().parent.parent


def test_window_icon_pngs_ship_with_package():
    assets = files("openmbb") / "assets"
    for size in ICON_SIZES:
        data = (assets / ("icon_%d.png" % size)).read_bytes()
        assert data[:8] == PNG_MAGIC, "icon_%d.png is not a PNG" % size
        # IHDR width/height live at fixed offsets in the first chunk.
        w, h = struct.unpack(">II", data[16:24])
        assert (w, h) == (size, size)


def test_ico_contains_all_frames():
    data = (REPO / "packaging" / "icon" / "openmbb.ico").read_bytes()
    count = struct.unpack("<H", data[4:6])[0]
    assert count == len(ICON_SIZES)
    sizes = set()
    for i in range(count):
        entry = data[6 + i * 16: 6 + (i + 1) * 16]
        w, h = entry[0], entry[1]
        # ICO encodes 256 as 0.
        sizes.add((w or 256, h or 256))
    assert sizes == {(s, s) for s in ICON_SIZES}
