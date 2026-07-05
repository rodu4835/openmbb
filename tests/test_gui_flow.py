"""End-to-end GUI flow in the simulator: connect -> baseline -> login -> write.

Skipped automatically where Tk has no display. Run with the real interpreter
on Windows; drives the app headlessly by pumping the event loop.
"""

import os
import time

import pytest

from zero_console.safety import WRITE_WHITELIST
from zero_console.transport import first_number


@pytest.fixture
def app(monkeypatch):
    tk = pytest.importorskip("tkinter")
    import tkinter.messagebox as mb
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(mb, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(mb, "showwarning", lambda *a, **k: None)
    errors = []
    monkeypatch.setattr(mb, "showerror", lambda *a, **k: errors.append(a))
    import tempfile
    from zero_console.gui import build_gui
    # hermetic: sessions go to a temp dir, never the user's configured location.
    # Build directly (no throwaway probe root) and skip cleanly with no display.
    try:
        application = build_gui(sim=True, log_dir=tempfile.mkdtemp(prefix="zcflow_"))
    except tk.TclError:
        pytest.skip("no display available for Tk")
    application._errors = errors
    yield application
    try:
        application.destroy()
    except Exception:
        pass


def _pump(app, cond, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        time.sleep(0.01)
        if cond():
            return True
    return False


def test_full_flow(app):
    app._connect()
    assert _pump(app, lambda: app.connected)

    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    assert len(app.settings) >= 30
    assert len(app.tree.get_children()) == len(WRITE_WHITELIST)

    app._login()
    assert _pump(app, lambda: app.logged_in)

    # write blocked without the unlock toggle
    app.tree.selection_set("spfront")
    app.newval_var.set("22")
    app._write()
    app.update()
    assert not app.journal_entries      # refused

    # unlock and write for real
    app.unlock_var.set(True)
    app._write()
    assert _pump(app, lambda: len(app.journal_entries) > 0)
    assert first_number(app.settings["spfront"]["value"]) == "22"
    backups = [f for f in os.listdir(app.logger.dir)
               if f.startswith("settings_backup")]
    assert backups
    assert not app._errors


def test_menu_dialogs_open(app):
    app._connect()
    assert _pump(app, lambda: app.connected)
    for opener in (app._show_instructions, app._show_wiring, app._show_safety,
                   app._show_about, app._show_bike_info):
        opener()
        app.update()
    # bike info should surface the firmware rev parsed from the version banner
    facts = dict(app._bike_facts())
    assert facts.get("MBB firmware rev") == "41"
    assert not app._errors
