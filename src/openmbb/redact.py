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


# A capture is not always written by this program. A file dropped into the
# folder by PowerShell redirection is UTF-16LE, and decoding that as UTF-8 with
# errors="replace" yields mojibake in which no identifier SHAPE can match - so
# the substitution finds nothing, the verification pass finds nothing, and the
# bundle is declared clean with the VIN still in it. tests/test_release_gate.py
# already treats this exact encoding as the realistic hazard on this machine.
_TEXT_ENCODINGS = ("utf-8-sig", "utf-16")


def decode_text(raw):
    """Decode `raw` (BOM-aware, UTF-8 then UTF-16), or None if neither works.

    Public because `sessions` reads captures through it too. There was a
    decoder here and a different one there, and the two halves of the tool
    disagreed about what a capture is - see sessions.read_text.
    """
    for enc in _TEXT_ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, ValueError):
            continue
    return None, None


#: the name this had while it was private; kept so nothing has to change at once
_decode_text = decode_text


def _same_or_inside(a, b):
    """True if path `a` is `b` or sits inside it (either direction is fatal here)."""
    a, b = os.path.abspath(a), os.path.abspath(b)
    try:
        return a == b or os.path.commonpath([a, b]) in (a, b)
    except ValueError:          # different drives on Windows
        return False


#: A `session_raw.log` line, as SessionLogger.raw writes it and nothing else does:
#: `[12:00:00.001] RX 'Zero Motorcycles MBB\r\n'`. This is the signature of a
#: LISTEN capture, which runs no commands at all and so carries no `# command:`
#: header anywhere - the shape v0.24.0 refused.
_RAW_LOG_LINE_RE = re.compile(r"^\[\d\d:\d\d:\d\d\.\d{3}\] (?:RX|TX) ", re.M)


def _capture_shape(folder):
    """(saw_text, saw_command_header) across the files in `folder`.

    The `# command:` header is what SessionLogger writes and what
    sessions.load_session reads back, so it is the same definition of "a
    capture" the rest of the tool uses.

    Decoded with this module's own BOM-aware decoder rather than as UTF-8: a
    capture written by PowerShell redirection is UTF-16LE, and a recognizer that
    could not see its header would refuse a real capture - worse than the hole
    it is closing.

    `saw_text` is reported separately so the caller can stay quiet for a folder
    with no readable text at all. Those already have sharper refusals further
    down ("nothing here to vouch for", "could not be read as text"), and both
    were put there by review findings.
    """
    saw_text = False
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return False, False
    for name in names:
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            continue
        body, _enc = _decode_text(raw)
        if body is None:
            continue
        saw_text = True
        if body.lstrip("\ufeff").startswith("# command:"):
            return True, True
        # A listen session runs no commands - Transport.listen() only drains -
        # so it has no header to find. Its raw log is still OpenMBB's own
        # output, and sharing one is the case this module exists for.
        if _RAW_LOG_LINE_RE.search(body):
            return True, True
    return saw_text, False


def redact_session(src_dir, dst_dir, overwrite=False):
    """Write a share-safe copy of a session folder. Returns a report.

    Raises FileExistsError if the destination exists and `overwrite` is False, and
    RuntimeError if any output file still carries an identifier — the export
    refuses to produce a bundle it cannot vouch for.
    """
    if not os.path.isdir(src_dir):
        raise NotADirectoryError(src_dir)
    # An empty folder was already refused ("nothing here to vouch for"), but a
    # folder holding any readable text passed and came back verified_clean over
    # files nobody had established were a capture. The assurance this module
    # gives is narrow on purpose: it scans for MOTORCYCLE identifiers - a VIN, an
    # MBB or Sevcon serial. Pointed at some other folder it would find none of
    # them and report clean, which a person reasonably reads as "safe to post"
    # while the files carry names, addresses and account numbers it never looked
    # for. So it refuses to vouch for anything it cannot first recognize.
    _saw_text, _saw_capture = _capture_shape(src_dir)
    if _saw_text and not _saw_capture:
        raise ValueError(
            "%s does not look like an OpenMBB capture - nothing in it carries a "
            "'# command:' header or a session_raw.log line. This export only "
            "knows how to look for motorcycle identifiers, so calling another "
            "kind of folder 'verified clean' would vouch for identifiers it "
            "never scanned for." % src_dir)
    # With --overwrite, the rmtree below runs BEFORE the source is listed. Aimed
    # at the source itself that destroyed the capture and then reported a
    # verified-clean export of zero files; aimed at the parent it took the
    # sibling captures with it. A capture costs a contactor-risk event-log read
    # to make and the bike's buffer is only weeks deep, so this is unrecoverable.
    if _same_or_inside(dst_dir, src_dir):
        raise ValueError(
            "refusing to export into the capture itself (or its parent): "
            "%s would destroy %s" % (dst_dir, src_dir))
    # A folder named after the bike - a decoder export often is - would otherwise
    # produce a bundle whose NAME carries the VIN while the report says clean.
    leaks_in_name = find_pii_shapes(os.path.basename(os.path.normpath(dst_dir)))
    if leaks_in_name:
        raise ValueError(
            "refusing to name the export after an identifier (%s): choose "
            "another destination" % ", ".join(sorted({l for l, _t in leaks_in_name})))
    if os.path.exists(dst_dir):
        if not overwrite:
            raise FileExistsError(dst_dir)
        shutil.rmtree(dst_dir)
    os.makedirs(dst_dir)

    red = Redactor()
    written, skipped, unscanned, pairs = [], [], [], []
    for name in sorted(os.listdir(src_dir)):
        src = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            continue
        try:
            with open(src, "rb") as f:
                raw = f.read()
        except OSError:
            skipped.append(name)
            continue
        body, enc = _decode_text(raw)
        # a file NAME can carry an identifier too (a decoder export is often
        # named after the VIN), so it goes through the same mapping
        out_name = red.text(name)
        if body is None:
            # Not text under any encoding we can scan. Copying it verbatim would
            # put an unexamined file in a bundle the report calls clean, so it is
            # named and left out - and it costs the bundle its clean bill below.
            unscanned.append(name)
            continue
        with open(os.path.join(dst_dir, out_name), "w",
                  encoding="utf-8", newline="") as f:
            f.write(red.text(body))
        written.append(out_name)
        # the source name too: a file whose NAME carried an identifier is
        # written under a different one, so a caller showing the two side by
        # side cannot pair them up by name alone
        pairs.append((name, out_name))
    if unscanned:
        shutil.rmtree(dst_dir, ignore_errors=True)
        raise RuntimeError(
            "export discarded: %d file(s) could not be read as text and so could "
            "not be checked for identifiers: %s"
            % (len(unscanned), ", ".join(sorted(unscanned)[:5])))
    if not written:
        # zero files re-scanned clean is vacuously true, and printing the usual
        # assurance over an empty set is exactly the false pass this module exists
        # to refuse
        shutil.rmtree(dst_dir, ignore_errors=True)
        raise RuntimeError(
            "export discarded: no readable files in %s - there is nothing here "
            "to vouch for" % src_dir)

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
        "unscanned": [],          # anything unscannable aborted the export above
        "identifiers_replaced": len(red.mapping),
        "pairs": pairs,
        "by_kind": {lab: sum(1 for k in red.kinds.values() if k == lab)
                    for lab, _rx in SHAPES
                    if any(k == lab for k in red.kinds.values())},
        "verified_clean": True,
    }
