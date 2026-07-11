"""Themed, centered modal dialogs.

Drop-in replacements for the ``tkinter.messagebox`` functions — same names and
call signatures (``showinfo`` / ``showwarning`` / ``showerror`` / ``askokcancel``
/ ``askyesno``) — so existing call sites don't change, but each dialog is
dark-themed to match the app and centered on the main window instead of an
OS-grey box floating at screen centre.

The GUI imports this as::

    from . import dialogs as messagebox

Tests drive the app headlessly by monkeypatching these functions (via
``from openmbb import dialogs as mb; monkeypatch.setattr(mb, ...)``), so the real
modal below only runs in production. The building/centering path is exercised by
a dedicated test that calls ``_dialog`` directly and auto-dismisses it.
"""

import tkinter as tk
from tkinter import ttk

from . import theme


def _spec(kind):
    """(glyph, accent colour) for each dialog kind."""
    P = theme.PALETTE
    return {
        "info":  ("ℹ", theme.ACCENT_BLUE),   # information i
        "warn":  ("⚠", P["warn"]),           # warning triangle
        "error": ("✕", P["danger"]),         # heavy x
        "ask":   ("?", theme.ACCENT_BLUE),
    }.get(kind, ("ℹ", theme.ACCENT_BLUE))


def _dark_titlebar(win):
    """Match the app's dark title bar on Windows 10/11 (best-effort)."""
    try:
        from ctypes import windll, byref, sizeof, c_int
        win.update_idletasks()
        hwnd = windll.user32.GetParent(win.winfo_id())
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (20, older 19)
            if windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, byref(c_int(1)), sizeof(c_int)) == 0:
                break
    except Exception:
        pass


def _center(win, parent):
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    try:
        if parent is not None and parent.winfo_ismapped():
            x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
        else:
            x = (win.winfo_screenwidth() - w) // 2
            y = (win.winfo_screenheight() - h) // 3
    except Exception:
        x, y = 220, 220
    win.geometry("+%d+%d" % (max(x, 0), max(y, 0)))


def _dialog(title, message, kind, buttons, default=None):
    """Show a modal themed dialog centered on the app window.

    ``buttons`` is a list of ``(label, value, is_primary)``; returns the chosen
    value (or ``default`` if closed).
    """
    parent = tk._default_root
    glyph, accent = _spec(kind)
    ui = theme.UI_FAMILY
    try:
        surface = ttk.Style().lookup("TFrame", "background") or theme.PALETTE["bg"]
    except Exception:
        surface = theme.PALETTE["bg"]

    win = tk.Toplevel(parent)
    win.title(title or "OpenMBB")
    win.configure(bg=surface)
    win.resizable(False, False)
    if parent is not None:
        try:
            win.transient(parent)
        except Exception:
            pass

    result = {"value": default}

    def choose(v):
        result["value"] = v
        win.destroy()

    body = ttk.Frame(win, padding=(24, 22))
    body.pack(fill="both", expand=True)
    top = ttk.Frame(body)
    top.pack(fill="both", expand=True)
    ttk.Label(top, text=glyph, foreground=accent,
              font=(ui, 22, "bold")).pack(side="left", padx=(0, 16), anchor="n")
    ttk.Label(top, text=message or "", wraplength=400, justify="left",
              font=(ui, 11)).pack(side="left", fill="both", expand=True)

    row = ttk.Frame(body)
    row.pack(fill="x", pady=(22, 0))
    primary_btn = None
    for label, value, is_primary in reversed(buttons):   # primary ends rightmost
        b = ttk.Button(row, text=label,
                       style="Accent.TButton" if is_primary else "TButton",
                       command=lambda v=value: choose(v))
        b.pack(side="right", padx=(8, 0))
        if is_primary:
            primary_btn = b

    win.protocol("WM_DELETE_WINDOW", lambda: choose(default))
    win.bind("<Escape>", lambda e: choose(default))
    win.bind("<Return>", lambda e: choose(buttons[-1][1] if buttons else default))

    _dark_titlebar(win)
    _center(win, parent)
    try:
        win.grab_set()
    except Exception:
        pass
    if primary_btn is not None:
        primary_btn.focus_set()
    win.wait_window()
    return result["value"]


# ---- messagebox-compatible API ------------------------------------------
def showinfo(title=None, message=None, **kw):
    _dialog(title, message, "info", [("OK", "ok", True)], default="ok")
    return "ok"


def showwarning(title=None, message=None, **kw):
    _dialog(title, message, "warn", [("OK", "ok", True)], default="ok")
    return "ok"


def showerror(title=None, message=None, **kw):
    _dialog(title, message, "error", [("OK", "ok", True)], default="ok")
    return "ok"


def askokcancel(title=None, message=None, **kw):
    return bool(_dialog(title, message, "ask",
                        [("Cancel", False, False), ("OK", True, True)],
                        default=False))


def askyesno(title=None, message=None, **kw):
    return bool(_dialog(title, message, "ask",
                        [("No", False, False), ("Yes", True, True)],
                        default=False))
