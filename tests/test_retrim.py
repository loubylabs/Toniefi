from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db, forge, jobs, library, main
from tests.test_final_integrity import isolated, make_collection  # noqa: F401


def make_forged(title: str = "Bedtime Story", *, trim_head: float = 0, trim_tail: float = 0) -> str:
    slug = make_collection(title, stage="forged")
    path = config.LIBRARY_DIR / slug / library.MANIFEST
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["forge"] = {
        "normalized": True,
        "titles_cleaned": True,
        "trim_head": trim_head,
        "trim_tail": trim_tail,
        "split": True,
    }
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return slug


@pytest.fixture
def fake_audio(monkeypatch):
    calls = []

    def trim(target: Path, head: float, tail: float):
        calls.append((target.name, head, tail))
        target.write_bytes(target.read_bytes() + b"|trim")

    monkeypatch.setattr(forge, "trim_track", trim)
    monkeypatch.setattr(forge.audio, "duration_seconds", lambda path: 1000)
    return calls


def test_retrim_cuts_every_chapter_and_adds_to_the_recorded_trim(isolated, fake_audio):
    slug = make_forged(trim_head=2)
    path = config.LIBRARY_DIR / slug

    result = forge.retrim(slug, operation_id="trim-a", trim_head=3, trim_tail=1)

    assert sorted(fake_audio) == [("one.mp3", 3, 1), ("two.mp3", 3, 1)]
    assert (path / "one.mp3").read_bytes() == b"one.mp3|trim"
    assert (path / "two.mp3").read_bytes() == b"two.mp3|trim"
    assert result["stage"] == "forged"
    assert result["forge"]["trim_head"] == 5
    assert result["forge"]["trim_tail"] == 1
    assert result["forge"]["normalized"] is True
    assert library.get(slug)["forge"]["trim_head"] == 5


def test_retrim_failure_leaves_the_visible_collection_unchanged(isolated, monkeypatch):
    slug = make_forged()
    path = config.LIBRARY_DIR / slug
    original_files = {item.name: item.read_bytes() for item in path.glob("*.mp3")}
    original_manifest = (path / library.MANIFEST).read_bytes()
    seen = 0

    def trim(target: Path, *_):
        nonlocal seen
        seen += 1
        target.write_bytes(target.read_bytes() + b"|trim")
        if seen == 2:
            raise forge.audio.AudioError("too short")

    monkeypatch.setattr(forge, "trim_track", trim)
    monkeypatch.setattr(forge.audio, "duration_seconds", lambda path: 1000)

    with pytest.raises(forge.audio.AudioError, match="too short"):
        forge.retrim(slug, operation_id="trim-b", trim_head=5, trim_tail=0)

    assert {item.name: item.read_bytes() for item in path.glob("*.mp3")} == original_files
    assert (path / library.MANIFEST).read_bytes() == original_manifest


def test_retrim_retry_after_publication_does_not_cut_twice(isolated, fake_audio):
    slug = make_forged()
    path = config.LIBRARY_DIR / slug

    forge.retrim(slug, operation_id="trim-c", trim_head=4, trim_tail=0)
    again = forge.retrim(slug, operation_id="trim-c", trim_head=4, trim_tail=0)

    assert len(fake_audio) == 2
    assert (path / "one.mp3").read_bytes() == b"one.mp3|trim"
    assert again["forge"]["trim_head"] == 4


def test_trim_job_runs_retrim(isolated, fake_audio):
    slug = make_forged()
    job_id = db.create_collection_job_once("trim", f"Trim {slug}", {
        "slug": slug, "trim_head": 2, "trim_tail": 0,
    })
    job = db.get_job(job_id)

    result = jobs._handle(job)

    assert result["forge"]["trim_head"] == 2
    assert len(fake_audio) == 2


def test_trim_jobs_cannot_bypass_the_once_per_collection_guard(isolated):
    with pytest.raises(ValueError, match="create_collection_job_once"):
        db.create_job("trim", "Bypass", {"slug": "bypass"})


def test_trim_route_queues_one_job_per_collection(isolated):
    slug = make_forged()
    client = TestClient(main.app)

    first = client.post(f"/api/collections/{slug}/trim", json={"trim_head": 3, "trim_tail": 0})
    second = client.post(f"/api/collections/{slug}/trim", json={"trim_head": 9, "trim_tail": 0})

    assert first.status_code == 200
    assert second.json()["job_id"] == first.json()["job_id"]
    job = db.get_job(first.json()["job_id"])
    assert job["kind"] == "trim"
    assert job["payload"]["slug"] == slug
    assert job["payload"]["trim_head"] == 3
    assert job["payload"]["trim_operation_id"].startswith("trim-")


def test_failed_trim_retry_reuses_the_active_trim_job(isolated):
    slug = make_forged()
    failed_id = db.create_collection_job_once("trim", f"Trim {slug}", {"slug": slug, "trim_head": 1})
    db.update_job(failed_id, status="failed", error="stopped")
    active_id = db.create_collection_job_once("trim", f"Trim {slug}", {"slug": slug, "trim_head": 2})

    assert db.clone_failed_job(failed_id) == active_id


def test_trim_route_refuses_a_collection_that_has_not_finished_forge(isolated):
    slug = make_collection()
    client = TestClient(main.app)

    response = client.post(f"/api/collections/{slug}/trim", json={"trim_head": 3})

    assert response.status_code == 409


def test_trim_route_refuses_an_unknown_collection(isolated):
    client = TestClient(main.app)

    response = client.post("/api/collections/nobody/trim", json={"trim_head": 3})

    assert response.status_code == 404


@pytest.mark.parametrize("body", [
    {},
    {"trim_head": 0, "trim_tail": 0},
    {"trim_head": -1},
    {"trim_head": "3"},
    {"trim_head": 3, "normalize": True},
])
def test_trim_route_refuses_a_bad_amount(isolated, body):
    slug = make_forged()
    client = TestClient(main.app)

    response = client.post(f"/api/collections/{slug}/trim", json=body)

    assert response.status_code in {400, 422}
    assert db.jobs_for_refresh() == []
