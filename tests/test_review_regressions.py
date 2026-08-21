"""Regressions for the defects an adversarial review of the v0.23 work found.

Each test names the failure it locks out, because several of these are the kind
that read as working code: a verdict computed from the wrong evidence, an export
that reports success over an empty set, a measurement that is confidently wrong
by a factor of four. None of them raise, and none of them look wrong on screen.
"""

import io
import json
import os
import shutil

import pytest

from openmbb import condition, health, library, parsers, redact, report, rides, sessions

REAL = r"C:\Users\durha\Documents\OpenMBB\openmbb-sessions"
_HAVE_REAL = os.path.isdir(REAL)
needs_real = pytest.mark.skipif(not _HAVE_REAL, reason="reference captures not present")

VIN = "5TESTVNPRZ" + "4200001"          # fragment-assembled: the release gate scans this file


def _ride(n, soc_from, soc_to, km, odo0=6000, start_min=0, amps=30, volts=110.0):
    out = []
    for i in range(n):
        f = i / float(n - 1)
        m = start_min + i
        out.append(" 00001     06/24/2026 %02d:%02d:00   Riding   PackTemp: h 30C, "
                   "l 28C, AmbTemp: 20C, PackSOC: %d%%, Vpack:%.3fV, BattAmps: %4d, "
                   "MotAmps: %4d, MotRPM:3000, Odo: %dkm, MinCell: 3700mV"
                   % (8 + m // 60, m % 60,
                      round(soc_from + (soc_to - soc_from) * f), volts, amps, amps,
                      round(odo0 + km * f)))
    return out


def _capture(folder, commands):
    folder.mkdir(parents=True, exist_ok=True)
    for i, (cmd, body) in enumerate(sorted(commands.items())):
        (folder / ("%03d_%s.txt" % (i + 1, cmd))).write_text(
            "# command: %s\n# time: 12:00:0%d.000\n\n%s" % (cmd, i % 10, body),
            encoding="utf-8")
    return str(folder)


# --- redact: the export must not destroy what it was pointed at --------------

def test_exporting_into_the_capture_itself_is_refused_not_performed(tmp_path):
    # `shutil.rmtree(dst)` ran before the source was listed, so aiming the export
    # at its own source deleted the capture and then reported a verified-clean
    # export of zero files. A capture costs a contactor-risk event-log read and
    # the bike's buffer is weeks deep, so there is nothing to recover.
    cap = _capture(tmp_path / "cap", {"bms": "  - Pack SOC : 61%\n"})
    with pytest.raises(ValueError, match="refusing to export into"):
        redact.redact_session(cap, cap, overwrite=True)
    assert os.listdir(cap), "the source was destroyed"
    # ...and the parent is worse: it takes the sibling captures with it
    with pytest.raises(ValueError, match="refusing to export into"):
        redact.redact_session(cap, str(tmp_path), overwrite=True)
    assert os.listdir(cap)


def test_an_export_of_nothing_is_not_a_clean_export(tmp_path):
    # zero files re-scanned clean is vacuously true; printing the usual assurance
    # over an empty set is exactly the false pass this module exists to refuse
    (tmp_path / "empty").mkdir()
    with pytest.raises(RuntimeError, match="nothing here to vouch for"):
        redact.redact_session(str(tmp_path / "empty"), str(tmp_path / "out"))


def test_a_utf16_capture_file_is_redacted_rather_than_waved_through(tmp_path):
    # PowerShell redirection writes UTF-16LE. Decoded as UTF-8 with
    # errors="replace" it becomes mojibake in which no identifier SHAPE matches,
    # so the substitution found nothing, the verification found nothing, and the
    # bundle was declared clean with the VIN still in it.
    cap = tmp_path / "cap"
    cap.mkdir()
    io.open(str(cap / "001_bms.txt"), "w", encoding="utf-8").write(
        "# command: bms\n\n  - Pack SOC : 61%\n")
    io.open(str(cap / "002_stats.txt"), "w", encoding="utf-16").write(
        "# command: stats\n\n  - Bike VIN : %s\n" % VIN)
    rep = redact.redact_session(str(cap), str(tmp_path / "out"))
    assert rep["verified_clean"] is True and rep["identifiers_replaced"] == 1
    raw = open(str(tmp_path / "out" / "002_stats.txt"), "rb").read()
    assert VIN.encode("utf-8") not in raw
    assert VIN.encode("utf-16-le") not in raw


def test_a_file_that_decodes_under_nothing_aborts_the_export(tmp_path):
    # copying an unexamined file into a bundle the report calls clean is the
    # same false pass by another route
    cap = tmp_path / "cap"
    cap.mkdir()
    io.open(str(cap / "001_bms.txt"), "w", encoding="utf-8").write("# command: bms\n\nok\n")
    open(str(cap / "blob.bin"), "wb").write(b"\xff\xfe\x81\x8d\x8f\x90\x9d")   # odd length
    with pytest.raises(RuntimeError, match="could not be read as text"):
        redact.redact_session(str(cap), str(tmp_path / "out"))
    assert not os.path.exists(str(tmp_path / "out"))


def test_the_bundle_may_not_be_named_after_an_identifier(tmp_path):
    # a decoder export is often named after the bike; the files were redacted
    # while the folder carrying them announced the VIN
    cap = _capture(tmp_path / "cap", {"bms": "  - Pack SOC : 61%\n"})
    with pytest.raises(ValueError, match="refusing to name the export"):
        redact.redact_session(cap, str(tmp_path / (VIN + "_shared")))


# --- library: the verdict column a buyer triages on --------------------------

def test_the_library_grades_with_the_health_metrics_like_the_condition_tab(tmp_path):
    # Called without metrics, condition.verdict forces the resting cell-spread
    # check to "unknown" and never appends the isolation or warning checks - so a
    # bike the Condition tab grades "concern" showed a green "ok" here.
    log = "\n".join(_ride(41, 100, 20, 40))
    metrics = [{"label": "Isolation resistance", "status": "alert", "value": 42,
                "display": "42 kOhm", "note": ""}]
    with_m = condition.verdict(condition.assess(log), metrics)["level"]
    without = condition.verdict(condition.assess(log))["level"]
    assert with_m != without, "fixture no longer distinguishes the two paths"

    cap = _capture(tmp_path / "cap", {"eventlogdump": log})
    import openmbb.library as lib
    orig = lib.health.health_snapshot
    lib.health.health_snapshot = lambda s, *a, **k: metrics
    try:
        got = lib.deep_verdict(cap)
    finally:
        lib.health.health_snapshot = orig
    assert got["level"] == with_m


def test_writing_the_verdict_cache_does_not_restamp_the_capture(tmp_path):
    # the Charts trend metrics plot each capture AT its folder mtime, so simply
    # opening the session library restamped every capture to "now" and flattened
    # a year of history into one second - newest-first, so it plotted reversed
    cap = _capture(tmp_path / "2026-07-10_124738_435640_COM4",
                   {"eventlogdump": "\n".join(_ride(41, 100, 20, 40))})
    old = 1_600_000_000
    os.utime(cap, (old, old))
    library.deep_verdict(cap)
    assert os.path.exists(os.path.join(cap, library.SUMMARY_FILE))
    assert int(os.path.getmtime(cap)) == old


def test_a_cached_verdict_from_a_different_log_is_not_reused(tmp_path):
    # a truncated first eventlogdump graded ok and cached; the complete re-read
    # lands as a higher-numbered file that load_session prefers, and the library
    # kept serving the partial log's verdict
    cap = _capture(tmp_path / "cap",
                   {"eventlogdump": "\n".join(_ride(41, 100, 20, 40))})
    first = library.deep_verdict(cap)
    assert first is not None and library.cached_verdict(cap) is not None
    io.open(os.path.join(cap, "099_eventlogdump.txt"), "w", encoding="utf-8").write(
        "# command: eventlogdump\n# time: 12:00:09.000\n\n"
        + "\n".join(_ride(41, 100, 20, 40, amps=60)))
    assert library.cached_verdict(cap) is None, "stale verdict survived a re-read"


def test_a_cache_from_older_checks_is_still_rejected(tmp_path):
    cap = _capture(tmp_path / "cap",
                   {"eventlogdump": "\n".join(_ride(41, 100, 20, 40))})
    library.deep_verdict(cap)
    path = os.path.join(cap, library.SUMMARY_FILE)
    data = json.load(open(path, encoding="utf-8"))
    data["version"] = library.SUMMARY_VERSION - 1
    json.dump(data, open(path, "w", encoding="utf-8"))
    assert library.cached_verdict(cap) is None


# --- rides: numbers that were confidently wrong ------------------------------

def test_regen_reduces_consumption_rather_than_adding_to_it():
    # abs(v*i) counted regenerative braking as energy DRAWN, so a braking-heavy
    # ride was charged twice for the same energy
    plain = parsers.parse_ride_log("\n".join(_ride(41, 100, 60, 40, amps=30)))
    braked = parsers.parse_ride_log("\n".join(
        _ride(21, 100, 80, 20, amps=30) + _ride(21, 80, 70, 20, odo0=6020,
                                                start_min=21, amps=-30)))
    assert rides.consumption(braked)["wh_per_km"] < rides.consumption(plain)["wh_per_km"]


@needs_real
def test_the_reference_bike_reads_the_signed_consumption():
    # the band this project documented (70-109 Wh/km) is the SIGNED band; the
    # abs() crept in after those figures were measured
    log = sessions.load_session(
        os.path.join(REAL, "2026-08-19_160449_675068_COM4")).cmd("eventlogdump")
    recs = parsers.parse_ride_log(log)
    assert len([r for r in recs if (r.get("battamps") or 0) < 0]) > 50   # real regen
    c = rides.consumption(recs)
    assert c["wh_per_km"] == pytest.approx(90.6, abs=0.5)
    assert c["wh_per_km_low"] == pytest.approx(70.4, abs=0.5)


def test_a_shallow_discharge_cannot_be_scaled_to_a_full_charge():
    # one gentle 7 km ride at 100->95% extrapolated to "about 140 km" and
    # "about 10.3 kWh" - both believable Gen2 figures, both ~4x wrong
    shallow = parsers.parse_ride_log("\n".join(_ride(8, 100, 95, 7)))
    assert rides.range_estimate(shallow) is None
    # and the commuter case: split_rides has no time-gap rule, so nightly
    # top-ups of <=8 points merge weeks of short trips into one segment whose
    # distance spans many cycles while its SOC span covers one
    lines = []
    for day in range(21):
        lines += _ride(9, 100, 92, 16, odo0=6000 + day * 16, start_min=day * 1440)
    assert rides.range_estimate(parsers.parse_ride_log("\n".join(lines))) is None


def test_a_deep_discharge_still_answers_and_says_how_far_it_was_scaled():
    r = rides.range_estimate(parsers.parse_ride_log("\n".join(_ride(41, 100, 20, 40))))
    assert r["full_charge_km"] == pytest.approx(50, rel=0.05)
    # how much of the answer is scaling rather than measurement
    assert r["extrapolation_x"] == pytest.approx(1.25, abs=0.01)


def test_a_band_of_one_ride_is_withheld_not_printed_twice(tmp_path):
    # "(middle 80% of rides: 75.3-75.3)" claims a precision that was invented
    recs = parsers.parse_ride_log("\n".join(_ride(41, 100, 20, 40)))
    c = rides.consumption(recs)
    assert c["rides"] == 1
    assert c["wh_per_km"] is not None
    assert c["wh_per_km_low"] is None and c["wh_per_km_high"] is None
    s = sessions.Session(str(tmp_path), {"eventlogdump": "\n".join(_ride(41, 100, 20, 40))}, "")
    text = report.format_report(report.analyze_session(s))
    assert "too few for a spread" in text and "middle 80%" not in text


# --- report: a claim about switches that were never read ---------------------

def test_the_report_will_not_clear_interlocks_it_never_read(tmp_path):
    # the console prints Key On and the rails BEFORE the interlocks, so a read
    # that ended early leaves a populated dict with every interlock missing -
    # and the old test could not fire, rendering that as a clean bill of health
    truncated = ("  - Key On                    :       Yes  - Raw : 1\n"
                 "  - Pack Voltage              : 112167 mV  - (   3889 ADC)\n")
    s = sessions.Session(str(tmp_path), {"inputs": truncated}, "")
    text = report.format_report(report.analyze_session(s))
    assert "no interlock was holding it" not in text
    assert "cannot say whether an interlock was holding it" in text

    full = truncated + ("  - Kill Switch Pos           :       Run\n"
                        "  - Kickstand Switch Pos      :        Up\n"
                        "  - Battery Thr En            :   Enabled\n")
    s2 = sessions.Session(str(tmp_path), {"inputs": full}, "")
    assert "no interlock was holding it" in report.format_report(
        report.analyze_session(s2))


# --- the headless path -------------------------------------------------------

def test_redact_prints_where_a_user_can_see_it():
    # in the frozen windowed build sys.stdout is None until _ensure_console()
    # runs, so every line redact printed - including "export discarded" - went
    # nowhere
    from openmbb import cli
    assert "redact" in cli.HEADLESS_COMMANDS
