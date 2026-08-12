"""Light/dark appearance, and dialogs centring on the app window.

The palette tests are pure. The Tk ones build real widgets and skip cleanly
where there is no display, matching test_gui_flow's convention.
"""

import tempfile
from pathlib import Path

import pytest

from openmbb import theme


# ------------------------------------------------------------------ palettes

def test_both_palettes_define_the_same_keys():
    """A key present in one mode and missing in the other is an AttributeError
    waiting for whoever switches theme — catch it here, not on screen."""
    dark, light = theme.PALETTES["dark"], theme.PALETTES["light"]
    assert set(dark) == set(light)


def test_every_palette_value_is_a_colour():
    for mode, palette in theme.PALETTES.items():
        for key, value in palette.items():
            assert value.startswith("#") and len(value) in (4, 7), (mode, key, value)


def test_set_palette_mutates_in_place_and_never_rebinds():
    """gui.py does `from .theme import PALETTE` then `P = PALETTE` once, so a
    rebind here would leave every one of those references on the old colours.
    This is the load-bearing property of the whole switch."""
    captured = theme.PALETTE           # what gui.py holds
    try:
        theme.set_palette("light")
        assert captured is theme.PALETTE
        assert captured["bg"] == theme.PALETTES["light"]["bg"]
        theme.set_palette("dark")
        assert captured is theme.PALETTE
        assert captured["bg"] == theme.PALETTES["dark"]["bg"]
    finally:
        theme.set_palette("dark")


def test_unknown_mode_falls_back_to_dark():
    try:
        assert theme.set_palette("chartreuse") == "dark"
        assert theme.current_mode() == "dark"
    finally:
        theme.set_palette("dark")


def test_light_console_is_readable_dark_on_light():
    """Phosphor green on white is unreadable, so light mode must invert the
    console rather than merely lighten it."""
    light = theme.PALETTES["light"]
    assert light["console"] == "#ffffff"
    assert _luminance(light["termfg"]) < _luminance(light["console"])
    dark = theme.PALETTES["dark"]
    assert _luminance(dark["termfg"]) > _luminance(dark["console"])


def _luminance(hex_colour):
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# -------------------------------------------------------------------- config

def test_theme_preference_round_trips(monkeypatch):
    from openmbb import config as cfg
    tmp = Path(tempfile.mkdtemp(prefix="mbbtheme_")) / "config.json"
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp.parent)
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp)
    assert cfg.get_theme() == "dark"          # default is the original look
    cfg.set_theme("light")
    assert cfg.get_theme() == "light"
    cfg.set_theme("nonsense")
    assert cfg.get_theme() == "dark"          # anything unknown means dark


# ------------------------------------------------------------------- centring

@pytest.fixture
def root():
    tk = pytest.importorskip("tkinter")
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for Tk")
    r.geometry("1000x700+120+80")
    r.update_idletasks()
    yield r
    r.destroy()


def test_dialog_centres_on_the_parent_on_both_axes(root):
    import tkinter as tk

    from openmbb import dialogs
    win = tk.Toplevel(root)
    win.geometry("400x300")
    dialogs._center(win, root)
    win.update_idletasks()
    x, y = _position(win)
    assert x == root.winfo_rootx() + (root.winfo_width() - 400) // 2
    # vertical too — this used to be //3, which read as "not centred"
    assert y == root.winfo_rooty() + (root.winfo_height() - 300) // 2
    win.destroy()


def test_size_falls_back_to_the_requested_size_when_unmapped(root):
    """An unmapped window with no explicit geometry reports 1x1; centring on
    that would put its top-left where its centre belongs."""
    import tkinter as tk
    from tkinter import ttk

    from openmbb import dialogs
    win = tk.Toplevel(root)
    ttk.Label(win, text="x" * 40).pack()
    w, h = dialogs._size(win)
    assert w > 1 and h > 1
    win.destroy()


def test_centring_clamps_to_the_screen(root):
    """A parent near an edge must not push the dialog off it."""
    import tkinter as tk

    from openmbb import dialogs
    root.geometry("%dx%d+0+0" % (root.winfo_screenwidth(), 200))
    root.update_idletasks()
    win = tk.Toplevel(root)
    win.geometry("500x400")
    dialogs._center(win, root)
    win.update_idletasks()
    x, y = _position(win)
    assert x >= 0 and y >= 0
    assert x + 500 <= root.winfo_screenwidth() + 1
    win.destroy()


def _position(win):
    geo = win.geometry()                       # "WxH+X+Y"
    _, _, x, y = geo.replace("x", "+").split("+")[:2] + geo.split("+")[1:3]
    return int(x), int(y)
