"""Preparation orchestration starts extraction, then completes Forge."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs, main, prepare


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
        del db._local.conn
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "portal.db")
    db.init()
    yield
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
        del db._local.conn


def test_prepare_extracts_checkpoints_then_forges(monkeypatch):
    calls = []
    monkeypatch.setattr(prepare.ingest, "import_url", lambda url, **kw: {"slug": "alice"})
    monkeypatch.setattr(
        prepare.forge,
        "run",
        lambda slug, **kw: calls.append(("forge", slug)) or {"slug": slug, "stage": "forged"},
    )
    monkeypatch.setattr(prepare.library, "get", lambda slug: None)
    checkpoints = []

    result = prepare.run(
        {"url": "https://example.com/alice", "options": {}},
        progress=lambda message: calls.append(("progress", message)),
        checkpoint=lambda payload: checkpoints.append(dict(payload)),
    )

    assert checkpoints[-1]["slug"] == "alice"
    assert checkpoints[-1]["options"] == prepare.DEFAULT_OPTIONS
    assert calls[-1] == ("forge", "alice")
    assert result["stage"] == "forged"


def test_prepare_resumes_forge_from_an_extracted_slug(monkeypatch):
    imported = []
    forged = []
    monkeypatch.setattr(prepare.ingest, "import_url", lambda *args, **kwargs: imported.append(args))
    monkeypatch.setattr(prepare.library, "get", lambda slug: {"slug": slug, "stage": "extracted"})
    monkeypatch.setattr(
        prepare.forge,
        "run",
        lambda slug, **kwargs: forged.append((slug, kwargs)) or {"slug": slug, "stage": "forged"},
    )

    result = prepare.run(
        {"url": "https://example.com/alice", "slug": "alice", "options": {"trim_head": 3}},
        progress=lambda message: None,
        checkpoint=lambda payload: None,
    )

    assert imported == []
    assert forged[0][0] == "alice"
    assert forged[0][1]["trim_head"] == 3
    assert result["stage"] == "forged"


def test_prepare_returns_an_already_forged_collection(monkeypatch):
    forged = {"slug": "alice", "stage": "forged"}
    monkeypatch.setattr(prepare.library, "get", lambda slug: forged)
    monkeypatch.setattr(prepare.ingest, "import_url", lambda *args, **kwargs: AssertionError("extract"))
    monkeypatch.setattr(prepare.forge, "run", lambda *args, **kwargs: AssertionError("forge"))

    assert prepare.run(
        {"url": "https://example.com/alice", "slug": "alice", "options": {}},
        progress=lambda message: None,
        checkpoint=lambda payload: None,
    ) == forged


def test_prepare_restarts_extraction_when_checkpoint_is_missing(monkeypatch):
    checkpoints = []
    monkeypatch.setattr(prepare.library, "get", lambda slug: None)
    monkeypatch.setattr(prepare.ingest, "import_url", lambda *args, **kwargs: {"slug": "new-alice"})
    monkeypatch.setattr(
        prepare.forge, "run", lambda slug, **kwargs: {"slug": slug, "stage": "forged"}
    )

    result = prepare.run(
        {"url": "https://example.com/alice", "slug": "missing-alice", "options": {}},
        progress=lambda message: None,
        checkpoint=lambda payload: checkpoints.append(dict(payload)),
    )

    assert checkpoints == [{
        "url": "https://example.com/alice",
        "slug": "new-alice",
        "options": prepare.DEFAULT_OPTIONS,
    }]
    assert result["slug"] == "new-alice"


def test_prepare_batch_rejects_an_empty_source_list(client, monkeypatch):
    monkeypatch.setattr(jobs, "enqueue", lambda *args: pytest.fail("enqueue"))

    response = client.post("/api/prepare", json={"sources": [], "options": {}})

    assert response.status_code == 400


def test_prepare_batch_rejects_unsupported_schemes(client, monkeypatch):
    monkeypatch.setattr(jobs, "enqueue", lambda *args: pytest.fail("enqueue"))

    response = client.post("/api/prepare", json={
        "sources": [{"url": "ftp://example.com/alice"}], "options": {},
    })

    assert response.status_code == 400


def test_prepare_batch_rejects_exact_duplicates(client, monkeypatch):
    monkeypatch.setattr(jobs, "enqueue", lambda *args: pytest.fail("enqueue"))

    response = client.post("/api/prepare", json={
        "sources": [
            {"url": "https://example.com/alice"},
            {"url": "https://example.com/alice"},
        ],
        "options": {},
    })

    assert response.status_code == 400


def test_prepare_batch_rejects_more_than_fifty_sources(client, monkeypatch):
    monkeypatch.setattr(jobs, "enqueue", lambda *args: pytest.fail("enqueue"))
    sources = [{"url": f"https://example.com/{index}"} for index in range(51)]

    response = client.post("/api/prepare", json={"sources": sources, "options": {}})

    assert response.status_code == 400


def test_prepare_batch_creates_one_job_per_source(client, monkeypatch):
    created = []
    monkeypatch.setattr(
        jobs,
        "enqueue",
        lambda kind, label, payload: created.append((kind, label, payload)) or len(created),
    )

    response = client.post("/api/prepare", json={
        "sources": [{"url": "https://example.com/a"}, {"url": "https://example.com/b"}],
        "options": {},
    })

    assert response.status_code == 200
    assert response.json() == {"jobs": [
        {"id": 1, "url": "https://example.com/a"},
        {"id": 2, "url": "https://example.com/b"},
    ]}
    assert [kind for kind, _, _ in created] == ["prepare_url", "prepare_url"]
    assert [payload["options"] for _, _, payload in created] == [prepare.DEFAULT_OPTIONS] * 2


def test_retry_refuses_a_non_failed_job(client, monkeypatch):
    monkeypatch.setattr(db, "retry_failed_job", lambda job_id: 0)

    response = client.post("/api/jobs/10/retry")

    assert response.status_code == 400


def test_retry_clones_a_failed_job_payload(isolated_db):
    payload = {"url": "https://example.com/alice", "slug": "alice", "options": {}}
    failed_id = db.create_job("prepare_url", "Alice", payload)
    db.update_job(failed_id, status="failed", error="Forge unavailable")

    retry_id = db.retry_failed_job(failed_id)

    retry = db.get_job(retry_id)
    assert retry_id != failed_id
    assert retry["kind"] == "prepare_url"
    assert retry["status"] == "queued"
    assert retry["label"] == "Alice"
    assert retry["payload"] == payload


def test_retry_returns_the_new_job(client, monkeypatch):
    retried = {"id": 12, "kind": "prepare_url", "status": "queued", "progress": "", "payload": {}}
    monkeypatch.setattr(db, "retry_failed_job", lambda job_id: 12)
    monkeypatch.setattr(db, "get_job", lambda job_id: retried)

    response = client.post("/api/jobs/10/retry")

    assert response.status_code == 200
    assert response.json()["id"] == 12


def test_job_presentation_splits_progress_phase():
    job = {"kind": "prepare_url", "status": "running", "progress": "extracting: Fetching audio"}

    presented = jobs.present(job)

    assert presented["phase"] == "extracting"
    assert presented["progress"] == "Fetching audio"


def test_job_presentation_derives_a_ready_phase_for_completed_preparation():
    job = {"kind": "prepare_url", "status": "done", "progress": "Finished"}

    presented = jobs.present(job)

    assert presented["phase"] == "ready"
    assert presented["progress"] == "Finished"


def test_old_single_url_routes_are_gone(client):
    assert client.post("/api/probe", json={"url": "https://example.com"}).status_code == 404
    assert client.post("/api/ingest/url", json={"url": "https://example.com"}).status_code == 404
