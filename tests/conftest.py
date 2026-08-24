"""Shared fixtures — chiefly, one honest answer to "is there a display?".

The GUI tests used to infer that from whatever `build_gui` happened to raise:
any `TclError` at all was caught and reported as "no display available for Tk".
That conflates a question with a symptom. `build_gui` builds a whole
application — a Tk root, a themed style, a notebook, several hundred widgets,
timers — and a `TclError` from any of it read as proof that the machine had no
display.

The consequence was measured before it was fixed: across six full runs on one
machine that plainly has a display, the skip count went 1, 0, 1, 2, 0, 2 — same
tests, same hardware, no changes in between. A floating subset of GUI coverage
silently did not run, and a run that skipped a test reported success just as
loudly as one that executed it. Green that means less than it appears is the one
thing this project will not have.

Surfacing the real error named the actual fault at once, and it was never the
display:

    _tkinter.TclError: invalid command name "tcl_findLibrary"

raised by a *bare* `Tk()` in test_theme's fixture — not by a machine with
nowhere to draw.

What it actually is was established later, and only because the retry was made
to announce itself: the first failure carries

    Can't find a usable init.tcl in the following directories: {...tcl8.6}
    couldn't read file ".../init.tcl": No error

a transient failure to READ init.tcl from disk. `tcl_findLibrary` is defined in
that file, so "invalid command name" is the downstream symptom of the same
thing rather than a separate fault. It is intermittent (forty build/destroy
cycles in isolation do not reproduce it), which is exactly why it hid: an
intermittent skip looks like a quiet machine.

The retry recovers it, which is what a transient read failure predicts and what
an interpreter finalized for good would not.
"""

import gc
import warnings

import pytest

#: Skips whose reason mentions the display, recorded so the session can check
#: that none happened on a machine that has one.
_display_skips = []


@pytest.fixture(scope="session")
def tk_display():
    """True when a Tk root can be created at all; a reason string when not.

    A bare `Tk()` is the whole test. It touches no application code, so it
    cannot fail for any reason except the one being asked about — which is what
    makes it usable as evidence, and what `build_gui` never was.
    """
    tk = pytest.importorskip("tkinter")
    # Asked once per session, so ONE unlucky attempt condemns every GUI test in
    # the run - which is exactly what happened before this retried: 439 passed,
    # 169 skipped, exit 0. The fault it trips over is transient (see the module
    # docstring), so a genuinely display-less machine still fails all three
    # attempts while a hiccup costs nothing.
    last = None
    for _attempt in range(3):
        try:
            root = tk.Tk()
        except tk.TclError as e:
            last = e
            gc.collect()
            continue
        try:
            root.destroy()
        except tk.TclError:
            pass
        return True
    return "no display available for Tk (%s)" % last


def require_display(tk_display):
    """Skip the calling test only when there is genuinely nowhere to draw."""
    if tk_display is not True:
        pytest.skip(str(tk_display))


def build_tk_or_fail(build, what="the Tk object"):
    """Build something Tk-based, or fail with the real error — never skip.

    A display exists by the time this runs (`tk_display` established it once,
    with a bare `Tk()`), so a `TclError` here is NOT about the display and must
    not be reported as though it were.

    The single retry is for one observed, transient fault: Tk failing to read
    its own init.tcl (see the module docstring). A collect is kept alongside it
    because it is free and the interpreter-state theory is not disproved for
    every case — but the cause this recovers from is the read. The retry announces
    itself, because a retry nobody hears about is the same silence in a
    different coat — and if it fails twice the test fails carrying both errors,
    which is the diagnosable outcome the old skip destroyed.
    """
    import tkinter as tk

    before = getattr(tk, "_default_root", None)
    try:
        return build()
    except tk.TclError as first:
        # A build that died PARTWAY may have created its root before raising,
        # and nothing destroys it. tkinter pins the first root created as
        # `_default_root` — which dialogs.py adopts as the parent for every
        # real message box — so the corpse of a failed build would go on
        # parenting dialogs for the rest of the session.
        orphan = getattr(tk, "_default_root", None)
        if orphan is not None and orphan is not before:
            try:
                orphan.destroy()          # Tk.destroy clears _default_root
            except tk.TclError:
                pass
            if getattr(tk, "_default_root", None) is None and before is not None:
                tk._default_root = before
        gc.collect()
        try:
            built = build()
        except tk.TclError as second:
            pytest.fail(
                "%s raised TclError on a machine that HAS a display, twice. "
                "This is a real failure, not a missing display.\n"
                "  first: %r\n  retry: %r" % (what, first, second))
        # A warning, not a print: this runs inside a test that goes on to PASS,
        # and pytest captures stdout from a passing test — so the
        # announcement the retry promised was being made to nobody. Warnings are
        # summarised even on a green run, which is the whole point of saying it.
        warnings.warn(
            "%s needed a retry after %r" % (what, first), stacklevel=2)
        return built


def _mark_run_failed(terminalreporter):
    """Turn the run red from the terminal summary.

    `session.exitstatus` is what pytest returns, and this hook runs late enough
    that setting it here is the last word.
    """
    session = getattr(terminalreporter, "_session", None)
    if session is not None:
        session.exitstatus = 1


def pytest_runtest_logreport(report):
    """Record any skip that blames the display, wherever it came from."""
    if report.skipped:
        reason = ""
        if isinstance(report.longrepr, tuple) and len(report.longrepr) == 3:
            reason = str(report.longrepr[2])
        if "display" in reason.lower():
            _display_skips.append((report.nodeid, reason))


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """A display-reason skip on a machine WITH a display is the old bug back.

    It cannot happen through the fixtures here — they ask once and route every
    caller through the same answer — so this exists to catch a raw
    `pytest.skip("no display...")` being reintroduced somewhere new, which is
    how the original lie was written in the first place.
    """
    if not _display_skips:
        return
    try:
        import tkinter as tk
        root = tk.Tk()
        root.destroy()
    except Exception as e:                       # noqa: BLE001
        if "display" in str(e).lower():
            return      # genuinely no display: those skips are honest
        # Anything else and the probe itself hit the intermittent fault this
        # fixture exists to chase, so it cannot say whether the skips above were
        # honest. Uncertainty is reported rather than swallowed: a silent guard
        # is indistinguishable from a guard that found nothing wrong.
        terminalreporter.write_sep(
            "=", "could not re-check the display", yellow=True)
        terminalreporter.write_line(
            "  %d skip(s) blamed the display and this probe failed with %r, so "
            "whether they were honest is UNKNOWN." % (len(_display_skips), e))
        return
    terminalreporter.write_sep(
        "=", "display-reason skips on a machine WITH a display", red=True)
    for nodeid, reason in _display_skips:
        terminalreporter.write_line("  %s — %s" % (nodeid, reason))
    terminalreporter.write_line(
        "These read as passes and are not. See tests/conftest.py.")
    # And they fail the run. Without this the guard is a comment: a suite that
    # skipped 169 GUI tests exited 0 while printing this very block, which is
    # the outcome this whole item exists to make impossible.
    _mark_run_failed(terminalreporter)
