"""Theming.

Default look (v0.13) is the **vaporwave** theme — full neon accents on a deep
violet base, built on ttk's `clam` so we have complete colour control (the
Windows-native Sun Valley theme is image-based and ignores background colours,
so it can't go neon). If clam is somehow unavailable we fall back to sv-ttk, then
a stock dark clam, so the app always launches. The console panes are plain
tk.Text widgets coloured from PALETTE.

apply_theme() returns a dict of resolved ttk style names so the GUI can use the
right primary/danger/toggle styles for whichever backend is active.
"""

# Colours for the tk (non-ttk) widgets: console panes, listbox. v0.13: tuned for
# the vaporwave surface — deep indigo-violet with neon accents.
PALETTE = {
    "console": "#0a0f2e",   # terminal panes (deep indigo-black)
    "termfg":  "#7cffc4",   # neon-mint console text
    "fg":      "#e8e6f0",
    "dim":     "#9a93b8",
    "green":   "#5cffb1",
    "warn":    "#fff59d",
    "danger":  "#ff5f8f",
    "sel":     "#4a2f8f",   # selection (violet)
    "panel":   "#1a1140",
    "bg":      "#120a24",
    "field":   "#241a4d",
    # named vaporwave accents (v0.13) — for neon headings, primary/danger actions
    "vw_pink":   "#ff5fd2",
    "vw_purple": "#b06bff",
    "vw_cyan":   "#22d3ee",
    "vw_mint":   "#5cffb1",
    "vw_yellow": "#fff59d",
    "vw_edge":   "#3a2d6e",
    "vw_ink":    "#e8e6f0",
    "vw_muted":  "#9a93b8",
    "vw_bg0":    "#120a24",
    "vw_bg1":    "#1a1140",
    "vw_bg2":    "#0a0f2e",
    "vw_field":  "#241a4d",
}

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


def apply_theme(root):
    """Apply the best available theme; return resolved style names + fonts.

    Cross-platform: picks UI/monospace font families that actually exist on the
    running OS, and falls back to a hand-rolled dark clam theme if sv-ttk is
    unavailable.
    """
    from tkinter import ttk
    global UI_FAMILY, MONO_FAMILY
    avail = _families(root)
    UI_FAMILY = _pick(avail, _UI_PREFS, "TkDefaultFont")
    MONO_FAMILY = _pick(avail, _MONO_PREFS, "TkFixedFont")
    ui = UI_FAMILY
    # v0.13: default to the 'full neon' vaporwave theme (clam-based, full colour
    # control). Fall back to sv-ttk, then stock clam, so we never fail to launch.
    try:
        _apply_vaporwave(root, ui)
        out = {"backend": "vaporwave", "accent": "Accent.TButton",
               "danger": "Danger.TButton", "toggle": "Unlock.TCheckbutton"}
    except Exception:
        try:
            import sv_ttk
            sv_ttk.set_theme("dark")
            style = ttk.Style(root)
            style.configure("Danger.TButton", background=DANGER_RED,
                            foreground="#ffffff")
            style.map("Danger.TButton",
                      background=[("active", DANGER_RED_ACTIVE),
                                  ("pressed", DANGER_RED_ACTIVE)])
            style.configure("Treeview", rowheight=28)
            out = {"backend": "sv-ttk", "accent": "Accent.TButton",
                   "danger": "Danger.TButton", "toggle": "Switch.TCheckbutton"}
        except Exception:
            _apply_clam(root, ui)
            out = {"backend": "clam", "accent": "Accent.TButton",
                   "danger": "Danger.TButton", "toggle": "Unlock.TCheckbutton"}
    out["ui"] = UI_FAMILY
    out["mono"] = MONO_FAMILY
    return out


def _apply_vaporwave(root, ui):
    """v0.13 'full neon' vaporwave theme, built on clam for full colour control.

    sv-ttk is image-based and ignores background colours, so it cannot go neon;
    clam honours every colour we set. Deep violet surfaces, neon cyan primary,
    hot-pink/red danger, mint success, violet selection.
    """
    from tkinter import ttk
    P = PALETTE
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(bg=P["vw_bg0"])
    edge, ink, muted = P["vw_edge"], P["vw_ink"], P["vw_muted"]
    cyan, pink, purple = P["vw_cyan"], P["vw_pink"], P["vw_purple"]
    mint, yellow = P["vw_mint"], P["vw_yellow"]

    style.configure(".", background=P["vw_bg0"], foreground=ink, font=(ui, 10),
                    fieldbackground=P["vw_field"], troughcolor=P["vw_bg1"],
                    bordercolor=edge, lightcolor=edge, darkcolor=P["vw_bg0"],
                    focuscolor=cyan)
    style.configure("TFrame", background=P["vw_bg0"])
    style.configure("Panel.TFrame", background=P["vw_bg1"])
    style.configure("TLabel", background=P["vw_bg0"], foreground=ink)
    style.configure("Dim.TLabel", background=P["vw_bg0"], foreground=muted)
    # neon headings (used by the landing/dashboard tiers)
    style.configure("Hero.TLabel", background=P["vw_bg0"], foreground=pink,
                    font=(ui, 30, "bold"))
    style.configure("Neon.TLabel", background=P["vw_bg0"], foreground=pink,
                    font=(ui, 15, "bold"))
    style.configure("NeonCyan.TLabel", background=P["vw_bg0"], foreground=cyan,
                    font=(ui, 12, "bold"))
    style.configure("Good.TLabel", background=P["vw_bg0"], foreground=mint,
                    font=(ui, 11, "bold"))

    style.configure("TButton", background=P["vw_field"], foreground=ink,
                    padding=(12, 6), borderwidth=1)
    style.map("TButton",
              background=[("disabled", P["vw_bg1"]), ("pressed", edge),
                          ("active", "#33256b")],
              foreground=[("disabled", "#5b5480")],
              bordercolor=[("active", cyan), ("focus", cyan)])
    # primary = neon cyan (the blue "Pull full database" action)
    style.configure("Accent.TButton", background=cyan, foreground="#05131a",
                    font=(ui, 10, "bold"), borderwidth=0, padding=(12, 6))
    style.map("Accent.TButton",
              background=[("pressed", "#0fb6d0"), ("active", "#5be3f5"),
                          ("disabled", "#26506a")],
              foreground=[("disabled", "#7fa6b5")])
    # danger = neon red-pink (writes / destructive)
    style.configure("Danger.TButton", background="#ff4d6d", foreground="#1a0410",
                    font=(ui, 10, "bold"), borderwidth=0, padding=(12, 6))
    style.map("Danger.TButton",
              background=[("pressed", "#e03356"), ("active", "#ff6f8a"),
                          ("disabled", "#5e2436")])

    # menubar (Menubutton) — the derived "Menubar.TMenubutton" inherits these
    style.configure("TMenubutton", background=P["vw_bg1"], foreground=cyan,
                    font=(ui, 10, "bold"), padding=(10, 4), borderwidth=0)
    style.map("TMenubutton", background=[("active", P["vw_field"])],
              foreground=[("active", pink)])

    style.configure("TNotebook", background=P["vw_bg0"], borderwidth=0,
                    tabmargins=(8, 8, 8, 0))
    style.configure("TNotebook.Tab", background=P["vw_bg1"], foreground=muted,
                    padding=(16, 8), font=(ui, 10, "bold"), borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", P["vw_field"])],
              foreground=[("selected", cyan), ("disabled", "#4a4470")])

    style.configure("Treeview", background=P["vw_field"],
                    fieldbackground=P["vw_field"], foreground=ink, rowheight=28,
                    borderwidth=0)
    style.configure("Treeview.Heading", background=P["vw_bg1"], foreground=cyan,
                    font=(ui, 9, "bold"), borderwidth=0)
    style.map("Treeview.Heading", background=[("active", P["vw_bg1"])])
    style.map("Treeview", background=[("selected", purple)],
              foreground=[("selected", "#0a0620")])

    style.configure("TEntry", fieldbackground=P["vw_field"], foreground=ink,
                    insertcolor=cyan, padding=4)
    style.configure("TCombobox", fieldbackground=P["vw_field"],
                    background=P["vw_bg1"], foreground=ink, arrowcolor=cyan,
                    padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", P["vw_field"])],
              foreground=[("disabled", muted)])
    root.option_add("*TCombobox*Listbox.background", P["vw_field"])
    root.option_add("*TCombobox*Listbox.foreground", ink)
    root.option_add("*TCombobox*Listbox.selectBackground", purple)
    root.option_add("*TCombobox*Listbox.selectForeground", "#0a0620")

    style.configure("TCheckbutton", background=P["vw_bg0"], foreground=ink)
    style.map("TCheckbutton", background=[("active", P["vw_bg0"])],
              indicatorcolor=[("selected", mint), ("!selected", P["vw_field"])])
    style.configure("Unlock.TCheckbutton", foreground=yellow, font=(ui, 10, "bold"),
                    background=P["vw_bg0"])
    style.map("Unlock.TCheckbutton", background=[("active", P["vw_bg0"])])

    style.configure("Horizontal.TProgressbar", background=cyan,
                    troughcolor=P["vw_bg1"], borderwidth=0, lightcolor=cyan,
                    darkcolor=cyan)


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
