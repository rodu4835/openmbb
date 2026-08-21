"""Make a capture safe to hand to somebody else.

A session folder is the most useful thing an owner can share — it is what lets a
second pair of eyes look at a pack, and it is the only way this project will ever
have more than one bike to calibrate against. It is also the most dangerous, because
it carries the VIN, the MBB/BMS/module/Sevcon serial numbers, and every byte that
crossed the wire.

So the export is built around one rule: **it verifies its own output.** Every file
written is re-scanned with the same detectors the release gate uses, and if a single
identifier survives, the export fails rather than producing a bundle that merely
looks redacted. A tool that quietly hands someone a VIN is worse than no tool.

Two design constraints, both learned from the committed fixtures:

  Placeholders are the SAME WIDTH as what they replace. The console pads its output
  into columns and some parsing reads by offset, so a shorter placeholder would
  silently re-shape the very records the recipient wants to analyse.

  Replacement is CONSISTENT and one-way. The same serial maps to the same
  placeholder everywhere in the bundle, so cross-references between files still line
  up, and two different serials never collapse into one — but nothing in the output
  can be turned back into the original.

The shape detectors live here rather than in the test tree so that the release gate,
the fixture guards and this exporter all harden together.
"""

import os
import re
import shutil

_L = r"(?<![0-9A-Za-z])"          # left boundary: not preceded by alnum
_R = r"(?![0-9A-Za-z])"           # right boundary: not followed by alnum

# One all-upper and one all-lower VIN alternative, each requiring a letter+digit
# mix. A real VIN is 17 chars over a charset excluding I/O/Q; requiring the mix
# rules out a 17-digit odometer run, and requiring a single case avoids flagging
# mixed-case code identifiers while still catching a lowercase paste.
_VIN_UPPER = (r"(?=[0-9A-HJ-NPR-Z]{17}" + _R + r")"
              r"(?=[A-HJ-NPR-Z]*[0-9])(?=[0-9]*[A-HJ-NPR-Z])[0-9A-HJ-NPR-Z]{17}")
_VIN_LOWER = (r"(?=[0-9a-hj-npr-z]{17}" + _R + r")"
              r"(?=[a-hj-npr-z]*[0-9])(?=[0-9]*[a-hj-npr-z])[0-9a-hj-npr-z]{17}")

VIN_SHAPE = re.compile(_L + "(?:" + _VIN_UPPER + "|" + _VIN_LOWER + ")")
MBB_SERIAL_SHAPE = re.compile(r"(?i)" + _L + r"sj\d{4}zer\d{4}" + _R)
MODULE_SERIAL_SHAPE = re.compile(r"(?i)" + _L + r"17gb\d{4}" + _R)
# A bare digit run has no distinctive shape, so anchor on the console LABEL.
SEVCON_SERIAL_SHAPE = re.compile(r"(?i)(Sevcon Serial num\s*:?\s*)(\d{6,})")

SHAPES = [("VIN", VIN_SHAPE), ("mbb-serial", MBB_SERIAL_SHAPE),
          ("module-serial", MODULE_SERIAL_SHAPE),
          ("sevcon-serial", SEVCON_SERIAL_SHAPE)]

# Same-width placeholders that legitimately appear in the vendored fixtures, and
# which the shape detectors would otherwise flag.
PLACEHOLDERS = {"REDACTEDVIN000000", "REDACTEDMBB00", "REDACTED17GB0"}

# Stems for generated placeholders, padded/truncated to the width of whatever they
# replace so the console's column alignment survives.
_STEM = {"VIN": "REDACTEDVIN", "mbb-serial": "REDACTEDSN",
         "module-serial": "REDMOD", "sevcon-serial": "REDSEV"}


def find_pii_shapes(text):
    """[(label, token)] for every identifier-shaped token that is not already a
    known placeholder. An empty list means clean."""
    hits = []
    for label, rx in SHAPES:
        for tok in rx.findall(text or ""):
            if isinstance(tok, tuple):        # Sevcon: (label, digits)
                tok = tok[-1]
            if tok in PLACEHOLDERS:
                continue
            hits.append((label, tok))
    return hits


class Redactor:
    """Assigns a stable, same-width placeholder per distinct identifier.

    Stateful on purpose: one Redactor is used for a whole bundle so a serial that
    appears in four files reads the same in all four, while two different serials
    stay distinguishable.
    """

    def __init__(self):
        self.mapping = {}
        self.kinds = {}          # original -> which detector matched it
        self._counts = {}

    def _placeholder(self, label, original):
        if original in self.mapping:
            return self.mapping[original]
        n = self._counts.get(label, 0)
        self._counts[label] = n + 1
        stem = _STEM.get(label, "REDACTED")
        body = "%s%d" % (stem, n)
        width = len(original)
        # pad with X (never a digit, so a padded placeholder cannot itself look
        # like a VIN or a serial) and truncate from the left if the stem is long
        out = (body + "X" * width)[:width] if len(body) <= width else body[-width:]
        self.mapping[original] = out
        self.kinds[original] = label
        return out

    def text(self, text):
        """Redact one blob, reusing this bundle's assignments."""
        if not text:
            return text
        for label, rx in SHAPES:
            if label == "sevcon-serial":
                text = rx.sub(
                    lambda m: m.group(1) + self._placeholder(label, m.group(2)),
                    text)
                continue
            text = rx.sub(
                lambda m, _l=label: (m.group(0) if m.group(0) in PLACEHOLDERS
                                     else self._placeholder(_l, m.group(0))),
                text)
        return text


def redact_session(src_dir, dst_dir, overwrite=False):
    """Write a share-safe copy of a session folder. Returns a report.

    Raises FileExistsError if the destination exists and `overwrite` is False, and
    RuntimeError if any output file still carries an identifier — the export
    refuses to produce a bundle it cannot vouch for.
    """
    if not os.path.isdir(src_dir):
        raise NotADirectoryError(src_dir)
    if os.path.exists(dst_dir):
        if not overwrite:
            raise FileExistsError(dst_dir)
        shutil.rmtree(dst_dir)
    os.makedirs(dst_dir)

    red = Redactor()
    written, skipped = [], []
    for name in sorted(os.listdir(src_dir)):
        src = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            continue
        try:
            with open(src, encoding="utf-8", errors="replace") as f:
                body = f.read()
        except OSError:
            skipped.append(name)
            continue
        # a file NAME can carry an identifier too (a decoder export is often
        # named after the VIN), so it goes through the same mapping
        out_name = red.text(name)
        with open(os.path.join(dst_dir, out_name), "w",
                  encoding="utf-8", newline="") as f:
            f.write(red.text(body))
        written.append(out_name)

    # verify our own output rather than trusting the substitution
    leaks = []
    for name in written:
        with open(os.path.join(dst_dir, name), encoding="utf-8",
                  errors="replace") as f:
            for label, tok in find_pii_shapes(f.read()):
                leaks.append((name, label))
        for label, tok in find_pii_shapes(name):
            leaks.append((name, label + " (in the file name)"))
    if leaks:
        shutil.rmtree(dst_dir, ignore_errors=True)
        raise RuntimeError(
            "redaction incomplete, export discarded: "
            + ", ".join("%s: %s" % (n, l) for n, l in leaks[:5]))

    return {
        "source": src_dir,
        "output": dst_dir,
        "files": len(written),
        "skipped": skipped,
        "identifiers_replaced": len(red.mapping),
        "by_kind": {lab: sum(1 for k in red.kinds.values() if k == lab)
                    for lab, _rx in SHAPES
                    if any(k == lab for k in red.kinds.values())},
        "verified_clean": True,
    }
