"""Theming.

Preferred look is the Windows-11-native **Sun Valley** ttk theme (`sv-ttk`);
if it is unavailable we fall back to a hand-rolled dark `clam` theme so the app
still runs with zero extra dependencies. Either way the console panes are plain
tk.Text widgets coloured from PALETTE.

apply_theme() returns a dict of resolved ttk style names so the GUI can use the
right primary/danger/toggle styles for whichever backend is active.
"""

# Colours for the tk (non-ttk) widgets: console panes, listbox. Tuned to sit
# on the Sun Valley dark surface (#1c1c1c) and read as an instrument console.
PALETTE = {
    "console": "#0d0f12",   # terminal panes (near black)
    "termfg":  "#8fe6ad",   # phosphor green console text
    "fg":      "#e6e6e6",
    "dim":     "#9aa0aa",
    "green":   "#4ade80",
    "warn":    "#ffb454",
    "danger":  "#ff6b6b",
    "sel":     "#2f4f3a",
    "panel":   "#2b2b2b",
    # fallback-only surfaces (clam)
    "bg":      "#15161a",
    "field":   "#2a2d38",
}

DANGER_RED = "#c42b1c"        # Win11 system "critical" red
DANGER_RED_ACTIVE = "#b02718"


def apply_theme(root):
    """Apply the best available theme; return resolved style names."""
    from tkinter import ttk
    try:
        import sv_ttk
        sv_ttk.set_theme("dark")
        style = ttk.Style(root)
        # Native accent (blue) already exists as Accent.TButton and the toggle
        # switch as Switch.TCheckbutton. Add a red danger variant.
        style.configure("Danger.TButton", background=DANGER_RED,
                        foreground="#ffffff")
        style.map("Danger.TButton",
                  background=[("active", DANGER_RED_ACTIVE),
                              ("pressed", DANGER_RED_ACTIVE)])
        _tree_tags(style)
        return {"backend": "sv-ttk", "accent": "Accent.TButton",
                "danger": "Danger.TButton", "toggle": "Switch.TCheckbutton"}
    except Exception:
        _apply_clam(root)
        return {"backend": "clam", "accent": "Accent.TButton",
                "danger": "Danger.TButton", "toggle": "Unlock.TCheckbutton"}


def _tree_tags(style):
    style.configure("Treeview", rowheight=28)


def _apply_clam(root):
    from tkinter import ttk
    P = PALETTE
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(bg=P["bg"])
    style.configure(".", background=P["bg"], foreground=P["fg"],
                    font=("Segoe UI", 10), fieldbackground=P["field"],
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
                    padding=(16, 8), font=("Segoe UI", 10, "bold"))
    style.map("TNotebook.Tab",
              background=[("selected", P["field"])],
              foreground=[("selected", P["green"]), ("disabled", "#3d414d")])
    style.configure("Treeview", background=P["field"], fieldbackground=P["field"],
                    foreground=P["fg"], rowheight=26, borderwidth=0)
    style.configure("Treeview.Heading", background=P["panel"], foreground=P["dim"],
                    font=("Segoe UI", 9, "bold"), borderwidth=0)
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
                    font=("Segoe UI", 10, "bold"))
    style.configure("Horizontal.TProgressbar", background=P["green"],
                    troughcolor=P["panel"], borderwidth=0,
                    lightcolor=P["green"], darkcolor=P["green"])
