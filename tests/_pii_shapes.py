"""Canonical PII-shape detectors, shared by the release gate
(test_release_gate.py) and the fixture redaction guards (test_rev41_fixture.py)
so hardening the shapes lands in BOTH at once (review PII-HYG-2).

A real VIN is a 17-char run over the VIN charset (which excludes I/O/Q) that
mixes letters and digits (rules out a 17-digit odometer/timestamp run). We match
an all-UPPER or an all-lower token — a real VIN is single-case, and requiring one
case avoids flagging mixed-case 17-char code identifiers while still catching a
lowercase paste (review PII-GATE-1). Boundaries are explicit non-alphanumeric
lookarounds rather than \\b, because \\b treats '_' as a word char and would miss
underscore-delimited tokens like `log_<vin>_parsed.txt` (review PII-GATE-1).
"""

import re

_L = r"(?<![0-9A-Za-z])"          # left boundary: not preceded by alnum
_R = r"(?![0-9A-Za-z])"           # right boundary: not followed by alnum

# one all-upper and one all-lower VIN alternative, each requiring a letter+digit mix
_VIN_UPPER = (r"(?=[0-9A-HJ-NPR-Z]{17}" + _R + r")"
              r"(?=[A-HJ-NPR-Z]*[0-9])(?=[0-9]*[A-HJ-NPR-Z])[0-9A-HJ-NPR-Z]{17}")
_VIN_LOWER = (r"(?=[0-9a-hj-npr-z]{17}" + _R + r")"
              r"(?=[a-hj-npr-z]*[0-9])(?=[0-9]*[a-hj-npr-z])[0-9a-hj-npr-z]{17}")

VIN_SHAPE = re.compile(_L + "(?:" + _VIN_UPPER + "|" + _VIN_LOWER + ")")
MBB_SERIAL_SHAPE = re.compile(r"(?i)" + _L + r"sj\d{4}zer\d{4}" + _R)   # MBB/BMS serial
MODULE_SERIAL_SHAPE = re.compile(r"(?i)" + _L + r"17gb\d{4}" + _R)      # module serial
# Sevcon controller serial: a bare digit run has no distinctive shape, so anchor
# on its console LABEL ("Sevcon Serial num : <digits>") — catches a real capture
# while a lettered redaction placeholder (no digit run) passes clean.
SEVCON_SERIAL_SHAPE = re.compile(r"(?i)Sevcon Serial num\s*:?\s*(\d{6,})")

SHAPES = [("VIN", VIN_SHAPE), ("mbb-serial", MBB_SERIAL_SHAPE),
          ("module-serial", MODULE_SERIAL_SHAPE),
          ("sevcon-serial", SEVCON_SERIAL_SHAPE)]

# same-width redaction placeholders that legitimately appear in the fixtures
PLACEHOLDERS = {"REDACTEDVIN000000", "REDACTEDMBB00", "REDACTED17GB0"}


def find_pii_shapes(text):
    """Return [(label, token)] for every PII-shape token in `text` that is not a
    redaction placeholder. Empty list == clean."""
    hits = []
    for label, rx in SHAPES:
        for tok in rx.findall(text):
            if tok in PLACEHOLDERS:
                continue
            hits.append((label, tok))
    return hits
