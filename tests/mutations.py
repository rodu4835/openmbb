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

    # Item 21 replaced journal_consent's hand-rolled per-field masking with one
    # masked writer shared by all three record types, so this points at that.
    ("consent: stop masking the consent record on its way to disk",
     "src/openmbb/transport.py",
     '''            f.write(self._mask(line))''',
     '''            f.write(line)''',
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
                shutil.rmtree(work, ignore_errors=True)''',
     '''            except ZeroDivisionError as e:
                shutil.rmtree(work, ignore_errors=True)''',
     "tests/test_gui_flow.py::test_the_share_safe_export_says_why_it_refused"),

    # An entry for "masks the command but not the sentence" was here. Item 21
    # made that unrepresentable: there is one mask over the whole line, so the
    # two halves cannot come apart. The test that named it still runs and is
    # still proven failable, by the entry above.

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

    # item 8 - the skips say what they mean
    ("display: a TclError with a display present becomes a skip again",
     "tests/conftest.py",
     '''        except tk.TclError as second:
            pytest.fail(''',
     '''        except tk.TclError as second:
            pytest.skip(''',
     "tests/test_display_honesty.py::test_a_tclerror_with_a_display_present_fails_rather_than_skips"),

    ("display: the retry is removed, so a known-recoverable build fails",
     "tests/conftest.py",
     '''        gc.collect()
        try:
            built = build()''',
     '''        gc.collect()
        try:
            raise first
            built = build()''',
     "tests/test_display_honesty.py::test_one_retry_is_allowed_because_the_cause_is_known"),

    ("display: require_display skips even when a display exists",
     "tests/conftest.py",
     "    if tk_display is not True:",
     "    if True:",
     "tests/test_display_honesty.py::test_a_display_that_exists_never_skips"),

    # item 5b - the two facts the mirror test could not previously exercise
    ("mirror: the tab drops the time-at-full sentence", "src/openmbb/gui.py",
     "                        condition_mod.full_hold_note(ch)), \"attention\")",
     "                        \"\"), \"attention\")",
     'tests/test_gui_flow.py::test_the_two_condition_surfaces_show_the_same_set_of_facts[holds+floor]'),

    ("mirror: the tab drops the cell-floor row entirely", "src/openmbb/gui.py",
     "            if condition_mod.show_cell_floor(a):",
     "            if False:",
     'tests/test_gui_flow.py::test_the_two_condition_surfaces_show_the_same_set_of_facts[holds+floor]'),

    ("display: the retry stops burying the half-built root",
     "tests/conftest.py",
     '''        orphan = getattr(tk, "_default_root", None)
        if orphan is not None and orphan is not before:''',
     '''        orphan = getattr(tk, "_default_root", None)
        if False:''',
     "tests/test_display_honesty.py::test_a_failed_build_leaves_no_root_behind"),

    # item 6 - the motor controller, read at last
    ("sevcon: let a mode parse as a temperature (drop the excludes)",
     "src/openmbb/parsers.py",
     '''            ("motor_temp_c", ("motor", "temp"), ("max", "age", "control")),''',
     '''            ("motor_temp_c", ("motor", "temp"), ()),''',
     "tests/test_sevcon.py::test_a_mode_is_not_a_temperature"),

    ("sevcon: an unreadable operational flag collapses to False",
     "src/openmbb/parsers.py",
     '''        out["operational"] = True if low.startswith("y") else (
            False if low.startswith("n") else None)''',
     '''        out["operational"] = low.startswith("y")''',
     "tests/test_sevcon.py::test_an_unreadable_operational_flag_is_none_not_false"),

    ("sevcon: a stored fault stops reaching the Health tab",
     "src/openmbb/health.py",
     '''    if sev.get("active_faults") is not None:''',
     '''    if False:''',
     "tests/test_sevcon.py::test_a_stored_fault_reaches_the_health_tab_as_an_alert"),

    # 6c - a green verdict may not hide a red row
    ("beyond-pack: sever the intake, so no note is ever composed",
     "src/openmbb/condition.py",
     '''        if m.get("label") not in _BEYOND_PACK_LABELS:''',
     '''        if True:''',
     "tests/test_beyond_pack.py::test_both_fault_classes_produce_a_note"),

    ("beyond-pack: cover only the Sevcon label, leaving OBD invisible",
     "src/openmbb/condition.py",
     '''_BEYOND_PACK_LABELS = ("Fault codes", "Sevcon faults")''',
     '''_BEYOND_PACK_LABELS = ("Sevcon faults",)''',
     "tests/test_beyond_pack.py::test_both_fault_classes_produce_a_note"),

    # The level must NOT move. A pack verdict that went amber because the motor
    # controller has a fault would be lying about the pack.
    ("beyond-pack: let a note escalate the pack verdict's level",
     "src/openmbb/condition.py",
     '''    # 4. preconditions that weaken whatever the checks said''',
     '''    for _note in beyond_pack_notes(metrics):
        add("Beyond the pack", "concern", _note)

    # 4. preconditions that weaken whatever the checks said''',
     "tests/test_beyond_pack.py::test_the_verdict_level_is_not_moved_by_a_note"),

    ("beyond-pack: the saved page stops printing the notes",
     "src/openmbb/report.py",
     '''    for note in v.get("beyond_pack") or []:''',
     '''    for note in []:''',
     'tests/test_gui_flow.py::test_a_fault_the_pack_verdict_ignores_reaches_both_surfaces'),

    # 6d's local hex guard was retired by item 13, which moved the refusal into
    # `num` for every parser. The runner reported this entry MISSED - removing
    # the local copy changed nothing, because the shared guard already caught
    # it. The protection it stood for is now entry "num: accept a hex prefix
    # again", and test_a_hex_fault_count_is_unreadable_not_zero still guards
    # the behaviour end to end.

    # item 15 - the write path says what happened, not what it asked for
    # Item 9 moved the write path's reply parsing down to the transport.
    ("write: discard the console reply again, so a refusal cannot be quoted",
     "src/openmbb/transport.py",
     '''        said_kind, said = console_write_result(reply)''',
     '''        said_kind, said = None, None''',
     'tests/test_gui_flow.py::test_a_refused_write_quotes_the_console_instead_of_contradicting_it'),

    ("write: stop parsing SUCCESS/FAILED out of the console's reply",
     "src/openmbb/parsers.py",
     '''    for kind, rx in (("failed", _WRITE_FAIL_RE), ("success", _WRITE_OK_RE)):''',
     '''    for kind, rx in ():''',
     'tests/test_gui_flow.py::test_a_refused_write_quotes_the_console_instead_of_contradicting_it'),

    ("write: stop diffing the dumps, so collateral changes go unreported",
     "src/openmbb/gui.py",
     '''                    moved = parsers.settings_diff(live_before, live2,
                                                 ignore=(name,))''',
     '''                    moved = []''',
     'tests/test_gui_flow.py::test_a_write_that_moves_other_settings_says_so'),

    # item 15b - the review of 15 found the same sin one layer down
    ("journal: attribute our own annotation to the console again",
     "src/openmbb/gui.py",
     '''                        self.logger.journal_observed(
                            mname, mold, mnew,
                            "moved by the %s write" % name)''',
     '''                        self.logger.journal_write(
                            mname, mold, mnew, None,
                            said="moved by the %s write" % name)''',
     'tests/test_gui_flow.py::test_a_write_that_moves_other_settings_says_so'),

    ("journal: let an observed change wear the console-said label",
     "src/openmbb/transport.py",
     '        self._journal("%s | %s | %s -> %s | OBSERVED (not requested) - %s\\n" % (',
     '        self._journal("%s | %s | %s -> %s | PENDING [console said: %s]\\n" % (',
     'tests/test_gui_flow.py::test_the_journal_never_puts_our_words_in_the_console_s_mouth'),

    ("journal: report an empty read-back as a value the bike reports",
     "src/openmbb/transport.py",
     '''            if not str(got).strip():''',
     '''            if False:''',
     'tests/test_gui_flow.py::test_a_read_back_that_did_not_return_is_not_a_value_the_bike_reports'),

    ("write: describe a read-back that never returned as a bike action",
     "src/openmbb/gui.py",
     '''                    unknown = not str(got).strip()''',
     '''                    unknown = False''',
     'tests/test_gui_flow.py::test_an_empty_read_back_is_reported_as_unknown_not_as_a_bike_action'),

    # item 19 - the coast-regen refusal was folklore
    ("coast regen: restore the unsourced refusal of 0",
     "src/openmbb/safety.py",
     "    return _v_int_range(0, 100)(v)",
     '''    ok, msg = _v_int_range(0, 100)(v)
    if ok and int(str(v).strip()) == 0:
        return False, "refused"
    return ok, msg''',
     "tests/test_safety_transport.py::test_validators"),

    ("coast regen: drop the warning, so 0 passes unremarked",
     "src/openmbb/safety.py",
     '''                   if str(v).strip() == "0" else None)),''',
     '''                   if False else None)),''',
     "tests/test_safety_transport.py::test_validators"),

    ("coast regen: let the removed fishtail claim back into shipped text",
     "README.md",
     "- Validators: coast regen accepts 0-100;",
     "- Validators: coast regen of exactly 0 is refused (fishtail risk);",
     "tests/test_safety_transport.py::test_no_shipped_text_still_claims_the_coast_regen_fishtail_hazard"),

    # item 20 - the headline names its driver and counts what it could not answer
    ("headline: stop naming the check that earned the verdict",
     "src/openmbb/condition.py",
     '''    named = [_label(c) for c in (checks or []) if c.get("level") == level]''',
     '''    named = []''',
     "tests/test_condition.py::test_the_headline_names_the_check_that_earned_it"),

    ("headline: drop the unanswered count from the harsh branches",
     "src/openmbb/condition.py",
     '''        missing = (" (%d of %d pack checks unanswered)"
                   % (total - answered, total))''',
     '''        missing = ""''',
     "tests/test_condition.py::test_the_harsh_branches_carry_the_unanswered_count"),

    # item 20b - what the stack review found
    ("assets: put the fishtail refusal back into the write confirm dialog",
     "src/openmbb/assets/write_options_help.json",
     '''"caution": "0% disables off-throttle regen completely''',
     '''"caution": "Exactly 0% is refused by the app (fishtail risk). 0% disables off-throttle regen completely''',
     "tests/test_safety_transport.py::test_no_shipped_text_still_claims_the_coast_regen_fishtail_hazard"),

    ("headline: let the Warning check name itself instead of its message",
     "src/openmbb/condition.py",
     '''        if c.get("name") == "Warning" and str(c.get("detail") or "").strip():''',
     '''        if False:''',
     "tests/test_condition.py::test_a_warning_driver_names_the_warning_not_the_word"),

    ("write: claim no harm done over a write that moved other settings",
     "src/openmbb/gui.py",
     '''                            harm = ("" if moved else''',
     '''                            harm = ("" if False else''',
     "tests/test_gui_flow.py::test_a_clamped_write_that_moved_other_settings_claims_no_harm_done"),

    # items 17-18 - what read badly at an actual motorcycle
    ("port: Refresh fills the list and leaves the field blank again",
     "src/openmbb/gui.py",
     '''            if len(ports) == 1 and not (self.port_var.get() or "").strip():
                self.port_var.set(ports[0])''',
     '''            if False:
                self.port_var.set(ports[0])''',
     "tests/test_gui_flow.py::test_refresh_selects_a_lone_port_when_nothing_is_chosen"),

    ("simulator: the title bar stops saying it is not your bike",
     "src/openmbb/gui.py",
     '''                              "   [SIMULATOR — NOT YOUR BIKE]" if on else ""))''',
     '''                              ""))''',
     "tests/test_gui_flow.py::test_the_title_bar_says_when_it_is_the_simulator"),

    ("writes: offer a setting the firmware is known to refuse",
     "src/openmbb/gui.py",
     '''            if name in REFUSED_ON_REV41 and _parse_fw_rev(self.version_text) == 41:''',
     '''            if False:''',
     "tests/test_gui_flow.py::test_a_setting_the_firmware_refuses_is_not_offered_as_a_write"),

    # item 18b - the gate's back door and the launch tag
    ("writes: move the firmware-refusal gate back to staging only",
     "src/openmbb/gui.py",
     '''            if (name in REFUSED_ON_REV41
                    and _parse_fw_rev(self.version_text) == 41):''',
     '''            if False:''',
     "tests/test_gui_flow.py::test_the_refused_setting_gate_has_no_back_door"),

    ("simulator: stop painting the badge at launch",
     "src/openmbb/gui.py",
     '''            self.after(0, self._refresh_sim_badge)''',
     '''            pass''',
     "tests/test_gui_flow.py::test_launching_in_simulator_mode_says_so_without_touching_the_toggle"),

    ("port: let the shared rule overrule a port already chosen",
     "src/openmbb/gui.py",
     '''            if len(ports) == 1 and not (self.port_var.get() or "").strip():''',
     '''            if len(ports) == 1:''',
     "tests/test_gui_flow.py::test_every_refresh_button_uses_the_same_lone_port_rule"),

    # item 24 - provenance in the meta file, the folder name out of the report
    ("report: print the folder's own name again",
     "src/openmbb/report.py",
     '''        "  capture  : %s" % ident,''',
     '''        "  capture  : %s" % session.name,''',
     "tests/test_report.py::test_the_report_never_prints_the_folders_own_name"),

    ("report: drop the not-a-motorcycle banner",
     "src/openmbb/report.py",
     '''    if banner:''',
     '''    if False:''',
     "tests/test_report.py::"
     "test_a_simulator_capture_says_so_before_the_reader_reaches_a_number"),

    ("sessions: let the folder name outrank the meta file",
     "src/openmbb/sessions.py",
     '''    m = _SOURCE_RE.search(_meta_text(folder))
    if m and m.group(1).strip():
        return m.group(1).strip()''',
     '''    m = None''',
     "tests/test_capture_format.py::test_the_meta_file_outranks_the_folder_name"),

    ("sessions: stop tolerating the collision suffix on a name tag",
     "src/openmbb/sessions.py",
     '''_NOT_A_BIKE_RE = re.compile(r"_(sim|listen|selftest)(?:_\\d+)?$")''',
     '''_NOT_A_BIKE_RE = re.compile(r"_(sim|listen|selftest)$")''',
     "tests/test_capture_format.py::"
     "test_a_collision_suffix_does_not_un_mark_a_simulator"),

    ("sessions: read silence as a bike rather than as nothing recorded",
     "src/openmbb/sessions.py",
     '''    return None


def not_from_a_bike(folder):''',
     '''    return "serial"


def not_from_a_bike(folder):''',
     "tests/test_capture_format.py::"
     "test_silence_is_not_a_claim_about_where_it_came_from"),

    ("transport: put the collision counter back after the tag",
     "src/openmbb/transport.py",
     '''                path, n = (os.path.join(root, "%s_%d_%s" % (stamp, n, tag)),
                           n + 1)''',
     '''                path, n = "%s_%d" % (base, n), n + 1''',
     "tests/test_capture_format.py::"
     "test_a_same_microsecond_collision_keeps_the_tag_at_the_end"),

    ("gui: stop recording what the session was captured from",
     "src/openmbb/gui.py",
     '''            if port is not None:''',
     '''            if False:''',
     "tests/test_gui_flow.py::test_the_session_meta_records_what_it_was_captured_from"),

    # item 23a - the export shows its work instead of asking to be trusted
    ("export: name the bundle after the source folder again",
     "src/openmbb/gui.py",
     '''                initialfile="openmbb-share-%s_REDACTED.zip" % stamp,''',
     '''                initialfile=os.path.basename(src) + "-shared.zip",''',
     "tests/test_gui_flow.py::"
     "test_the_export_writes_one_zip_and_nothing_beside_the_capture"),

    ("export: build the bundle beside the capture again",
     "src/openmbb/gui.py",
     '''            work = tempfile.mkdtemp(prefix="openmbb-share-")
            dst = os.path.join(work, "openmbb-share-%s_REDACTED" % stamp)''',
     '''            work = tempfile.mkdtemp(prefix="openmbb-share-")
            dst = src + "-shared"''',
     "tests/test_gui_flow.py::"
     "test_the_export_writes_one_zip_and_nothing_beside_the_capture"),

    ("export: let a refusal fall through to the save dialog",
     "src/openmbb/gui.py",
     '''                messagebox.showerror(
                    APP_NAME, "Nothing was written.\\n\\n%s" % e)
                return
            except OSError as e:''',
     '''                messagebox.showerror(
                    APP_NAME, "Nothing was written.\\n\\n%s" % e)
            except OSError as e:''',
     "tests/test_gui_flow.py::test_a_refused_export_never_asks_where_to_save"),

    ("diff: show only the files that changed",
     "src/openmbb/gui.py",
     '''            names = sorted(pairs or [],
                           key=lambda pr: (pr[0] != "session_note.txt",
                                           pr[0].lower()))''',
     '''            names = sorted(pairs or [],
                           key=lambda pr: pr[0].lower())[1:]''',
     "tests/test_gui_flow.py::"
     "test_the_diff_viewer_shows_every_file_with_the_note_first"),

    ("dialog: go back to naming only what was removed",
     "src/openmbb/gui.py",
     '''                text="Your own note, the settings dump, your machine's clock, and "
                     "the full event log \u2014 when you rode, how far, when you "''',
     '''                text="Every file was re-scanned and carries no VIN or "
                     "serial number. "''',
     "tests/test_gui_flow.py::"
     "test_the_success_dialog_says_what_remains_not_just_what_was_removed"),

    ("redact: stop reporting which source file became which",
     "src/openmbb/redact.py",
     '''        pairs.append((name, out_name))''',
     '''        pass''',
     "tests/test_redact.py::"
     "test_the_report_pairs_each_source_file_with_what_it_became"),

    # item 10 - one decoder for everything that reads a capture
    ("sessions: put the loaders back on a UTF-8-only read",
     "src/openmbb/sessions.py",
     '''    text, _enc = redact.decode_text(raw)
    return text if text is not None else raw.decode("utf-8", errors="replace")''',
     '''    return raw.decode("utf-8", errors="replace")''',
     "tests/test_capture_format.py::"
     "test_a_utf16_capture_still_has_its_commands"),

    ("sessions: read the format stamp UTF-8-only again",
     "src/openmbb/sessions.py",
     '''    text = read_text(os.path.join(folder, "session_meta.txt"))''',
     '''    try:
        with open(os.path.join(folder, "session_meta.txt"), encoding="utf-8",
                  errors="replace") as f:
            text = f.read()
    except OSError:
        text = None''',
     "tests/test_capture_format.py::"
     "test_a_utf16_stamp_from_the_future_is_refused_not_read_as_absent"),

    ("sessions: drop the lenient fallback a stray byte needs",
     "src/openmbb/sessions.py",
     '''    return text if text is not None else raw.decode("utf-8", errors="replace")''',
     '''    return text''',
     "tests/test_capture_format.py::"
     "test_one_stray_byte_does_not_cost_the_whole_capture"),

    # item 13 - num() and the hex prefix
    ("num: accept a hex prefix again",
     "src/openmbb/parsers.py",
     '''    if end < len(s) and s[end] in "xX" and m.group(0).lstrip("-+") == "0":
        return None''',
     '''    if False:
        return None''',
     "tests/test_sevcon.py::"
     "test_num_refuses_a_hex_token_everywhere_not_just_in_this_parser"),

    ("num: take the head of a split digit run again",
     "src/openmbb/parsers.py",
     '''        if tail[:1].isspace() and tail.lstrip()[:1].isdigit():
            return None''',
     '''        if False:
            return None''',
     "tests/test_sevcon.py::"
     "test_num_refuses_a_split_digit_run_the_way_a_unit_number_does"),

    ("num: refuse the decimal that precedes a hex note too",
     "src/openmbb/parsers.py",
     '''    if end < len(s) and s[end] in "xX" and m.group(0).lstrip("-+") == "0":''',
     '''    if "0x" in s.lower():''',
     "tests/test_sevcon.py::"
     "test_num_still_reads_the_decimal_that_comes_before_a_hex_note"),

    # item 14 - the simulator's sevcon block
    ("sim: go back to the sevcon block the parser cannot read",
     "src/openmbb/sim.py",
     '''  - Number of Faults          :    0""" % _SIM_SEVCON_ODO_KM''',
     '''  Faults              : 0""" % _SIM_SEVCON_ODO_KM''',
     "tests/test_sevcon.py::"
     "test_the_simulator_shows_the_sevcon_feature_it_exists_to_demo"),

    ("sim: let the simulated odometer contradict the measured ratio",
     "src/openmbb/sim.py",
     '''_SIM_SEVCON_ODO_KM = 14619.4''',
     '''_SIM_SEVCON_ODO_KM = 6155.0''',
     "tests/test_sevcon.py::"
     "test_the_simulated_odometer_reproduces_the_measured_ratio"),

    # item 11 - a refused capture is named wherever it is absent
    ("library: swallow a refused capture again",
     "src/openmbb/library.py",
     '''        except sessions.CaptureFormatError:
            if refused is not None:
                refused.append(name)
            continue''',
     '''        except sessions.CaptureFormatError:
            continue''',
     "tests/test_library.py::test_scan_names_a_capture_it_must_not_read"),

    ("library window: drop the line naming what was not listed",
     "src/openmbb/gui.py",
     '''            if refused:
                # named on the window, not counted in silence: the list would
                # otherwise read as the whole library''',
     '''            if False:
                # named on the window, not counted in silence: the list would
                # otherwise read as the whole library''',
     "tests/test_gui_flow.py::"
     "test_the_library_lists_what_it_has_and_names_what_it_could_not_read"),

    ("library window: call an all-refused folder empty again",
     "src/openmbb/gui.py",
     '''                if refused:
                    messagebox.showinfo(
                        APP_NAME,
                        "%d capture%s in:''',
     '''                if False:
                    messagebox.showinfo(
                        APP_NAME,
                        "%d capture%s in:''',
     "tests/test_gui_flow.py::"
     "test_the_library_says_a_capture_is_missing_rather_than_looking_empty"),

    ("trend: swallow a refused capture again",
     "src/openmbb/gui.py",
     '''                    except sessions.CaptureFormatError:
                        # counted, not swallowed: a trend line with an
                        # undisclosed hole in it is worse than one that says a
                        # capture is missing
                        refused.append(name)''',
     '''                    except sessions.CaptureFormatError:
                        pass''',
     "tests/test_gui_flow.py::"
     "test_the_trend_loader_counts_what_it_could_not_read"),

    ("chart: stop naming the captures the trend left out",
     "src/openmbb/gui.py",
     '''                note += " · %d not shown (unreadable stamp)" % len(refused)''',
     '''                pass''',
     "tests/test_gui_flow.py::"
     "test_the_chart_caption_names_the_captures_it_left_out"),

    ("chart: let an all-refused chart read as no data",
     "src/openmbb/gui.py",
     '''                if refused:
                    self._chart_msg(''',
     '''                if False:
                    self._chart_msg(''',
     "tests/test_gui_flow.py::test_a_chart_with_nothing_left_to_draw_says_why"),

    # item 12 - every session states its format from the moment it exists
    ("sessions: leave listen and selftest captures unstamped again",
     "src/openmbb/transport.py",
     '''        self._write_base_meta()''',
     '''        pass''',
     "tests/test_capture_format.py::"
     "test_every_session_states_its_format_from_the_moment_it_exists"),

    # item 9 - the record follows the enforcement down to the transport
    ("transport: journal a write only from the GUI again",
     "src/openmbb/transport.py",
     '''        self.logger.journal_write(name, old, value, ok=None)      # PENDING''',
     '''        pass''',
     "tests/test_safety_transport.py::"
     "test_a_headless_write_is_journalled_without_a_gui"),

    ("transport: stop handing back the read-back it performed",
     "src/openmbb/transport.py",
     '''        self.last_write = {"name": name, "old": old, "new": value,''',
     '''        self.last_write = None or {"name": name, "old": old, "new": None and value,''',
     "tests/test_safety_transport.py::"
     "test_the_transport_hands_back_the_read_back_it_already_did"),

    ("transport: believe the console instead of the read-back",
     "src/openmbb/transport.py",
     '''        verified = first_number(got) == first_number(value)''',
     '''        verified = said_kind != "failed"''',
     "tests/test_safety_transport.py::"
     "test_a_silent_clamp_is_caught_at_the_transport"),

    # item 21 - one masking rule, one writer
    ("journal: stop masking the record on its way to disk",
     "src/openmbb/transport.py",
     '''            f.write(self._mask(line))''',
     '''            f.write(line)''',
     "tests/test_safety_transport.py::"
     "test_every_journal_record_type_masks_a_registered_secret"),

    # item 18c - the tag asks what it is connected to
    ("gui: let the simulator tag follow the checkbox again",
     "src/openmbb/gui.py",
     '''            port = getattr(getattr(self, "transport", None), "port", None)
            if port is not None and self.connected:
                return isinstance(port, SimPort)
            return bool(self.sim_var.get())''',
     '''            return bool(self.sim_var.get())''',
     "tests/test_gui_flow.py::"
     "test_the_simulator_tag_follows_the_connection_not_the_checkbox"),

    # item 22 - a scorecard whose total does not move with the bike
    ("verdict: count the conditional rows in the denominator again",
     "src/openmbb/condition.py",
     '''    scored = [c for c in checks if c["name"] in PACK_CHECKS]''',
     '''    scored = list(checks)''',
     "tests/test_condition.py::"
     "test_the_unanswered_fraction_does_not_move_with_the_bike"),

    ("verdict: drop a fired conditional row out of the verdict entirely",
     "src/openmbb/condition.py",
     '''    level = "unknown"
    for c in checks:
        if _RANK[c["level"]] > _RANK[level]:
            level = c["level"]''',
     '''    level = "unknown"
    for c in scored:
        if _RANK[c["level"]] > _RANK[level]:
            level = c["level"]''',
     "tests/test_condition.py::"
     "test_a_fired_conditional_row_is_named_rather_than_counted"),

    # item 7 - the clamping port, and the seam it needs
    ("sim: store the asked-for value even when the console clamps",
     "src/openmbb/sim.py",
     '''                    self._respond(self._apply_write(name, val))''',
     '''                    self._settings[name][1] = val
                    self._respond("  %s set to %s" % (name, val))''',
     "tests/test_safety_transport.py::"
     "test_a_silent_clamp_is_caught_at_the_transport"),

    # item 25 - the stack review's fix pass
    ("25a: stop repainting the simulator tag on connect",
     "src/openmbb/gui.py",
     '''                self._refresh_sim_badge()
                self._probe_log("PROMPT OK — connected.\\n")''',
     '''                self._probe_log("PROMPT OK — connected.\\n")''',
     "tests/test_gui_flow.py::"
     "test_the_tag_repaints_when_the_connection_changes"),

    ("25b: let a refused write leave its PENDING dangling",
     "src/openmbb/transport.py",
     '''        except BlockedCommandError as e:
            self.logger.journal_refused(name, old, value, e)
            raise''',
     '''        except BlockedCommandError:
            raise''',
     "tests/test_safety_transport.py::"
     "test_a_refused_write_closes_its_own_pending_line"),

    ("25c: guess at utf-16 without a BOM again",
     "src/openmbb/redact.py",
     '''        if enc == "utf-16" and not raw.startswith(_UTF16_BOMS):
            continue''',
     '''        if False:
            continue''',
     "tests/test_capture_format.py::"
     "test_a_damaged_claim_is_refused_rather_than_read_as_absent"),

    ("25d: filter the trend by folder name again",
     "src/openmbb/gui.py",
     '''                    if sessions.not_from_a_bike(folder):''',
     '''                    if name.endswith(("_sim", "_listen")):''',
     "tests/test_gui_flow.py::"
     "test_the_trend_charts_exclude_simulator_data_the_way_everything_else_does"),

    ("25e: let the diff viewer outlive the copy it reads",
     "src/openmbb/gui.py",
     '''                diff = getattr(self, "_diff_win", None)
                if diff is not None:''',
     '''                diff = getattr(self, "_diff_win", None)
                if False:''',
     "tests/test_gui_flow.py::"
     "test_the_diff_viewer_does_not_outlive_the_copy_it_reads"),

    ("25f: drop the clock from the pull path's rewritten stamp",
     "src/openmbb/gui.py",
     '''                    "time: %s" % _dt.datetime.now().isoformat(timespec="seconds"),''',
     '''                    "app_version: %s" % __version__,''',
     "tests/test_gui_flow.py::"
     "test_the_pull_path_rewrite_keeps_every_key_the_base_stamp_established"),

    ("25 minor: save the bundle inside the capture again",
     "src/openmbb/gui.py",
     '''            if redact_mod.same_or_inside(zip_path, src):''',
     '''            if False:''',
     "tests/test_gui_flow.py::"
     "test_the_export_refuses_a_destination_inside_the_capture"),

    ("25 minor: stop scanning the chosen file name for identifiers",
     "src/openmbb/gui.py",
     '''            leaks = redact_mod.find_pii_shapes(os.path.basename(zip_path))''',
     '''            leaks = []''',
     "tests/test_gui_flow.py::"
     "test_the_export_refuses_a_file_name_carrying_an_identifier"),

    # item 26 - a spliced line is not a reading
    ("26: read a spliced record as though it were data",
     "src/openmbb/parsers.py",
     '''        if _line_is_spliced(line):''',
     '''        if False:''',
     "tests/test_sevcon.py::"
     "test_a_spliced_ride_record_is_refused_in_every_field"),

    ("26: refuse every record, damaged or not",
     "src/openmbb/parsers.py",
     '''        if len(re.findall(pat, low)) > 1:''',
     '''        if len(re.findall(pat, low)) > 0:''',
     "tests/test_sevcon.py::test_an_undamaged_record_is_untouched"),

    ("26: blame the firmware for a damaged capture",
     "src/openmbb/condition.py",
     '''    if spliced and total:''',
     '''    if False:''',
     "tests/test_sevcon.py::"
     "test_the_coverage_note_names_the_damage_and_its_remedy"),
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
