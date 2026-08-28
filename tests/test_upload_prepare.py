from __future__ import annotations

import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs, main


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "portal.db")
    db.init()
    yield tmp_path
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


def upload_payload(stage: str) -> dict:
    return {
        "title": "Family Stories",
        "stage": stage,
        "files": [
            {"name": "chapter-one.mp3", "stored": "000.mp3"},
            {"name": "chapter-two.mp3", "stored": "001.mp3"},
        ],
        "options": {
            "use_chapters": True,
            "normalize": False,
            "clean_titles": True,
            "trim_head": 1.5,
            "trim_tail": 2.5,
            "split_oversized": False,
        },
    }


def test_upload_endpoint_stages_the_whole_selection_as_one_job(client, isolated_db):
    response = client.post(
        "/api/uploads/prepare",
        files=[
            ("files", ("chapter-one.mp3", b"one", "audio/mpeg")),
            ("files", ("chapter-two.mp3", b"two", "audio/mpeg")),
        ],
        data={
            "title": "Family Stories",
            "options": json.dumps({
                "use_chapters": True,
                "normalize": False,
                "clean_titles": True,
                "trim_head": 1.5,
                "trim_tail": 2.5,
                "split_oversized": False,
            }),
        },
    )

    assert response.status_code == 200
    job = db.get_job(response.json()["job_id"])
    assert job["kind"] == "upload_prepare"
    assert job["status"] == "queued"
    assert job["payload"]["title"] == "Family Stories"
    assert job["payload"]["options"] == {
        "use_chapters": True,
        "normalize": False,
        "clean_titles": True,
        "trim_head": 1.5,
        "trim_tail": 2.5,
        "split_oversized": False,
    }
    assert [item["name"] for item in job["payload"]["files"]] == [
        "chapter-one.mp3",
        "chapter-two.mp3",
    ]
    stage = config.WORK_DIR / job["payload"]["stage"]
    assert [(stage / item["stored"]).read_bytes() for item in job["payload"]["files"]] == [b"one", b"two"]
    assert len(db.jobs_for_refresh()) == 1


def test_upload_job_imports_every_file_checkpoints_then_forges(monkeypatch, isolated_db):
    stage = config.WORK_DIR / "upload-batch"
    stage.mkdir(parents=True)
    (stage / "000.mp3").write_bytes(b"one")
    (stage / "001.mp3").write_bytes(b"two")
    payload = upload_payload(stage.name)
    calls = []
    updates = []

    monkeypatch.setattr(jobs.library, "create", lambda title, source: calls.append(("create", title, source)) or "family-stories")
    monkeypatch.setattr(jobs.library, "get", lambda slug: None)
    monkeypatch.setattr(
        jobs.ingest,
        "import_upload",
        lambda name, data, slug, title=None: calls.append(("import", name, data, slug)) or {"slug": slug},
    )
    monkeypatch.setattr(
        jobs.forge,
        "run",
        lambda slug, **options: calls.append(("forge", slug, {
            "normalize": options["normalize"],
            "clean_titles": options["clean_titles"],
            "trim_head": options["trim_head"],
            "trim_tail": options["trim_tail"],
            "split_oversized": options["split_oversized"],
        })) or {"slug": slug, "stage": "forged"},
    )
    monkeypatch.setattr(
        jobs.db,
        "update_job",
        lambda job_id, **fields: updates.append((job_id, deepcopy(fields))),
    )
    monkeypatch.setattr(jobs, "enqueue", lambda *args: pytest.fail("upload preparation stays in one job"))

    result = jobs._handle({"id": 51, "kind": "upload_prepare", "payload": payload})

    assert result == {"slug": "family-stories", "stage": "forged"}
    assert calls == [
        ("create", "Family Stories", "upload"),
        ("import", "chapter-one.mp3", b"one", "family-stories"),
        ("import", "chapter-two.mp3", b"two", "family-stories"),
        ("forge", "family-stories", {
            "normalize": False,
            "clean_titles": True,
            "trim_head": 1.5,
            "trim_tail": 2.5,
            "split_oversized": False,
        }),
    ]
    assert updates[0] == (51, {"payload": {
        **payload,
        "slug": "family-stories",
        "next_file": 0,
        "owns_collection": True,
    }})
    assert updates[-1][1]["payload"]["next_file"] == 2
    assert not stage.exists()


def test_upload_import_failure_rolls_back_its_new_collection(monkeypatch, isolated_db):
    stage = config.WORK_DIR / "upload-failure"
    stage.mkdir(parents=True)
    (stage / "000.mp3").write_bytes(b"one")
    (stage / "001.mp3").write_bytes(b"two")
    payload = upload_payload(stage.name)
    deleted = []
    updates = []
    attempts = []

    monkeypatch.setattr(jobs.library, "create", lambda title, source: "family-stories")
    monkeypatch.setattr(jobs.library, "get", lambda slug: None)

    def import_file(name, data, slug, title=None):
        attempts.append(name)
        if name == "chapter-two.mp3":
            raise RuntimeError("second file failed")
        return {"slug": slug}

    monkeypatch.setattr(jobs.ingest, "import_upload", import_file)
    monkeypatch.setattr(jobs.library, "delete", lambda slug: deleted.append(slug))
    monkeypatch.setattr(
        jobs.db,
        "update_job",
        lambda job_id, **fields: updates.append((job_id, deepcopy(fields))),
    )

    with pytest.raises(RuntimeError, match="second file failed"):
        jobs._handle({"id": 52, "kind": "upload_prepare", "payload": payload})

    assert attempts == ["chapter-one.mp3", "chapter-two.mp3"]
    assert deleted == ["family-stories"]
    assert updates[-1] == (52, {"payload": {**payload, "next_file": 0}})
    assert stage.exists()


def test_upload_retry_resumes_at_forge_without_importing_or_enqueuing(monkeypatch, isolated_db):
    stage = config.WORK_DIR / "upload-retry"
    stage.mkdir(parents=True)
    payload = {
        **upload_payload(stage.name),
        "slug": "family-stories",
        "next_file": 2,
        "owns_collection": True,
    }
    forge_calls = []
    monkeypatch.setattr(jobs.library, "get", lambda slug: {"slug": slug, "stage": "extracted"})
    monkeypatch.setattr(jobs.ingest, "import_upload", lambda *args: pytest.fail("retry must not import again"))
    monkeypatch.setattr(
        jobs.forge,
        "run",
        lambda slug, **options: forge_calls.append(slug) or {"slug": slug, "stage": "forged"},
    )
    monkeypatch.setattr(jobs.db, "update_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(jobs, "enqueue", lambda *args: pytest.fail("retry must not enqueue Forge"))

    result = jobs._handle({"id": 53, "kind": "upload_prepare", "payload": payload})

    assert forge_calls == ["family-stories"]
    assert result == {"slug": "family-stories", "stage": "forged"}
    assert not stage.exists()


def test_upload_retry_after_cleanup_returns_forged_collection_without_duplicate_work(monkeypatch, isolated_db):
    payload = {
        **upload_payload("upload-already-cleaned"),
        "slug": "family-stories",
        "next_file": 2,
        "owns_collection": True,
    }
    forged = {"slug": "family-stories", "stage": "forged"}
    monkeypatch.setattr(jobs.library, "get", lambda slug: forged)
    monkeypatch.setattr(jobs.ingest, "import_upload", lambda *args: pytest.fail("must not import again"))
    monkeypatch.setattr(jobs.forge, "run", lambda *args, **kwargs: pytest.fail("must not Forge again"))

    assert jobs._handle({"id": 54, "kind": "upload_prepare", "payload": payload}) == forged
