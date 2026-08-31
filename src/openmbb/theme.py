"""Theming.

Preferred look is the Windows-11-native **Sun Valley** ttk theme (`sv-ttk`) — a
clean, modern, low-saturation dark theme — with a hand-rolled dark `clam` theme
as a zero-dependency fallback. The console panes are plain tk.Text widgets
coloured from PALETTE.

apply_theme() returns a dict of resolved ttk style names so the GUI can use the
right primary/danger/toggle styles for whichever backend is active. It also
registers a small set of restrained heading/label styles (calm accents, no neon)
used by the guided screens.
"""

# Colours for the tk (non-ttk) widgets: console panes, listbox, status cells.
# ttk widgets are themed by sv-ttk; these are the ones Tk makes us paint.
PALETTES = {
    # Tuned to sit on the Sun Valley dark surface (#1c1c1c) and read as an
    # instrument console.
    "dark": {
        "console": "#0d0f12",   # terminal panes (near black)
        "termfg":  "#8fe6ad",   # soft phosphor-green console text
        "fg":      "#e6e6e6",
        "dim":     "#9aa0aa",
        "green":   "#4ade80",
        "warn":    "#ffb454",
        "danger":  "#ff6b6b",
        "sel":     "#2f4f3a",
        "selfg":   "#eafff2",   # text inside a selection
        "panel":   "#2b2b2b",
        "border":  "#39394a",
        "chipfg":  "#0d0d0d",   # text on a solid green/amber chip
        "menubg":  "#1d1d26",   # dropdown surface
        "menuhov": "#33384a",   # dropdown hover row
        "menubd":  "#4a4470",   # dropdown 1px border
        "tooltip": "#4a4470",
        "carddim": "#aab2c5",   # secondary text inside the effect card
        "accent":  "#8fd0ff",   # a changed/attention row in a table
        "grid":    "#2a2d38",   # chart gridlines
        # fallback-only surfaces (clam)
        "bg":      "#15161a",
        "field":   "#2a2d38",
    },
    # Sits on the Sun Valley light surface (#fafafa). The console keeps its
    # green identity but inverted to a dark-on-white terminal — phosphor green
    # on white is unreadable, so it darkens rather than merely lightening.
    "light": {
        "console": "#ffffff",
        "termfg":  "#0f6d33",
        "fg":      "#1a1a1a",
        "dim":     "#5b6270",
        "green":   "#177245",
        "warn":    "#8a5200",
        "danger":  "#b3261e",
        "sel":     "#cfe8d8",
        "selfg":   "#0f2417",
        "panel":   "#eceff2",
        "border":  "#c8ccd4",
        "chipfg":  "#ffffff",
        "menubg":  "#ffffff",
        "menuhov": "#e4ecf7",
        "menubd":  "#c2c7d0",
        "tooltip": "#fbfbe6",   # the familiar pale-yellow tip surface
        "carddim": "#5b6270",
        "accent":  "#1a5fb4",
        "grid":    "#dfe3e9",
        "bg":      "#fafafa",
        "field":   "#ffffff",
    },
}

MODES = tuple(PALETTES)
DEFAULT_MODE = "dark"

# The ACTIVE palette. Mutated in place by set_palette() and never rebound —
# gui.py does `from .theme import PALETTE` and then `P = PALETTE` once at
# import, so rebinding this name would leave every one of those references
# pointing at the old colours.
PALETTE = dict(PALETTES[DEFAULT_MODE])
_mode = DEFAULT_MODE


def current_mode():
    return _mode


def set_palette(mode):
    """Swap the active palette in place. Returns the resolved mode."""
    global _mode
    _mode = mode if mode in PALETTES else DEFAULT_MODE
    PALETTE.clear()
    PALETTE.update(PALETTES[_mode])
    return _mode

# Restrained accent colours for headings/status text (calm, modern — no neon).
ACCENT_BLUE = "#5aa8ff"
ACCENT_GREEN = "#57c07b"

DANGER_RED = "#c42b1c"        # Win11 system "critical" red
DANGER_RED_ACTIVE = "#b02718"

# Resolved at apply_theme() time to families that exist on the running OS.
UI_FAMILY = "TkDefaultFont"
MONO_FAMILY = "TkFixedFont"

# Preference order: native Windows first, then common Linux/macOS families,
# then a Tk named font that always resolves.
_UI_PREFS = ["Segoe UI", "DejaVu Sans", "Noto Sans", "Cantarell", "Ubuntu",
             "Helvetica Neue", "Arial"]
_MONO_PREFS = ["Consolas", "DejaVu Sans Mono", "Noto Sans Mono",
               "Liberation Mono", "Ubuntu Mono", "Menlo", "Courier New"]


def _families(root):
    try:
        from tkinter import font
        return set(font.families(root))
    except Exception:
        return set()


def _pick(avail, prefs, default):
    for p in prefs:
        if p in avail:
            return p
    return default


def apply_theme(root, mode=DEFAULT_MODE):
    """Apply the best available theme; return resolved style names + fonts.

    Cross-platform: picks UI/monospace font families that actually exist on the
    running OS, and falls back to a hand-rolled clam theme if sv-ttk is
    unavailable. Safe to call again to switch mode — sv-ttk restyles every ttk
    widget in place, and set_palette() updates the colours the tk widgets read.
    """
    from tkinter import ttk
    global UI_FAMILY, MONO_FAMILY
    mode = set_palette(mode)
    avail = _families(root)
    UI_FAMILY = _pick(avail, _UI_PREFS, "TkDefaultFont")
    MONO_FAMILY = _pick(avail, _MONO_PREFS, "TkFixedFont")
    ui = UI_FAMILY
    try:
        import sv_ttk
        sv_ttk.set_theme(mode)
        style = ttk.Style(root)
        # Native accent (blue) and the toggle switch already exist; add a red
        # danger variant.
        style.configure("Danger.TButton", background=DANGER_RED,
                        foreground="#ffffff")
        style.map("Danger.TButton",
                  background=[("active", DANGER_RED_ACTIVE),
                              ("pressed", DANGER_RED_ACTIVE)])
        style.configure("Treeview", rowheight=28)
        # Flatter tab bar to match the flat landing surface (owner: make the tab
        # pages match the first page's styling).
        style.configure("TNotebook", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8))
        out = {"backend": "sv-ttk", "accent": "Accent.TButton",
               "danger": "Danger.TButton", "toggle": "Switch.TCheckbutton"}
    except Exception:
        _apply_clam(root, ui)
        out = {"backend": "clam", "accent": "Accent.TButton",
               "danger": "Danger.TButton", "toggle": "Unlock.TCheckbutton"}
    _accent_labels(root, ui)
    out["ui"] = UI_FAMILY
    out["mono"] = MONO_FAMILY
    out["mode"] = mode
    return out


def _accent_labels(root, ui):
    """Restrained, modern heading/label styles used by the guided screens.

    Calm accents (no neon) that read well on the dark surface; theme-agnostic
    (labels are not image-based, so foreground/font configure on both backends).
    """
    from tkinter import ttk
    style = ttk.Style(root)
    dim = PALETTE["dim"]
    # Accents are palette-driven so they stay legible on a light surface — the
    # dark-mode blue/green wash out on white.
    accent = ACCENT_BLUE if current_mode() == "dark" else "#0a5ca8"
    good = ACCENT_GREEN if current_mode() == "dark" else PALETTE["green"]
    style.configure("Title.TLabel", font=(ui, 26, "bold"))
    style.configure("Heading.TLabel", font=(ui, 15, "bold"))
    style.configure("Subtitle.TLabel", font=(ui, 12), foreground=dim)
    style.configure("Muted.TLabel", foreground=dim)
    style.configure("Accent.TLabel", font=(ui, 12, "bold"), foreground=accent)
    style.configure("Good.TLabel", font=(ui, 12, "bold"), foreground=good)
    # Good.TLabel had no counterpart, so anything wanting to say "this is not
    # right" either shouted in Accent (which reads as a heading) or fell back to
    # plain body text with no style at all - an unconfigured ttk style name is
    # not an error, it just silently looks normal.
    style.configure("Warn.TLabel", foreground=PALETTE["warn"])


def _apply_clam(root, ui):
    from tkinter import ttk
    P = PALETTE
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(bg=P["bg"])
    style.configure(".", background=P["bg"], foreground=P["fg"],
                    font=(ui, 10), fieldbackground=P["field"],
                    troughcolor=P["panel"], bordercolor=P["panel"],
                    lightcolor=P["panel"], darkcolor=P["bg"],
                    focuscolor=P["green"])
    style.configure("TFrame", background=P["bg"])
    style.configure("TLabel", background=P["bg"], foreground=P["fg"])
    style.configure("TButton", background=P["panel"], foreground=P["fg"],
                    padding=(12, 6), borderwidth=0)
    style.map("TButton",
              background=[("disabled", P["bg"]), ("pressed", P["field"]),
                          ("active", "#2e323f")],
              foreground=[("disabled", "#555a68")])
    style.configure("Accent.TButton", background="#1f5c38", foreground="#eafff2")
    style.map("Accent.TButton",
              background=[("pressed", "#194b2e"), ("active", "#277147")])
    style.configure("Danger.TButton", background=DANGER_RED, foreground="#ffe9ec")
    style.map("Danger.TButton",
              background=[("pressed", DANGER_RED_ACTIVE),
                          ("active", "#8a2e3c")])
    style.configure("TNotebook", background=P["bg"], borderwidth=0,
                    tabmargins=(8, 8, 8, 0))
    style.configure("TNotebook.Tab", background=P["panel"], foreground=P["dim"],
                    padding=(16, 8), font=(ui, 10, "bold"))
    style.map("TNotebook.Tab",
              background=[("selected", P["field"])],
              foreground=[("selected", P["green"]), ("disabled", "#3d414d")])
    style.configure("Treeview", background=P["field"], fieldbackground=P["field"],
                    foreground=P["fg"], rowheight=26, borderwidth=0)
    style.configure("Treeview.Heading", background=P["panel"], foreground=P["dim"],
                    font=(ui, 9, "bold"), borderwidth=0)
    style.map("Treeview.Heading", background=[("active", P["panel"])])
    style.map("Treeview", background=[("selected", P["sel"])],
              foreground=[("selected", "#eafff2")])
    style.configure("TEntry", fieldbackground=P["field"], foreground=P["fg"],
                    insertcolor=P["fg"], padding=4)
    style.configure("TCombobox", fieldbackground=P["field"], background=P["panel"],
                    foreground=P["fg"], arrowcolor=P["fg"], padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", P["field"])],
              foreground=[("disabled", P["dim"])])
    root.option_add("*TCombobox*Listbox.background", P["field"])
    root.option_add("*TCombobox*Listbox.foreground", P["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", P["sel"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#eafff2")
    style.configure("TCheckbutton", background=P["bg"], foreground=P["fg"])
    style.map("TCheckbutton", background=[("active", P["bg"])],
              indicatorcolor=[("selected", P["green"]), ("!selected", P["field"])])
    style.configure("Unlock.TCheckbutton", foreground=P["warn"],
                    font=(ui, 10, "bold"))
    style.configure("Horizontal.TProgressbar", background=P["green"],
                    troughcolor=P["panel"], borderwidth=0,
                    lightcolor=P["green"], darkcolor=P["green"])
