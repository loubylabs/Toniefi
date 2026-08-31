"""The work cart's dismissal set: what it accepts, and what it forgets.

Dismissal hides a finished row from the Desk. It deletes nothing: the job keeps
its place in Activity with its real error, and the collection keeps its place in
the Library. The only durable state is this small key set, and it is capped, so
a Desk that has hidden thousands of rows still stores a bounded amount.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import config, db, main


@pytest.fixture
def isolated_desk(monkeypatch, tmp_path):
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "toniefi.db")
    config.ensure_dirs()
    db.init()
    yield
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


@pytest.fixture
def client(isolated_desk) -> TestClient:
    return TestClient(main.app)


def test_dismissed_keys_survive_the_round_trip(client):
    posted = client.post(
        "/api/desk/dismissals",
        json={"keys": ["job-7", "collection-a-story"]},
    )
    assert posted.status_code == 200
    assert sorted(posted.json()) == ["collection-a-story", "job-7"]

    listed = client.get("/api/desk/dismissals")
    assert listed.status_code == 200
    assert sorted(listed.json()) == ["collection-a-story", "job-7"]
    assert all(isinstance(value, float) for value in listed.json().values())


def test_the_dismissal_set_starts_empty(client):
    assert client.get("/api/desk/dismissals").json() == {}


@pytest.mark.parametrize(
    "key",
    [
        "",
        "job",
        "job-",
        "job-abc",
        "job-1.5",
        "job--1",
        "job-١٢",
        "collection-",
        "collection-../escape",
        "collection-a/b",
        "collection-.toniefi-slug-secret",
        "tonie-42",
        "collection",
    ],
)
def test_a_key_the_work_cart_cannot_build_is_refused(client, key):
    response = client.post("/api/desk/dismissals", json={"keys": [key]})

    assert response.status_code == 400
    assert client.get("/api/desk/dismissals").json() == {}


def test_a_refused_batch_dismisses_nothing(client):
    response = client.post(
        "/api/desk/dismissals",
        json={"keys": ["job-7", "collection-../escape"]},
    )

    assert response.status_code == 400
    assert client.get("/api/desk/dismissals").json() == {}


def test_an_empty_batch_is_refused(client):
    assert client.post("/api/desk/dismissals", json={"keys": []}).status_code == 422


def test_a_batch_past_the_cap_is_refused(client):
    keys = [f"job-{number}" for number in range(db.DESK_DISMISSAL_LIMIT + 1)]

    assert client.post("/api/desk/dismissals", json={"keys": keys}).status_code == 422


def test_dismissing_again_moves_the_timestamp_forward(isolated_desk):
    db.add_desk_dismissals(["job-7"], 1000.0)

    assert db.add_desk_dismissals(["job-7"], 2000.0) == {"job-7": 2000.0}


def test_the_dismissal_set_keeps_the_newest_and_drops_the_rest(isolated_desk):
    for number in range(db.DESK_DISMISSAL_LIMIT + 20):
        db.add_desk_dismissals([f"job-{number}"], float(number))

    stored = db.desk_dismissals()

    assert len(stored) == db.DESK_DISMISSAL_LIMIT
    assert stored.keys() == {
        f"job-{number}"
        for number in range(20, db.DESK_DISMISSAL_LIMIT + 20)
    }


def test_a_re_dismissed_key_counts_as_the_newest(isolated_desk):
    for number in range(db.DESK_DISMISSAL_LIMIT):
        db.add_desk_dismissals([f"job-{number}"], float(number))

    # job-0 is the oldest, and dismissing it again must save it from the trim
    # that the next new key forces.
    db.add_desk_dismissals(["job-0"], 5000.0)
    db.add_desk_dismissals(["job-9000"], 5001.0)
    stored = db.desk_dismissals()

    assert len(stored) == db.DESK_DISMISSAL_LIMIT
    assert "job-0" in stored
    assert "job-9000" in stored
    assert "job-1" not in stored


def test_unreadable_stored_dismissals_read_as_none(isolated_desk):
    db.connect().execute(
        "INSERT INTO settings(key,value) VALUES(?,?)",
        (db.DESK_DISMISSALS_KEY, "not json"),
    )
    db.connect().commit()

    assert db.desk_dismissals() == {}


def test_stored_dismissals_drop_entries_that_are_not_timestamps(isolated_desk):
    db.connect().execute(
        "INSERT INTO settings(key,value) VALUES(?,?)",
        (
            db.DESK_DISMISSALS_KEY,
            json.dumps({"job-1": 12.0, "job-2": "soon", "job-3": True, "job-4": 8}),
        ),
    )
    db.connect().commit()

    assert db.desk_dismissals() == {"job-1": 12.0, "job-4": 8.0}
