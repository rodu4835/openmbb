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

raised by a *bare* `Tk()` in test_theme's fixture after test_gui_flow had built
and destroyed the application a hundred-odd times — the signature of a Tcl
interpreter that has been finalized, not of a machine with nowhere to draw. It
is intermittent (forty build/destroy cycles in isolation do not reproduce it),
which is exactly why it hid: an intermittent skip looks like a quiet machine.
"""

import gc

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
    try:
        root = tk.Tk()
    except tk.TclError as e:
        return "no display available for Tk (%s)" % e
    try:
        root.destroy()
    except tk.TclError:
        pass
    return True


def require_display(tk_display):
    """Skip the calling test only when there is genuinely nowhere to draw."""
    if tk_display is not True:
        pytest.skip(str(tk_display))


def build_tk_or_fail(build, what="the Tk object"):
    """Build something Tk-based, or fail with the real error — never skip.

    A display exists by the time this runs (`tk_display` established it once,
    with a bare `Tk()`), so a `TclError` here is NOT about the display and must
    not be reported as though it were.

    The single retry is for one known, named mechanism: a previous test's Tk
    variables being collected mid-build, which finalizes the interpreter
    underneath the new one. A forced collect clears that. The retry announces
    itself, because a retry nobody hears about is the same silence in a
    different coat — and if it fails twice the test fails carrying both errors,
    which is the diagnosable outcome the old skip destroyed.
    """
    import tkinter as tk

    try:
        return build()
    except tk.TclError as first:
        gc.collect()
        try:
            built = build()
        except tk.TclError as second:
            pytest.fail(
                "%s raised TclError on a machine that HAS a display, twice. "
                "This is a real failure, not a missing display.\n"
                "  first: %r\n  retry: %r" % (what, first, second))
        print("[warn] %s needed a retry after %r — a previous test's Tk objects "
              "were probably collected mid-build" % (what, first))
        return built


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
    except Exception:
        return          # genuinely no display: those skips are honest
    terminalreporter.write_sep(
        "=", "display-reason skips on a machine WITH a display", red=True)
    for nodeid, reason in _display_skips:
        terminalreporter.write_line("  %s — %s" % (nodeid, reason))
    terminalreporter.write_line(
        "These read as passes and are not. See tests/conftest.py.")
