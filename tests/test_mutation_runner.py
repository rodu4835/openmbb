"""The mutation runner's own evidence standard.

tests/mutations.py certifies every other guard in this project, so the one thing
it must never do is report a check that DID NOT RUN as a catch. It did: pytest
exits 4 for "no tests ran", and the runner counted any nonzero exit as success,
so an entry whose test id was misspelled - or renamed by a later refactor -
scored a perfect CAUGHT for ever.
"""

import importlib.util
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _runner():
    spec = importlib.util.spec_from_file_location(
        "openmbb_mutations", os.path.join(REPO, "tests", "mutations.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_test_id_that_does_not_exist_is_an_error_not_a_catch(monkeypatch):
    m = _runner()
    real = m.MUTATIONS[0]
    monkeypatch.setattr(m, "_tree_is_clean", lambda: True)
    monkeypatch.setattr(m, "MUTATIONS", [
        (real[0], real[1], real[2], real[3],
         "tests/test_redact.py::test_this_test_was_renamed_last_year")])
    assert m.main([]) != 0          # never a clean run


def test_a_mismatched_multi_edit_entry_is_refused_before_anything_is_written(
        monkeypatch):
    """zip() truncates a mismatched pair silently, applying HALF a mutation and
    then reporting on it - so the entry is validated before a file is touched."""
    m = _runner()
    real = m.MUTATIONS[0]
    monkeypatch.setattr(m, "_tree_is_clean", lambda: True)
    monkeypatch.setattr(m, "MUTATIONS", [
        ("mismatched", real[1], (real[2], "second anchor"), real[3], real[4])])
    assert m.main([]) == 2


def test_edits_validation_accepts_the_shapes_the_manifest_actually_uses():
    m = _runner()
    assert m._edits("x", "a", "b") == [("a", "b")]
    assert m._edits("x", ("a", "c"), ("b", "d")) == [("a", "b"), ("c", "d")]
    with pytest.raises(ValueError):
        m._edits("x", ("a", "c"), "b")
    with pytest.raises(ValueError):
        m._edits("x", ("a", "c"), ("b",))


def test_the_manifest_is_not_empty_and_every_entry_is_well_formed():
    """A manifest that quietly emptied would report 0/0 caught and exit 0."""
    m = _runner()
    assert len(m.MUTATIONS) >= 18
    for label, path, old, new, test in m.MUTATIONS:
        assert os.path.isfile(os.path.join(REPO, path)), path
        assert test.startswith("tests/") and "::" in test, test
        m._edits(label, old, new)       # raises if malformed
