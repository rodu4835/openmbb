"""A green verdict must not walk a reader past a red row.

With four active OBD trouble codes and the warning lamp ON — or three stored
Sevcon faults — the Health tab shows a red ALERT and the verdict says *"OK —
Nothing in this capture looks wrong with the pack."* Every word of that is
true, and the effect is wrong: the verdict is the last step of the inspection
flow, and somebody standing in a seller's driveway reading a green OK does not
go hunting other tabs.

The hole predates the Sevcon work entirely. `condition.verdict`'s health intake
was a hard-coded allow-list of `("Isolation resistance", "Warning")`, so the OBD
fault-code row had been invisible to the verdict since the verdict existed.

The fix does NOT widen the verdict. A pack verdict that went amber because the
motor controller has a fault would be lying about the pack, and the pack scope
is what makes the number worth trusting. It says the extra sentence instead.
"""

import io
import os

import pytest

from openmbb import condition, health, parsers, report, sessions

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "rev41_sevcon.txt")


def _sevcon(faults):
    with io.open(FIXTURE, encoding="utf-8") as f:
        return f.read().replace("- Number of Faults          :    0",
                                "- Number of Faults          :    %d" % faults)


def _obd(active):
    return ("MIL ON : %d\nNumber of Active DTCs : %d\n"
            "Number of Pending DTCs : 0\n" % (1 if active else 0, active))


def _session(tmp_path, **commands):
    return sessions.Session(str(tmp_path), commands, "")


# --- the composer ------------------------------------------------------------

def test_a_clean_bike_produces_no_notes(tmp_path):
    s = _session(tmp_path, sevcon=_sevcon(0), obd=_obd(0))
    assert condition.beyond_pack_notes(health.health_snapshot(s)) == []


@pytest.mark.parametrize("label,commands", [
    ("Sevcon faults", {"sevcon": lambda: _sevcon(3)}),
    ("Fault codes", {"obd": lambda: _obd(4)}),
])
def test_both_fault_classes_produce_a_note(tmp_path, label, commands):
    """Both, not one. The Sevcon row is new; the OBD row has been invisible to
    the verdict for far longer, and fixing only the new label would have left
    the identical hole one label over."""
    s = _session(tmp_path, **{k: v() for k, v in commands.items()})
    notes = condition.beyond_pack_notes(health.health_snapshot(s))
    assert len(notes) == 1
    assert notes[0].startswith(label)
    # the load-bearing clause: this is what stops the green line above being
    # read as covering it
    assert "does not cover this" in notes[0]
    assert "PACK" in notes[0]


def test_the_verdict_level_is_not_moved_by_a_note(tmp_path):
    """The whole point. A pack verdict that went amber because the CONTROLLER
    has a fault would be lying about the pack."""
    s = _session(tmp_path, sevcon=_sevcon(3), obd=_obd(4))
    metrics = health.health_snapshot(s)
    a = condition.assess("")
    clean = condition.verdict(a, [m for m in metrics
                                  if m["label"] not in ("Sevcon faults",
                                                        "Fault codes")])
    faulted = condition.verdict(a, metrics)
    assert faulted["level"] == clean["level"]
    assert len(faulted["beyond_pack"]) == 2
    assert clean["beyond_pack"] == []


def test_a_watch_row_counts_too(tmp_path):
    """Pending codes grade `watch`, not `alert` — still a fault the pack
    verdict does not cover."""
    s = _session(tmp_path, obd="MIL ON : 0\nNumber of Active DTCs : 0\n"
                               "Number of Pending DTCs : 2\n")
    rows = [m for m in health.health_snapshot(s) if m["label"] == "Fault codes"]
    assert rows and rows[0]["status"] == "watch"
    assert condition.beyond_pack_notes(health.health_snapshot(s))
