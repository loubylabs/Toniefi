"""A starred chapter stays starred across reads, and goes with its chapter."""
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
    yield
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


def make(title: str, stage: str = "forged") -> str:
    slug = library.create(title)
    path = config.LIBRARY_DIR / slug
    for name in ("one.mp3", "two.mp3", "three.mp3"):
        (path / name).write_bytes(name.encode())
    manifest_path = path / library.MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["stage"] = stage
    manifest["tracks"] = [
        {"name": name, "title": name, "seconds": 60, "size": len(name), "mtime": 1}
        for name in ("one.mp3", "two.mp3", "three.mp3")
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return slug


def starred(body: dict) -> list[str]:
    return [track["name"] for track in body["tracks"] if track.get("starred")]


def test_stars_show_on_both_reads_and_unstar_removes_them(isolated):
    slug = make("Night Stories")
    client = TestClient(main.app)

    answer = client.post(f"/api/collections/{slug}/stars", json={"names": ["three.mp3", "one.mp3"], "starred": True})
    assert answer.status_code == 200
    assert answer.json() == {"starred": ["one.mp3", "three.mp3"]}
    assert starred(client.get(f"/api/collections/{slug}").json()) == ["one.mp3", "three.mp3"]
    assert starred(client.get("/api/collections").json()[0]) == ["one.mp3", "three.mp3"]

    answer = client.post(f"/api/collections/{slug}/stars", json={"names": ["one.mp3"], "starred": False})
    assert answer.json() == {"starred": ["three.mp3"]}


def test_a_star_does_not_change_the_send_fingerprint(isolated):
    slug = make("Night Stories")
    client = TestClient(main.app)
    before = client.get(f"/api/collections/{slug}").json()["manifest_fingerprint"]
    client.post(f"/api/collections/{slug}/stars", json={"names": ["one.mp3"], "starred": True})
    assert client.get(f"/api/collections/{slug}").json()["manifest_fingerprint"] == before


def test_star_refuses_unknown_chapters_and_unfinished_collections(isolated):
    slug = make("Night Stories")
    raw = make("Raw Upload", stage="extracted")
    client = TestClient(main.app)

    assert client.post(f"/api/collections/{slug}/stars", json={"names": ["nope.mp3"], "starred": True}).status_code == 400
    assert client.post(f"/api/collections/{raw}/stars", json={"names": ["one.mp3"], "starred": True}).status_code == 409
    assert client.post("/api/collections/missing/stars", json={"names": [], "starred": True}).status_code == 404
    assert db.starred_chapters() == {}


def test_deleting_a_chapter_or_collection_forgets_its_stars(isolated):
    slug = make("Night Stories")
    client = TestClient(main.app)
    client.post(f"/api/collections/{slug}/stars", json={"names": ["one.mp3", "two.mp3"], "starred": True})

    client.delete(f"/api/collections/{slug}/tracks/one.mp3")
    assert set(db.starred_chapters()) == {(slug, "two.mp3")}

    client.delete(f"/api/collections/{slug}")
    assert db.starred_chapters() == {}


def test_a_new_collection_reusing_a_slug_does_not_inherit_old_stars(isolated):
    slug = make("Night Stories")
    db.set_stars(slug, ["one.mp3"], True)
    connection = db.connect()
    connection.execute("UPDATE stars SET created_at=0")
    connection.commit()
    client = TestClient(main.app)
    assert starred(client.get(f"/api/collections/{slug}").json()) == []
