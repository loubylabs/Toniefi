"""A chapter shows its latest finished send, read from the push jobs."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import config, db, library, main


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "portal.db")
    config.ensure_dirs()
    db.init()
    slug = library.create("Night Stories")
    path = config.LIBRARY_DIR / slug
    for name in ("one.mp3", "two.mp3", "three.mp3"):
        (path / name).write_bytes(name.encode())
    manifest_path = path / library.MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tracks"] = [
        {"name": name, "title": name, "seconds": 60, "size": len(name), "mtime": 1}
        for name in ("one.mp3", "two.mp3", "three.mp3")
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    yield slug
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


def push(slug: str, files: list[str], tonie: str, status: str = "done") -> int:
    job_id = db.create_job("push", "Send", {"tonie_id": "T1", "sources": [{"slug": slug, "files": files}]})
    db.update_job(job_id, status=status, result={"tonie": tonie} if status == "done" else {})
    return job_id


def marks(body: dict) -> dict[str, str | None]:
    return {track["name"]: (track.get("sent") or {}).get("tonie") for track in body["tracks"]}


def test_chapters_show_their_latest_finished_send(isolated):
    push(isolated, ["one.mp3", "two.mp3"], "Bedtime")
    push(isolated, ["two.mp3"], "Car Rides")
    push(isolated, ["three.mp3"], "Never Landed", status="failed")
    client = TestClient(main.app)

    detail = client.get(f"/api/collections/{isolated}").json()
    listed = client.get("/api/collections").json()[0]

    expected = {"one.mp3": "Bedtime", "two.mp3": "Car Rides", "three.mp3": None}
    assert marks(detail) == expected
    assert marks(listed) == expected
    assert detail["tracks"][0]["sent"]["at"] >= detail["created_at"]


def test_a_send_does_not_change_the_fingerprint(isolated):
    client = TestClient(main.app)
    before = client.get(f"/api/collections/{isolated}").json()["manifest_fingerprint"]

    push(isolated, ["one.mp3"], "Bedtime")

    assert client.get(f"/api/collections/{isolated}").json()["manifest_fingerprint"] == before


def test_a_send_older_than_the_collection_belongs_to_an_earlier_one(isolated):
    push(isolated, ["one.mp3"], "Bedtime")
    library.delete(isolated)
    assert library.create("Night Stories") == isolated
    (config.LIBRARY_DIR / isolated / "one.mp3").write_bytes(b"new one")

    body = TestClient(main.app).get(f"/api/collections/{isolated}?refresh=true").json()

    assert marks(body) == {"one.mp3": None}
