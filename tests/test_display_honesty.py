"""The rule that a skip must mean what it says.

For six full runs on one machine with a display, the GUI skip count went
1, 0, 1, 2, 0, 2 — same tests, same hardware, nothing changed in between. The
cause was a fixture that caught every `TclError` out of `build_gui` and reported
it as "no display available for Tk". Surfacing the real error named the fault in
one run, and it was never the display:

    _tkinter.TclError: invalid command name "tcl_findLibrary"

raised by a bare `Tk()` after the GUI suite had built and destroyed the app a
hundred-odd times.

These tests hold the replacement to its promise. They matter more than they
look: everything else in this suite reports a failure when it finds one, and
this is the machinery that decides whether a test is allowed not to run at all.
"""

import pytest

from conftest import build_tk_or_fail, require_display


class _FakeTclError(Exception):
    pass


def test_a_display_that_exists_never_skips():
    """`tk_display` is True only when a bare Tk() succeeded, and that is the
    only thing allowed to excuse a GUI test from running.

    The skip is caught rather than simply not expected: letting it escape would
    skip THIS test, and a skipped test reports success. That is the same fault
    this file exists to close, one level up - the mutation runner found it here
    before anybody else did.
    """
    try:
        require_display(True)
    except pytest.skip.Exception as e:
        raise AssertionError(
            "require_display skipped on a machine WITH a display: %r" % e)


def test_no_display_skips_with_the_reason_it_was_given():
    with pytest.raises(pytest.skip.Exception) as excinfo:
        require_display("no display available for Tk (couldn't connect)")
    assert "couldn't connect" in str(excinfo.value)


def test_a_tclerror_with_a_display_present_fails_rather_than_skips(monkeypatch):
    """The whole point. A build that keeps failing on a machine WITH a display
    is a defect, and a defect reported as a skip is a green run that means less
    than it appears."""
    import tkinter as tk

    def always_broken():
        raise tk.TclError("invalid command name \"tcl_findLibrary\"")

    # The skip branch is caught EXPLICITLY. `pytest.raises(fail.Exception)`
    # does not match `Skipped`, so a regression to skipping would sail past the
    # raises() block, skip this test, and be scored as a pass - which is the
    # exact failure under test, wearing this test as a disguise.
    try:
        build_tk_or_fail(always_broken, "a deliberately broken build")
    except pytest.skip.Exception as e:
        raise AssertionError(
            "build_tk_or_fail SKIPPED where it must FAIL: %r" % e)
    except pytest.fail.Exception as e:
        assert "tcl_findLibrary" in str(e)
        assert "not a missing display" in str(e)
    else:
        raise AssertionError("a build that never succeeds must not return")


def test_one_retry_is_allowed_because_the_cause_is_known(capsys):
    """A previous test's Tk objects collected mid-build finalize the
    interpreter underneath the new one, and a forced collect clears it. One
    retry recovers that; it is not allowed to be silent."""
    import tkinter as tk

    calls = []

    def broken_once():
        calls.append(1)
        if len(calls) == 1:
            raise tk.TclError("invalid command name \"tcl_findLibrary\"")
        return "an application"

    assert build_tk_or_fail(broken_once, "a flaky build") == "an application"
    assert len(calls) == 2
    assert "needed a retry" in capsys.readouterr().out


def test_the_retry_is_not_unlimited():
    """Two failures is a defect, not a flake. A loop that retried until it
    worked would rebuild the original silence with extra steps."""
    import tkinter as tk

    calls = []

    def broken_twice_then_fine():
        calls.append(1)
        if len(calls) <= 2:
            raise tk.TclError("still broken")
        return "an application"

    try:
        build_tk_or_fail(broken_twice_then_fine, "a build broken twice")
    except pytest.skip.Exception as e:
        raise AssertionError("skipped where it must fail: %r" % e)
    except pytest.fail.Exception:
        pass
    else:
        raise AssertionError("two failures in a row must not be recovered from")
    assert len(calls) == 2, "it must stop after one retry, not keep going"
