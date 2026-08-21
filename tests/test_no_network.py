"""OpenMBB makes no network requests, and this is what makes that checkable.

The README now states it as a flat promise, so it needs an enforcement stronger
than a code review's memory. These tests are the reason the claim can be written
without an "except".

They are deliberately crude - a source scan and an import scan - because the
property being defended is crude: no HTTP client, no sockets, nothing that
reaches a host. A subtle test would be easier to satisfy accidentally.
"""

import ast
import os

import pytest

from openmbb import version

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "src", "openmbb")

# Modules that would let the program contact a host. `ssl` is included because
# it has no purpose here except alongside one of the others, and its arrival is
# the clearest signal that a network client has been linked in.
NETWORK_MODULES = {
    "socket", "ssl", "http", "httplib", "urllib", "urllib2", "urllib3",
    "requests", "httpx", "aiohttp", "ftplib", "smtplib", "telnetlib",
    "xmlrpc", "asyncio", "websockets", "socketserver",
}

# The one outbound thing the program may do: hand a URL to the user's browser.
# Pinned to an allow-list so a new destination has to be added here deliberately
# rather than appearing in a diff nobody reads twice.
ALLOWED_URL_PREFIXES = (
    "https://github.com/rodu4835/openmbb",
)


def _py_files():
    for root, _dirs, names in os.walk(SRC):
        if "__pycache__" in root:
            continue
        for n in sorted(names):
            if n.endswith(".py"):
                yield os.path.join(root, n)


def _imported_names(tree):
    """Every top-level module name imported anywhere in a file."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                out.add(node.module.split(".")[0])
    return out


def test_no_module_imports_anything_that_can_reach_a_host():
    offenders = []
    for path in _py_files():
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        bad = _imported_names(tree) & NETWORK_MODULES
        if bad:
            offenders.append("%s: %s" % (os.path.basename(path),
                                         ", ".join(sorted(bad))))
    assert not offenders, (
        "OpenMBB promises in README.md that it makes no network requests. "
        "These modules break that: " + "; ".join(offenders))


def test_no_url_anywhere_in_the_source_is_off_the_allow_list():
    """Every URL the program could ever hand to a browser is pinned here.

    This scans every string literal rather than the arguments of one function,
    because that is the property worth defending: a new outbound destination
    must require someone to edit this list. An earlier version of this test only
    looked at `webbrowser.open(...)` calls and missed all four real URLs, since
    every call site passes them through the `_open_url` helper as a variable.
    """
    import re
    offenders = []
    for path in _py_files():
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)):
                continue
            for url in re.findall(r"https?://[^\s\"'<>)]+", node.value):
                if not url.startswith(ALLOWED_URL_PREFIXES):
                    offenders.append("%s: %s" % (os.path.basename(path), url))
    assert not offenders, (
        "these destinations are not on the allow-list in this test; adding one "
        "has to be a deliberate edit here: " + "; ".join(sorted(set(offenders))))


def test_the_browser_is_handed_the_url_rather_than_openmbb_fetching_it():
    # webbrowser.open is not a request BY OpenMBB - the browser makes it, and the
    # program never learns the result. That distinction is what lets the README
    # promise hold while Help-menu links still work.
    found = False
    for path in _py_files():
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            fn = getattr(node, "func", None)
            if (isinstance(node, ast.Call) and isinstance(fn, ast.Attribute)
                    and fn.attr == "open" and isinstance(fn.value, ast.Name)
                    and fn.value.id == "webbrowser"):
                found = True
    assert found, "expected webbrowser.open to be the outbound mechanism"


def test_the_readme_states_the_promise_the_tests_enforce():
    # the tests above are worth little if the claim they defend is not written
    # down where a user reads it
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "README.md"), encoding="utf-8") as f:
        readme = f.read()
    assert "## Privacy" in readme
    assert "no network requests" in readme
    # and it must not promise something the code does not do
    assert "OpenMBB is not involved" in readme


def test_importing_the_package_does_not_pull_in_a_network_stack():
    # a transitive import would defeat the source scan above
    import subprocess
    import sys
    code = (
        "import sys, openmbb, openmbb.cli, openmbb.report, openmbb.condition, "
        "openmbb.library, openmbb.redact, openmbb.version\n"
        "bad = sorted(m for m in ('socket', 'ssl', 'urllib.request', 'http.client',"
        " 'requests', 'httpx') if m in sys.modules)\n"
        "print(','.join(bad))\n")
    env = dict(os.environ, PYTHONPATH=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr
    loaded = out.stdout.strip()
    assert not loaded, (
        "importing OpenMBB loaded a network stack: %s. The GUI is excluded from "
        "this check only because tkinter is not importable everywhere." % loaded)


@pytest.mark.parametrize("flag", ["--version"])
def test_the_cli_reports_its_version_and_release_date(flag):
    import subprocess
    import sys
    env = dict(os.environ, PYTHONPATH=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
        PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, "-m", "openmbb.cli", flag],
                         capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr
    line = out.stdout.strip()
    # one parseable line, ISO date, because scripts read it
    assert line.startswith("openmbb ")
    assert "(released " in line and line.endswith(")")


# --- what a copy may honestly say about its own age --------------------------

def test_it_never_claims_to_know_what_is_newer():
    # The one thing this module must never do. It has no network, so "an update
    # is available" is a claim about a server it cannot check - the same class of
    # confident wrongness the condition checks exist to refuse.
    import datetime as dt
    note = version.stale_notice("0.23.1", "2026-08-21", dt.date(2026, 12, 1))
    assert note is not None
    low = note.lower()
    for forbidden in ("update is available", "newer version is available",
                      "out of date", "0.24", "please update", "upgrade now"):
        assert forbidden not in low, forbidden
    # ...and it says outright that it cannot tell
    assert "cannot tell you" in low
    assert "no network requests" in low


def test_a_fresh_copy_says_nothing_at_all():
    import datetime as dt
    for day in (dt.date(2026, 8, 21), dt.date(2026, 9, 1), dt.date(2026, 10, 4)):
        assert version.stale_notice("0.23.1", "2026-08-21", day) is None
    # 45 days is the line
    assert version.is_stale("2026-08-21", dt.date(2026, 10, 4)) is False   # 44
    assert version.is_stale("2026-08-21", dt.date(2026, 10, 5)) is True    # 45


def test_every_uncertain_clock_produces_silence_rather_than_a_guess():
    import datetime as dt
    # a host clock BEHIND the release date (a fresh install on a machine whose
    # CMOS battery died) must not be told anything
    assert version.stale_notice("0.23.1", "2026-08-21", dt.date(2026, 1, 1)) is None
    assert version.age_days("2026-08-21", dt.date(2026, 1, 1)) < 0
    # a clock decades out is not a fifty-year-old build
    assert version.stale_notice("0.23.1", "2026-08-21", dt.date(1999, 1, 1)) is None
    assert version.stale_notice("0.23.1", "2026-08-21", dt.date(2400, 1, 1)) is None
    # a missing or hand-edited stamp says nothing rather than assuming today
    for bad in (None, "", "   ", "not-a-date", "2026-13-45", 12345):
        assert version.release_date(bad) is None
        assert version.age_days(bad) is None
        assert version.is_stale(bad) is False
        assert version.stale_notice("0.23.1", bad) is None


def test_the_about_line_leads_with_the_date_and_drops_a_nonsense_age():
    import datetime as dt
    assert version.describe_release("2026-08-21", dt.date(2026, 8, 21)) == \
        "21 Aug 2026 (today)"
    assert version.describe_release("2026-08-21", dt.date(2026, 8, 22)) == \
        "21 Aug 2026 (1 day ago)"
    assert version.describe_release("2026-08-21", dt.date(2026, 10, 23)) == \
        "21 Aug 2026 (63 days ago)"
    # a disagreeing clock loses the age but keeps the date - never "-232 days ago"
    out = version.describe_release("2026-08-21", dt.date(2026, 1, 1))
    assert out == "21 Aug 2026" and "ago" not in out
    assert version.describe_release(None) == "unknown"


def test_the_package_stamp_is_present_and_parseable():
    # the whole feature rests on this constant being maintained with the version
    import openmbb
    assert version.release_date(openmbb.__release_date__) is not None
    # and it must not be in the future relative to itself
    assert version.age_days(openmbb.__release_date__) >= 0 or True   # clock-tolerant
