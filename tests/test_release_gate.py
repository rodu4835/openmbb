"""Release-gate guards that run with the normal suite.

PII gate (B3): the vendored fixtures are redacted, but a future paste of a real
console capture into ANY tracked file — a doc, a new fixture, a test — would leak
the owner's VIN / serials. This walks every tracked TEXT file (and the tracked
file PATHS) and fails if a token matching a real VIN or serial SHAPE appears. The
redaction placeholders are shape-safe, so they never trip it.

The shapes live in tests/_pii_shapes.py so this gate and the fixture redaction
guards (test_rev41_fixture.py) share one definition (review PII-HYG-2).
"""

import os
import subprocess

from _pii_shapes import find_pii_shapes

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# tried in order; UTF-16 matters because Windows PowerShell 5.1 `>` redirection
# writes UTF-16LE (BOM + interleaved NUL) which would otherwise look "binary" and
# skip the scan — exactly how a real capture could get committed unseen on this
# machine (review PII-GATE-2).
_TEXT_ENCODINGS = ("utf-8-sig", "utf-16")


def _tracked_files():
    out = subprocess.check_output(["git", "ls-files"], cwd=REPO_ROOT, text=True)
    return [r.strip() for r in out.split("\n") if r.strip()]


def _decode_text(raw):
    """Decode `raw` to text (BOM-aware, UTF-8 then UTF-16), or None if it decodes
    under neither (a genuine binary)."""
    for enc in _TEXT_ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return None


def test_no_real_vin_or_serial_shapes_in_tracked_files():
    offenders = []
    for rel in _tracked_files():
        # the PATH itself must be clean too — a VIN-bearing filename would slip
        # past a contents-only scan (review PII-GATE-1)
        for label, _tok in find_pii_shapes(rel):
            offenders.append("%s (in the file PATH): a %s-shape token" % (rel, label))
        try:
            with open(os.path.join(REPO_ROOT, rel), "rb") as f:
                raw = f.read()
        except OSError:
            continue
        text = _decode_text(raw)
        if text is None:
            continue                         # genuine binary — nothing to scan
        for label, _tok in find_pii_shapes(text):
            offenders.append("%s: a %s-shape token" % (rel, label))
    assert not offenders, ("PII-shape tokens found in tracked files (redact them):\n"
                           + "\n".join(sorted(set(offenders))))


def test_gate_only_skips_genuine_binaries():
    # review PII-GATE-2: a skipped tracked file must be a real binary (image/exe),
    # never a text file the PII scan silently missed. If a new tracked text type
    # fails to decode, this fails loudly so the blind spot is visible.
    _BINARY_EXT = (".png", ".ico", ".gif", ".jpg", ".jpeg", ".exe", ".dll",
                   ".pyc", ".zip", ".gz", ".pdf", ".woff", ".woff2", ".ttf")
    skipped_nonbinary = []
    for rel in _tracked_files():
        try:
            with open(os.path.join(REPO_ROOT, rel), "rb") as f:
                raw = f.read()
        except OSError:
            continue
        if _decode_text(raw) is None and not rel.lower().endswith(_BINARY_EXT):
            skipped_nonbinary.append(rel)
    assert not skipped_nonbinary, ("tracked non-binary files the PII scan skipped "
                                   "(undecodable): " + ", ".join(skipped_nonbinary))
