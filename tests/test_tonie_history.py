from __future__ import annotations

import pytest

from app import db, tonie_history


@pytest.fixture
def database():
    db.init()


def chapters(*pairs, seconds=60.0):
    return [{"id": i, "title": t, "seconds": seconds} for i, t in pairs]


def test_first_list_is_stored_even_when_empty(database):
    assert db.record_tonie_version("h", "t", "Bear", [], "seen", 1.0) is True
    assert [v["chapters"] for v in db.tonie_versions("h", "t")] == [[]]


def test_identical_list_is_skipped(database):
    db.record_tonie_version("h", "t", "Bear", chapters(("a", "One")), "seen", 1.0)
    assert db.record_tonie_version("h", "t", "Bear", chapters(("a", "One")), "toniefi", 2.0) is False
    assert len(db.tonie_versions("h", "t")) == 1


def test_seconds_only_change_is_skipped(database):
    db.record_tonie_version("h", "t", "Bear", chapters(("a", "One"), seconds=0.0), "seen", 1.0)
    assert db.record_tonie_version("h", "t", "Bear", chapters(("a", "One"), seconds=312.0), "seen", 2.0) is False


def test_rename_and_reorder_are_stored(database):
    db.record_tonie_version("h", "t", "Bear", chapters(("a", "One"), ("b", "Two")), "seen", 1.0)
    assert db.record_tonie_version("h", "t", "Bear", chapters(("a", "Uno"), ("b", "Two")), "toniefi", 2.0)
    assert db.record_tonie_version("h", "t", "Bear", chapters(("b", "Two"), ("a", "Uno")), "toniefi", 3.0)
    assert [v["source"] for v in db.tonie_versions("h", "t")] == ["seen", "toniefi", "toniefi"]


def test_tonies_are_kept_apart(database):
    db.record_tonie_version("h1", "t", "A", chapters(("a", "One")), "seen", 1.0)
    assert db.record_tonie_version("h2", "t", "B", chapters(("a", "One")), "seen", 1.0) is True
    assert db.record_tonie_version("h1", "u", "C", chapters(("a", "One")), "seen", 1.0) is True
    assert len(db.tonie_versions("h1", "t")) == 1


def test_changes_first():
    assert tonie_history.changes(None, chapters(("a", "One"))) == {
        "first": True, "added": 0, "removed": 0, "renamed": 0, "reordered": False}


def test_changes_counts_by_id():
    before = chapters(("a", "One"), ("b", "Two"), ("c", "Three"))
    after = chapters(("c", "Three"), ("a", "Uno"), ("d", "Four"), ("e", "Five"))
    assert tonie_history.changes(before, after) == {
        "first": False, "added": 2, "removed": 1, "renamed": 1, "reordered": True}


def test_removal_alone_is_not_a_reorder():
    before = chapters(("a", "One"), ("b", "Two"), ("c", "Three"))
    after = chapters(("a", "One"), ("c", "Three"))
    assert tonie_history.changes(before, after)["reordered"] is False


def test_versions_are_newest_first_with_durations(database):
    db.record_tonie_version("h", "t", "Bear", chapters(("a", "One")), "seen", 1.0)
    db.record_tonie_version("h", "t", "Bear", chapters(("a", "One"), ("b", "Two")), "toniefi", 2.0)
    out = tonie_history.versions("h", "t")
    assert [v["source"] for v in out] == ["toniefi", "seen"]
    assert out[0]["changes"]["added"] == 1
    assert out[1]["changes"]["first"] is True
    assert out[0]["chapters"][0]["duration"]  # human_duration of 60s, non-empty


def test_versions_for_unknown_tonie_is_empty(database):
    assert tonie_history.versions("h", "nope") == []


def test_remember_normalises_a_raw_cloud_tonie(database):
    raw = {"id": "t", "name": "Bear", "chapters": [
        {"id": "a", "title": "One", "seconds": 5, "file": "x", "transcoding": False}]}
    tonie_history.remember("h", "t", raw, "seen")
    assert db.tonie_versions("h", "t")[0]["chapters"] == [{"id": "a", "title": "One", "seconds": 5.0}]


def test_remember_never_raises(monkeypatch, database):
    def boom(*_, **__):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(db, "record_tonie_version", boom)
    tonie_history.remember("h", "t", {"chapters": []}, "toniefi")  # no exception
