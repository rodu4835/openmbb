"""The share-safe export.

The property that matters is not "it replaces things" but "it refuses to hand
over a bundle it cannot vouch for". These tests are written against that.
"""

import os

import pytest

from openmbb import redact, report, sessions

# Invented identifiers that MATCH the detector shapes — the tests are worthless
# unless the input is dirty. They are assembled from fragments on purpose: a
# tracked file containing a whole shape-matching token would trip the release
# gate, which scans this repo for exactly that. Do not "tidy" these into single
# literals, and never paste a real one here — the gate is the last line, not the
# first, and it fired on precisely this mistake once already.
_VIN = "5TESTVNPRZ" + "4200001"          # 17 chars, VIN charset, letters+digits
_MBB = "SJ" + "0000" + "ZER" + "0000"    # same shape as a real MBB/BMS serial
_BMS = "SJ" + "1111" + "ZER" + "1111"    # ...and a second, DIFFERENT one
_MODULE = "17" + "gb" + "0000"
_SEVCON = "99" + "999999"

CAPTURE = {
    "version": "  Firmware Rev : 41\n",
    "bms": ("  - MBB Serial Number         : %s\n" % _MBB
            + "  - BMS Serial Number         : %s\n" % _BMS
            + "  - Battery Serial Number     : %s\n" % _MODULE
            + "  - Pack SOC                  : 96%\n"),
    "stats": ("  - Bike VIN                  : %s\n" % _VIN
              + "  - Sevcon Serial num         : %s\n" % _SEVCON
              + "  - Odometer                  : 7924 km\n"),
}


def _write_capture(folder, commands=None):
    folder.mkdir(parents=True, exist_ok=True)
    for i, (cmd, body) in enumerate(sorted((commands or CAPTURE).items())):
        (folder / ("%03d_%s.txt" % (i + 1, cmd))).write_text(
            "# command: %s\n# time: 12:00:0%d.000\n\n%s" % (cmd, i, body),
            encoding="utf-8")
    (folder / "session_meta.txt").write_text(
        "OpenMBB session metadata\ntime: 2026-08-19T16:10:11\n", encoding="utf-8")
    return folder


def test_a_real_capture_shape_is_scanned_as_dirty():
    # the detectors must actually fire on the inputs, or the rest proves nothing
    kinds = {k for k, _v in redact.find_pii_shapes(
        CAPTURE["bms"] + CAPTURE["stats"])}
    assert kinds == {"VIN", "mbb-serial", "module-serial", "sevcon-serial"}


def test_the_export_is_clean_and_says_what_it_replaced(tmp_path):
    src = _write_capture(tmp_path / "cap")
    rep = redact.redact_session(str(src), str(tmp_path / "out"))
    assert rep["verified_clean"] is True
    assert rep["identifiers_replaced"] == 5          # VIN, 2 serials, module, sevcon
    assert rep["by_kind"]["mbb-serial"] == 2         # kept distinct, not collapsed
    for name in os.listdir(str(tmp_path / "out")):
        body = (tmp_path / "out" / name).read_text(encoding="utf-8")
        assert redact.find_pii_shapes(body) == []


def test_placeholders_keep_the_column_width(tmp_path):
    # the console pads into columns and some parsing reads by offset, so a
    # shorter placeholder would silently re-shape the records
    src = _write_capture(tmp_path / "cap")
    redact.redact_session(str(src), str(tmp_path / "out"))
    before = (src / "001_bms.txt").read_text(encoding="utf-8").splitlines()
    after = (tmp_path / "out" / "001_bms.txt").read_text(encoding="utf-8").splitlines()
    assert [len(x) for x in before] == [len(x) for x in after]


def test_two_different_serials_do_not_collapse_into_one(tmp_path):
    src = _write_capture(tmp_path / "cap")
    redact.redact_session(str(src), str(tmp_path / "out"))
    body = (tmp_path / "out" / "001_bms.txt").read_text(encoding="utf-8")
    # two serials of the SAME shape and different values — the real risk, since a
    # single shared placeholder would silently merge two components into one
    mbb = [l for l in body.splitlines() if "MBB Serial" in l][0]
    bms = [l for l in body.splitlines() if "BMS Serial" in l][0]
    assert mbb.split(":")[1].strip() != bms.split(":")[1].strip()
    assert _MBB not in body and _BMS not in body


def test_the_same_identifier_reads_the_same_across_files(tmp_path):
    # cross-references between files have to survive, or the bundle is useless
    caps = dict(CAPTURE)
    caps["status"] = "  - Bike VIN : %s\n" % _VIN
    src = _write_capture(tmp_path / "cap", caps)
    redact.redact_session(str(src), str(tmp_path / "out"))
    bodies = [(tmp_path / "out" / n).read_text(encoding="utf-8")
              for n in os.listdir(str(tmp_path / "out"))]
    seen = {l.split(":")[-1].strip() for b in bodies for l in b.splitlines()
            if "VIN" in l}
    assert len(seen) == 1 and _VIN not in seen.pop()


def test_an_identifier_in_a_file_name_is_replaced_too(tmp_path):
    src = tmp_path / "cap"
    src.mkdir()
    (src / (_VIN + "_MBB.txt")).write_text(
        "# command: bms\n# time: 12:00:00.000\n\nnothing sensitive inside\n",
        encoding="utf-8")
    redact.redact_session(str(src), str(tmp_path / "out"))
    names = os.listdir(str(tmp_path / "out"))
    assert not any(_VIN in n for n in names)
    assert redact.find_pii_shapes(" ".join(names)) == []


def test_a_bundle_it_cannot_vouch_for_is_discarded(tmp_path, monkeypatch):
    # If substitution ever misses something, the export must delete what it wrote
    # rather than hand over a folder that merely looks redacted.
    src = _write_capture(tmp_path / "cap")
    monkeypatch.setattr(redact.Redactor, "text", lambda self, t: t)   # no-op
    with pytest.raises(RuntimeError, match="redaction incomplete"):
        redact.redact_session(str(src), str(tmp_path / "out"))
    assert not os.path.exists(str(tmp_path / "out"))


def test_it_refuses_to_overwrite_unless_asked(tmp_path):
    src = _write_capture(tmp_path / "cap")
    (tmp_path / "out").mkdir()
    with pytest.raises(FileExistsError):
        redact.redact_session(str(src), str(tmp_path / "out"))
    rep = redact.redact_session(str(src), str(tmp_path / "out"), overwrite=True)
    assert rep["verified_clean"] is True


def test_a_redacted_capture_still_analyzes_the_same(tmp_path):
    # the whole point: what is shared must still be worth analysing
    src = _write_capture(tmp_path / "cap")
    redact.redact_session(str(src), str(tmp_path / "out"))
    a = report.analyze_folder(str(src))
    b = report.analyze_folder(str(tmp_path / "out"))
    assert [m["value"] for m in a["health"]] == [m["value"] for m in b["health"]]
    assert a["verdict"]["level"] == b["verdict"]["level"]


def test_a_missing_folder_raises_rather_than_writing_anything(tmp_path):
    with pytest.raises(NotADirectoryError):
        redact.redact_session(str(tmp_path / "nope"), str(tmp_path / "out"))
    assert not os.path.exists(str(tmp_path / "out"))


def test_redact_refuses_to_vouch_for_a_folder_that_is_not_a_capture(tmp_path):
    """An empty folder was already refused ("nothing here to vouch for"), but a
    folder holding any readable text passed and came back verified_clean.

    The assurance here is narrow on purpose - it scans for a VIN, an MBB serial,
    a Sevcon serial. Pointed at some other folder it finds none of them and
    reports clean, which a person reasonably reads as "safe to post" over files
    carrying names and addresses it never looked for.
    """
    src = tmp_path / "not-a-capture"
    src.mkdir()
    (src / "holiday.txt").write_text("nothing to do with a motorcycle\n",
                                     encoding="utf-8")
    out = tmp_path / "bundle"
    with pytest.raises(ValueError) as excinfo:
        redact.redact_session(str(src), str(out))
    assert "does not look like an OpenMBB capture" in str(excinfo.value)
    # and it refused before building anything
    assert not out.exists()


def test_redact_still_exports_a_real_capture(tmp_path):
    """The guard must recognize the thing it is meant to pass, or it is just a
    refusal. Same fixture the rest of this file uses."""
    src = tmp_path / "src"
    _write_capture(src)
    rep = redact.redact_session(str(src), str(tmp_path / "bundle"))
    assert rep["verified_clean"] is True
    assert rep["files"] > 0


def test_a_capture_written_entirely_in_utf16_is_still_recognized(tmp_path):
    """The guard that refuses a folder which is not a capture has to SEE a
    capture written the way Windows actually writes one.

    PowerShell redirection produces UTF-16LE. Read as UTF-8 the `# command:`
    header is mojibake, so a recognizer that only decoded UTF-8 would refuse a
    real capture as "not a capture" - worse than the hole it closes, and
    invisible to every other test here because they all write UTF-8.
    """
    cap = tmp_path / "utf16cap"
    cap.mkdir()
    # A real capture folder carries session_meta.txt beside the command files,
    # and that detail is load-bearing here: one readable file is what makes the
    # recognizer report "there is text in this folder". Without it, a recognizer
    # that cannot decode UTF-16 sees an EMPTY folder and stays quiet, the export
    # succeeds, and this test passes while proving nothing.
    (cap / "session_meta.txt").write_text(
        "OpenMBB session metadata\ncapture_format: 1\n"
        "time: 2026-08-19T16:10:11\n", encoding="utf-8")
    (cap / "001_bms.txt").write_bytes(
        "# command: bms\n\n  - Pack SOC : 61%\n".encode("utf-16"))
    (cap / "002_stats.txt").write_bytes(
        ("# command: stats\n\n  - Bike VIN : %s\n" % _VIN).encode("utf-16"))
    rep = redact.redact_session(str(cap), str(tmp_path / "out"))
    assert rep["verified_clean"] is True
    assert rep["identifiers_replaced"] == 1


def test_an_empty_folder_keeps_its_own_sharper_refusal(tmp_path):
    """The not-a-capture guard must not preempt this one. "Nothing here to vouch
    for" is the more useful answer for an empty folder and a review finding put
    it there; the newer guard speaks only when there IS text and none of it is a
    capture."""
    (tmp_path / "empty").mkdir()
    with pytest.raises(RuntimeError, match="nothing here to vouch for"):
        redact.redact_session(str(tmp_path / "empty"), str(tmp_path / "out"))


def test_a_listen_capture_can_still_be_shared(tmp_path):
    """A listen session runs no commands - Transport.listen() only drains - so
    it carries no `# command:` header anywhere, and the not-a-capture guard
    shipped in v0.24.0 refused it.

    That refusal is the safe direction and still the wrong answer: a listen
    capture is the "my cable does not work, here is what the bike said"
    artifact, which is the forum-help case this module was built for. Refusing
    it pushes somebody toward posting the raw log instead, which is worse than
    anything the guard was protecting against.
    """
    cap = tmp_path / "2026-08-23_120000_COM4_listen"
    cap.mkdir()
    (cap / "session_raw.log").write_text(
        "[12:00:00.001] RX 'Zero Motorcycles MBB\r\n'\n"
        "[12:00:00.100] RX '  - Bike VIN : %s\r\n'\n" % _VIN,
        encoding="utf-8")
    rep = redact.redact_session(str(cap), str(tmp_path / "bundle"))
    assert rep["verified_clean"] is True
    assert rep["identifiers_replaced"] == 1
    out = (tmp_path / "bundle" / "session_raw.log").read_text(encoding="utf-8")
    assert _VIN not in out


def test_cli_redact_refuses_a_non_capture_with_a_message_not_a_traceback(tmp_path):
    """redact_session's refusals are ValueError, and cmd_redact caught only
    FileExistsError and RuntimeError - so a wrong source folder killed the
    command with a raw Python traceback.

    Exit 2, not 1: this command uses 1 for "verification failed", which says the
    DATA was bad. A refused REQUEST is a different thing to tell a script.
    """
    import subprocess
    import sys
    src = tmp_path / "not-a-capture"
    src.mkdir()
    (src / "holiday.txt").write_text("nothing to do with a motorcycle\n",
                                     encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "openmbb.cli", "redact", str(src),
         "--out", str(tmp_path / "bundle")],
        capture_output=True, text=True, timeout=300)
    assert r.returncode == 2, (r.returncode, r.stderr[-500:])
    assert "Traceback" not in r.stderr
    assert "does not look like an OpenMBB capture" in r.stderr
    assert not (tmp_path / "bundle").exists()
