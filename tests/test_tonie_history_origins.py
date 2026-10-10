from __future__ import annotations

import shutil

import pytest

from app import config, db, library, tonie_history


@pytest.fixture
def database():
    db.init()


def send(slug, names, titles, status="done"):
    job = db.create_job("push", "send", {"sources": [{"slug": slug, "files": names}]})
    db.update_job(job, status=status, result={"uploaded": [{"title": t} for t in titles]})


def record(chapter_list, at):
    db.record_tonie_version("h", "t", "Bear", chapter_list, "toniefi", at)


def ch(i, title):
    return {"id": i, "title": title, "seconds": 60.0}


def test_title_match_names_the_collection(database):
    slug = library.create("VeggieTales")
    send(slug, ["a.mp3"], ["One"])
    record([ch("a", "One"), ch("b", "Mystery")], 1.0)
    chapters = tonie_history.versions("h", "t")[0]["chapters"]
    assert chapters[0]["collection"] == {"slug": slug, "title": "VeggieTales"}
    assert chapters[1]["collection"] is None


def test_renamed_chapter_keeps_its_origin(database):
    slug = library.create("VeggieTales")
    send(slug, ["a.mp3"], ["One"])
    record([ch("a", "One")], 1.0)
    record([ch("a", "Uno")], 2.0)
    newest = tonie_history.versions("h", "t")[0]["chapters"][0]
    assert newest["title"] == "Uno"
    assert newest["collection"]["slug"] == slug


def test_latest_send_wins(database):
    first = library.create("First")
    second = library.create("Second")
    send(first, ["a.mp3"], ["One"])
    send(second, ["a.mp3"], ["One"])
    record([ch("a", "One")], 1.0)
    assert tonie_history.versions("h", "t")[0]["chapters"][0]["collection"]["slug"] == second


def test_failed_push_is_ignored(database):
    slug = library.create("VeggieTales")
    send(slug, ["a.mp3"], ["One"], status="failed")
    record([ch("a", "One")], 1.0)
    assert tonie_history.versions("h", "t")[0]["chapters"][0]["collection"] is None


def test_unpairable_job_is_skipped(database):
    slug = library.create("VeggieTales")
    send(slug, ["a.mp3", "b.mp3"], ["One"])
    assert db.sent_titles() == {}


def test_deleted_collection_falls_back_to_slug(database):
    slug = library.create("VeggieTales")
    send(slug, ["a.mp3"], ["One"])
    shutil.rmtree(config.LIBRARY_DIR / slug)
    record([ch("a", "One")], 1.0)
    assert tonie_history.versions("h", "t")[0]["chapters"][0]["collection"] == {"slug": slug, "title": slug}
