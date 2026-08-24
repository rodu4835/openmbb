"""Mutation manifest and runner: proof that the guards can actually fail.

Sixteen commits in this repository say "mutation-checked N/N". Every one of those
checks was real, and every one was run from a throwaway script that no longer
exists. The discipline this project leans on hardest was, until now, enforced by
nothing you could run.

That matters because a test asserting a guard is not the same as a test that
would NOTICE the guard being removed. A regression test written against a fixture
the guard does not actually govern passes forever, in both directions, and reads
exactly like a real one. The only way to tell them apart is to break the fix on
purpose and watch the named test go red.

WHAT AN ENTRY IS

    (label, path, old, new, test)

`old` is replaced by `new` in `path` (repo-relative), `test` is then run, and it
MUST FAIL - specifically with pytest exit 1, meaning tests ran and failed. Any
other exit (a renamed test id, a collection error) means the check DID NOT RUN,
which is reported as ERROR rather than counted as a catch. Every named test is
also run on the clean tree first and must pass there, since a test that was
already failing would otherwise be indistinguishable from a perfect catch. Both may be tuples of equal length when re-creating the defect takes
more than one edit - moving a call from before a write to after it, say, which is
the difference between testing that a record EXISTS and testing that it is
written in time to survive the failure it exists for. Anything else is reported: a test that stays green under its own
mutation is not testing what its name says, and is a finding in its own right -
that is how the UTF-16 recognizer entry below was caught being vacuous.

    python tests/mutations.py            # run them all
    python tests/mutations.py redact     # only entries whose label matches

This is NOT part of the default suite: it rewrites files under src/. It refuses
to run against a dirty working tree, so `git checkout -- .` is always a complete
recovery if it is interrupted between the write and the restore.

BACKFILL

The entries here are the ones verified at the commit that introduced them. The
~52 mutations named in earlier commit bodies are not here yet; they are prose and
have to be reconstructed and re-verified one at a time rather than transcribed on
faith. Add them as they are re-established - an entry nobody has watched fail is
worth less than no entry at all.
"""

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CRLF = chr(13) + chr(10)
LF = chr(10)


# --- the manifest ------------------------------------------------------------
# Grouped by the commit that established them. Keep the label short enough to
# read as a column and specific enough to name the defect it re-creates.

MUTATIONS = [

    # 8fa44c4 - the pre-bike-day selftest, and the contactor gate's record
    ("selftest: drop the heavy_consent kwarg",
     "src/openmbb/cli.py",
     '                          heavy_consent="selftest: simulator only, no bike attached",' + LF,
     "",
     "tests/test_cli_selftest.py::test_the_selftest_runs_all_the_way_to_the_end"),

    ("selftest: stop exercising the contactor gate",
     "src/openmbb/cli.py",
     '''    try:
        tr.exec_command("dumpall")
        check("heavy read refused without consent", False)
    except BlockedCommandError:
        check("heavy read refused without consent", True)''',
     "    pass",
     "tests/test_cli_selftest.py::test_the_selftest_exercises_the_gate_that_broke_it"),

    # A real move, not a deletion: the test drives a port whose write() raises,
    # so this measures the ORDER of the record rather than its mere presence.
    # Deleting the call would prove only that the test notices it missing.
    ("consent: journal it after the wire instead of before",
     "src/openmbb/transport.py",
     '''            if head in HEAVY_COMMANDS:
                self.logger.journal_consent(cmd, heavy_consent)
            self.logger.raw("TX", wire)     # logger masks any registered secrets
            self.port.write(wire)''',
     '''            self.logger.raw("TX", wire)     # logger masks any registered secrets
            self.port.write(wire)
            if head in HEAVY_COMMANDS:
                self.logger.journal_consent(cmd, heavy_consent)''',
     "tests/test_cli_selftest.py::test_the_consent_is_recorded_before_the_first_byte"),

    ("consent: journal it before the gate, so a refusal leaves a record",
     "src/openmbb/transport.py",
     '''        head = (str(cmd).strip().split() or [""])[0].lower()
        if head in HEAVY_COMMANDS and not (isinstance(heavy_consent, str)
                                           and heavy_consent.strip()):''',
     '''        head = (str(cmd).strip().split() or [""])[0].lower()
        if head in HEAVY_COMMANDS:
            self.logger.journal_consent(cmd, heavy_consent)
        if head in HEAVY_COMMANDS and not (isinstance(heavy_consent, str)
                                           and heavy_consent.strip()):''',
     "tests/test_cli_selftest.py::test_a_refused_heavy_read_leaves_no_consent_record"),

    ("consent: stop masking the consent sentence",
     "src/openmbb/transport.py",
     "            self._mask(str(cmd).strip()), self._mask(str(consent).strip()))",
     "            self._mask(str(cmd).strip()), str(consent).strip())",
     "tests/test_cli_selftest.py::test_the_consent_record_is_masked_like_everything_else"),

    ("consent: journal every command, burying the two that matter",
     "src/openmbb/transport.py",
     '''            if head in HEAVY_COMMANDS:
                self.logger.journal_consent(cmd, heavy_consent)''',
     '''            if True:
                self.logger.journal_consent(cmd, heavy_consent)''',
     "tests/test_cli_selftest.py::test_an_ordinary_read_is_not_journalled"),

    # a199424 - the capture format stamp, and three ways of vouching blind
    ("format: guess a malformed version as 1 instead of refusing",
     "src/openmbb/sessions.py",
     '''    except ValueError:
        raise CaptureFormatError(
            "%s says capture_format: %r, which is not a version number. The "
            "file is damaged or was not written by OpenMBB; reading it as a "
            "format-1 capture would be a guess." % (folder, raw))''',
     '''    except ValueError:
        return CAPTURE_FORMAT''',
     "tests/test_capture_format.py::test_a_version_that_is_not_a_version_is_refused_not_guessed"),

    ("format: read a capture from the future anyway",
     "src/openmbb/sessions.py",
     "    if claimed > CAPTURE_FORMAT:",
     "    if False:",
     "tests/test_capture_format.py::test_a_capture_from_the_future_is_refused_and_says_which_is_older"),

    ("format: stop checking the format in the loader",
     "src/openmbb/sessions.py",
     "    capture_format(folder)          # refuses before anything is believed" + LF,
     "",
     "tests/test_capture_format.py::test_a_capture_from_the_future_is_refused_and_says_which_is_older"),

    ("format: treat silence as a refusal instead of format 1",
     "src/openmbb/sessions.py",
     '''    m = _CAPTURE_FORMAT_RE.search(text)
    if m is None:
        return CAPTURE_FORMAT          # meta file, but from before the stamp''',
     '''    m = _CAPTURE_FORMAT_RE.search(text)
    if m is None:
        raise CaptureFormatError("no stamp")''',
     "tests/test_capture_format.py::test_a_folder_that_makes_no_claim_is_format_one"),

    ("format: accept 0 and -1 as version numbers",
     "src/openmbb/sessions.py",
     "        if claimed < 1:",
     "        if False:",
     "tests/test_capture_format.py::test_a_version_that_is_not_a_version_is_refused_not_guessed"),

    ("format: stop stamping new captures",
     "src/openmbb/gui.py",
     '                    "capture_format: %d" % sessions.CAPTURE_FORMAT,' + LF,
     "",
     "tests/test_gui_flow.py::test_a_new_capture_states_the_format_it_was_written_in"),

    ("analyze: let has_settings trust the filename again",
     "src/openmbb/report.py",
     '''            "has_settings": bool(
                transport.parse_settings_dump(session.settings_text or "")[0]),''',
     '''            "has_settings": bool((session.settings_text or "").strip()),''',
     "tests/test_report.py::test_cli_analyze_refuses_a_capture_whose_headers_will_not_read"),

    ("analyze: let the format refusal escape as a traceback and exit 1",
     "src/openmbb/cli.py",
     '''    except sessions_mod.CaptureFormatError as e:
        print("Cannot analyze %s: %s" % (args.folder, e), file=sys.stderr)
        return 2''',
     '''    except ZeroDivisionError as e:
        print("Cannot analyze %s: %s" % (args.folder, e), file=sys.stderr)
        return 2''',
     "tests/test_report.py::test_cli_analyze_refuses_a_capture_from_a_newer_openmbb"),

    ("redact: vouch for any folder again",
     "src/openmbb/redact.py",
     "    if _saw_text and not _saw_capture:",
     "    if False:",
     "tests/test_redact.py::test_redact_refuses_to_vouch_for_a_folder_that_is_not_a_capture"),

    ("redact: recognize UTF-8 only, refusing a real UTF-16 capture",
     "src/openmbb/redact.py",
     '''        body, _enc = _decode_text(raw)
        if body is None:
            continue
        saw_text = True''',
     '''        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        saw_text = True''',
     "tests/test_redact.py::test_a_capture_written_entirely_in_utf16_is_still_recognized"),

    ("redact: stop recognizing a listen capture (no commands, raw log only)",
     "src/openmbb/redact.py",
     '''        if _RAW_LOG_LINE_RE.search(body):
            return True, True''',
     '''        if False:
            return True, True''',
     "tests/test_redact.py::test_a_listen_capture_can_still_be_shared"),

    ("redact: preempt the empty-folder refusal with the vaguer one",
     "src/openmbb/redact.py",
     "    if _saw_text and not _saw_capture:",
     "    if not _saw_capture:",
     "tests/test_redact.py::test_an_empty_folder_keeps_its_own_sharper_refusal"),

    # a646b7c + the v0.24.1 bundle - the sharing path's refusals
    ("redact/cli: stop catching the refusal, so it escapes as a traceback",
     "src/openmbb/cli.py",
     '''    except ValueError as e:''',
     '''    except ZeroDivisionError as e:''',
     "tests/test_redact.py::test_cli_redact_refuses_a_non_capture_with_a_message_not_a_traceback"),

    ("redact/gui: stop catching the refusal, so the export silently does nothing",
     "src/openmbb/gui.py",
     '''            except ValueError as e:
                # A refusal about the REQUEST rather than the data: the chosen''',
     '''            except ZeroDivisionError as e:
                # A refusal about the REQUEST rather than the data: the chosen''',
     "tests/test_gui_flow.py::test_the_share_safe_export_says_why_it_refused"),

    ("consent: stop masking the command, only the consent sentence",
     "src/openmbb/transport.py",
     "            self._mask(str(cmd).strip()), self._mask(str(consent).strip()))",
     "            str(cmd).strip(), self._mask(str(consent).strip()))",
     "tests/test_cli_selftest.py::test_the_consent_record_masks_the_command_not_only_the_consent"),

    # item 5 - the two condition surfaces are composed once
    ("mirror: the tab stops showing the coverage limit", "src/openmbb/gui.py",
     "            _cov_note = condition_mod.coverage_note(cov)",
     "            _cov_note = None",
     'tests/test_gui_flow.py::test_the_coverage_limit_reaches_both_surfaces'),

    ("mirror: the saved page stops showing the coverage limit",
     "src/openmbb/report.py",
     "    return _wrap(condition.coverage_note(cov))",
     "    return []",
     'tests/test_gui_flow.py::test_the_coverage_limit_reaches_both_surfaces'),

    ("mirror: the tab stops showing the taper caveat", "src/openmbb/gui.py",
     '''            _taper = condition_mod.taper_note(ch)
            if _taper:
                _row("Charge taper", _taper, "unknown")''',
     "            pass",
     'tests/test_gui_flow.py::test_the_two_condition_surfaces_show_the_same_set_of_facts'),

    ("mirror: the tab re-derives the cell-floor predicate instead of sharing it",
     "src/openmbb/gui.py",
     "            if condition_mod.show_cell_floor(a):",
     "            if floor:",
     'tests/test_gui_flow.py::test_the_two_condition_surfaces_show_the_same_set_of_facts'),
]


# --- the runner --------------------------------------------------------------

#: How each file was actually written, so a restore puts it back the same way.
_NEWLINES = {}

#: pytest's exit codes, named. 0 = all passed, 1 = tests ran and some failed.
#: Everything else (2 interrupted, 3 internal error, 4 usage/"no tests ran",
#: 5 none collected) means the check DID NOT RUN, which is never an outcome.
_PYTEST_PASSED = 0
_PYTEST_FAILED = 1


def _read(rel):
    """The file with LF endings, remembering the endings it arrived with.

    Anchors in the manifest are written LF, so matching happens in LF. But a
    restore that forced CRLF back onto a file that arrived LF would leave a
    dirty tree on any LF checkout - this is a public repo whose CI runs Linux -
    and a dirty tree is precisely the recovery the guard at the top promises.
    """
    with open(os.path.join(REPO, rel), encoding="utf-8", newline="") as f:
        raw = f.read()
    _NEWLINES[rel] = CRLF if CRLF in raw else LF
    return raw.replace(CRLF, LF)


def _write(rel, text):
    nl = _NEWLINES.get(rel, LF)
    with open(os.path.join(REPO, rel), "w", encoding="utf-8", newline="") as f:
        f.write(text if nl == LF else text.replace(LF, nl))


def _edits(label, old, new):
    """The (old, new) pairs for one entry, validated before anything is written.

    zip() is silent about a mismatch: unequal tuples truncate to the shorter one
    and apply HALF a mutation, and a tuple paired with a string zips edits
    against single characters. Either produces a run that reports confidently on
    something other than what the entry describes.
    """
    if isinstance(old, tuple) != isinstance(new, tuple):
        raise ValueError(
            "%s: old and new must both be tuples, or neither" % label)
    if isinstance(old, tuple):
        if len(old) != len(new):
            raise ValueError("%s: %d anchor(s) but %d replacement(s)"
                             % (label, len(old), len(new)))
        return list(zip(old, new))
    return [(old, new)]


def _tree_is_clean():
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=REPO,
                             capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None            # no git here; caller decides
    return out.returncode == 0 and not out.stdout.strip()


def main(argv):
    picked = [m for m in MUTATIONS
              if not argv or any(a.lower() in m[0].lower() for a in argv)]
    if not picked:
        print("no entries match %s" % (argv,))
        return 2

    clean = _tree_is_clean()
    if clean is False:
        print("refusing to run against a dirty working tree.\n"
              "This rewrites files under src/, and a clean tree is what makes\n"
              "`git checkout -- .` a complete recovery if it is interrupted.")
        return 2
    if clean is None:
        print("[warn] could not ask git whether the tree is clean; "
              "an interruption may leave a mutation applied.")

    originals = {}
    for _l, path, _o, _n, _t in picked:
        originals.setdefault(path, _read(path))

    # Validate every entry BEFORE touching a file, so a malformed manifest
    # cannot leave a half-applied mutation behind.
    try:
        plan = [(label, path, _edits(label, old, new), test)
                for label, path, old, new, test in picked]
    except ValueError as e:
        print("malformed manifest entry: %s" % e)
        return 2

    # Every named test must PASS on the clean tree first. An entry whose test is
    # misspelled, renamed, or already failing is otherwise indistinguishable
    # from a perfect catch - and "the check could not run" must never read as
    # one, least of all here.
    ids = sorted({test for _l, _p, _e, test in plan})
    print("prechecking %d test id(s) on the clean tree..." % len(ids))
    unrunnable = {}
    for test in ids:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", test, "-q", "--no-header"],
            cwd=REPO, capture_output=True, text=True, timeout=1800)
        if r.returncode != _PYTEST_PASSED:
            why = ("no such test (pytest exit 4)" if r.returncode == 4
                   else "already failing" if r.returncode == _PYTEST_FAILED
                   else "pytest exit %d" % r.returncode)
            unrunnable[test] = why
            print("  UNRUNNABLE  %s  (%s)" % (test, why))
    if unrunnable:
        print("")
        print("%d test id(s) cannot serve as evidence; fix or re-anchor them "
              "before trusting any result below." % len(unrunnable))

    caught, missed, broken, errored = 0, [], [], []
    try:
        for label, path, edits, test in plan:
            if test in unrunnable:
                errored.append((label, unrunnable[test]))
                print("ERROR     %s" % label)
                continue
            src = originals[path]
            stale = None
            mutated = src
            for o, nw in edits:
                n = mutated.count(o)
                if n != 1:
                    # The source moved out from under the entry. That is not a
                    # pass and not a failure - it is an entry that no longer
                    # means anything, and saying so beats skipping it quietly.
                    stale = "anchor matched %d times" % n
                    break
                mutated = mutated.replace(o, nw)
            if stale:
                broken.append((label, stale))
                print("STALE     %s" % label)
                continue
            _write(path, mutated)
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "pytest", test, "-q", "--no-header"],
                    cwd=REPO, capture_output=True, text=True, timeout=1800)
            finally:
                _write(path, originals[path])
            if r.returncode == _PYTEST_FAILED:
                caught += 1
                print("CAUGHT    %s" % label)
            elif r.returncode == _PYTEST_PASSED:
                missed.append(label)
                print("MISSED !  %s" % label)
                print("          %s stayed green under its own mutation" % test)
            else:
                # Collection error, interrupt, usage error: the test did not
                # run, so nothing was demonstrated either way.
                errored.append((label, "pytest exit %d under mutation"
                                % r.returncode))
                print("ERROR     %s  (pytest exit %d)" % (label, r.returncode))
    finally:
        for path, text in originals.items():
            _write(path, text)

    print("")
    print("%d/%d caught" % (caught, len(plan)))
    if broken:
        print("%d stale entr%s (the code moved; re-anchor or delete):"
              % (len(broken), "y" if len(broken) == 1 else "ies"))
        for label, why in broken:
            print("  - %s (%s)" % (label, why))
    if errored:
        print("%d entr%s proved nothing (the test did not run):"
              % (len(errored), "y" if len(errored) == 1 else "ies"))
        for label, why in errored:
            print("  - %s (%s)" % (label, why))
    if missed:
        print("%d test%s did not notice its own fix being removed:"
              % (len(missed), "" if len(missed) == 1 else "s"))
        for label in missed:
            print("  - %s" % label)
    return 0 if (caught == len(plan) and not broken and not errored) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
