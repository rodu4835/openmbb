"""Release-gate guards that run with the normal suite.

PII gate (B3): the vendored fixtures are redacted, but a future paste of a real
console capture into ANY tracked file — a doc, a new fixture, a test — would leak
the owner's VIN / serials. This walks every tracked TEXT file and fails if a token
matching a real VIN or serial SHAPE appears. The redaction placeholders are
shape-safe (the VIN placeholder contains an 'I', which the VIN charset excludes),
so they never trip it.
"""

import os
import re
import subprocess

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A real 17-char VIN is uppercase alnum over the VIN charset, which EXCLUDES I/O/Q
# and MUST mix letters and digits (rules out a 17-digit odometer/timestamp run).
_VIN_SHAPE = re.compile(r"\b(?=[0-9A-HJ-NPR-Z]{17}\b)(?=[A-HJ-NPR-Z]*[0-9])"
                        r"(?=[0-9]*[A-HJ-NPR-Z])[0-9A-HJ-NPR-Z]{17}\b")
_MBB_SERIAL_SHAPE = re.compile(r"(?i)\bsj\d{4}zer\d{4}\b")      # MBB/BMS serial
_MODULE_SERIAL_SHAPE = re.compile(r"(?i)\b17gb\d{4}\b")         # module serial
_SHAPES = [("VIN", _VIN_SHAPE), ("mbb-serial", _MBB_SERIAL_SHAPE),
           ("module-serial", _MODULE_SERIAL_SHAPE)]

# same-width redaction placeholders that legitimately appear in the fixtures
_ALLOW = {"REDACTEDVIN000000", "REDACTEDMBB00", "REDACTED17GB0"}


def _tracked_text_files():
    out = subprocess.check_output(["git", "ls-files"], cwd=REPO_ROOT, text=True)
    for rel in out.split("\n"):
        rel = rel.strip()
        if not rel:
            continue
        try:
            with open(os.path.join(REPO_ROOT, rel), "rb") as f:
                raw = f.read()
        except OSError:
            continue
        if b"\x00" in raw:              # binary — skip
            continue
        try:
            yield rel, raw.decode("utf-8")
        except UnicodeDecodeError:
            continue


def test_no_real_vin_or_serial_shapes_in_tracked_files():
    offenders = []
    for rel, text in _tracked_text_files():
        for label, rx in _SHAPES:
            for m in rx.findall(text):
                if m in _ALLOW:
                    continue
                offenders.append("%s: a %s-shape token" % (rel, label))
    assert not offenders, ("PII-shape tokens found in tracked files (redact them):\n"
                           + "\n".join(sorted(set(offenders))))
