"""Preparation orchestration starts extraction, then completes Forge."""
from __future__ import annotations

import sqlite3

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


def test_librivox_job_imports_checkpoints_and_forges_in_one_background_job(monkeypatch):
    calls = []
    updates = []

    def import_book(book_id, progress):
        calls.append(("import", book_id))
        progress("Downloading 1/2: Chapter one")
        return {"slug": "wind-in-the-willows", "stage": "extracted"}

    def run_forge(slug, **kwargs):
        calls.append(("forge", slug, {
            "normalize": kwargs["normalize"],
            "clean_titles": kwargs["clean_titles"],
            "trim_head": kwargs["trim_head"],
            "trim_tail": kwargs["trim_tail"],
            "split_oversized": kwargs["split_oversized"],
        }))
        kwargs["progress"]("Levelling 1/2: Chapter one")
        return {"slug": slug, "stage": "forged"}

    monkeypatch.setattr(jobs.ingest, "import_librivox", import_book)
    monkeypatch.setattr(jobs.library, "get", lambda slug: None)
    monkeypatch.setattr(jobs.forge, "run", run_forge)
    monkeypatch.setattr(jobs.db, "update_job", lambda job_id, **fields: updates.append((job_id, fields)))

    result = jobs._handle({
        "id": 41,
        "kind": "librivox",
        "payload": {"book_id": "180"},
    })

    assert result == {"slug": "wind-in-the-willows", "stage": "forged"}
    assert calls == [
        ("import", "180"),
        ("forge", "wind-in-the-willows", {
            "normalize": True,
            "clean_titles": True,
            "trim_head": 0,
            "trim_tail": 0,
            "split_oversized": True,
        }),
    ]
    assert updates == [
        (41, {"progress": "extracting: Downloading 1/2: Chapter one"}),
        (41, {"payload": {
            "book_id": "180",
            "slug": "wind-in-the-willows",
            "options": {
                "use_chapters": True,
                "normalize": True,
                "clean_titles": True,
                "trim_head": 0,
                "trim_tail": 0,
                "split_oversized": True,
            },
        }}),
        (41, {"progress": "forging: Levelling 1/2: Chapter one"}),
    ]


def test_librivox_retry_resumes_forge_without_importing_or_enqueuing_another_job(monkeypatch):
    forge_calls = []
    monkeypatch.setattr(
        jobs.ingest,
        "import_librivox",
        lambda *args, **kwargs: pytest.fail("a checkpointed retry must not import again"),
    )
    monkeypatch.setattr(
        jobs.library,
        "get",
        lambda slug: {"slug": slug, "stage": "extracted"},
    )
    monkeypatch.setattr(
        jobs.forge,
        "run",
        lambda slug, **kwargs: forge_calls.append(slug) or {"slug": slug, "stage": "forged"},
    )
    monkeypatch.setattr(
        jobs,
        "enqueue",
        lambda *args, **kwargs: pytest.fail("the same LibriVox job must complete Forge"),
    )
    monkeypatch.setattr(jobs.db, "update_job", lambda *args, **kwargs: None)

    result = jobs._handle({
        "id": 42,
        "kind": "librivox",
        "payload": {
            "book_id": "180",
            "slug": "wind-in-the-willows",
            "options": {
                "use_chapters": True,
                "normalize": True,
                "clean_titles": True,
                "trim_head": 0,
                "trim_tail": 0,
                "split_oversized": True,
            },
        },
    })

    assert forge_calls == ["wind-in-the-willows"]
    assert result == {"slug": "wind-in-the-willows", "stage": "forged"}


def test_failed_librivox_forge_progress_is_presented_as_failed():
    presented = jobs.present({
        "id": 43,
        "kind": "librivox",
        "status": "failed",
        "progress": "forging: Levelling 1/2: Chapter one",
    })

    assert presented["phase"] == "failed"
    assert presented["progress"] == "Levelling 1/2: Chapter one"


def test_completed_legacy_librivox_import_is_not_presented_as_ready():
    presented = jobs.present({
        "id": 44,
        "kind": "librivox",
        "status": "done",
        "progress": "Finished",
    })

    assert presented["phase"] == "done"


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


@pytest.mark.parametrize("url", [
    "https:///missing-host",
    "https://",
    "http://[::1",
    "https://example.com:alphabetic/story",
    "https://example.com:70000/story",
    "https://exa mple.com/story",
])
def test_prepare_batch_rejects_http_urls_without_a_host(client, isolated_db, url):
    response = client.post("/api/prepare", json={
        "sources": [{"url": url}],
        "options": {},
    })

    assert response.status_code == 400
    assert db.jobs_for_refresh() == []


def test_prepare_batch_accepts_a_valid_explicit_port(client, monkeypatch):
    created = []
    monkeypatch.setattr(
        jobs,
        "enqueue_many",
        lambda entries: created.extend(entries) or [81],
    )

    response = client.post("/api/prepare", json={
        "sources": [{"url": "https://example.com:8443/story"}],
        "options": {},
    })

    assert response.status_code == 200
    assert response.json() == {
        "jobs": [{"id": 81, "url": "https://example.com:8443/story"}],
    }
    assert created[0][2]["url"] == "https://example.com:8443/story"


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
        "enqueue_many",
        lambda entries: created.extend(entries) or [1, 2],
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


def test_prepare_batch_rolls_back_every_job_when_one_insert_fails(client, isolated_db):
    connection = db.connect()
    connection.execute(
        "CREATE TRIGGER fail_second_prepare BEFORE INSERT ON jobs "
        "WHEN NEW.label='https://example.com/b' "
        "BEGIN SELECT RAISE(ABORT, 'second insert failed'); END"
    )
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="second insert failed"):
        client.post("/api/prepare", json={
            "sources": [
                {"url": "https://example.com/a"},
                {"url": "https://example.com/b"},
            ],
            "options": {},
        })

    assert db.jobs_for_refresh() == []


def test_librivox_endpoint_persists_selected_forge_options(client, monkeypatch):
    created = []
    monkeypatch.setattr(
        jobs,
        "enqueue",
        lambda kind, label, payload: created.append((kind, label, payload)) or 27,
    )

    response = client.post("/api/librivox/import", json={
        "book_id": "180",
        "options": {
            "use_chapters": False,
            "normalize": False,
            "clean_titles": False,
            "trim_head": 1.5,
            "trim_tail": 2.5,
            "split_oversized": False,
        },
    })

    assert response.status_code == 200
    assert response.json() == {"job_id": 27}
    assert created == [(
        "librivox",
        "LibriVox import 180",
        {
            "book_id": "180",
            "options": {
                "use_chapters": False,
                "normalize": False,
                "clean_titles": False,
                "trim_head": 1.5,
                "trim_tail": 2.5,
                "split_oversized": False,
            },
        },
    )]


def test_jobs_endpoint_returns_every_active_job_before_failed_and_completed(client, isolated_db):
    active_ids = []
    for index in range(5):
        job_id = db.create_job("prepare_url", f"active-{index}", {"url": f"https://example.com/{index}"})
        if index % 2:
            db.update_job(job_id, status="running")
        active_ids.append(job_id)
    completed_ids = []
    for index in range(3):
        job_id = db.create_job("prepare_url", f"done-{index}", {"url": f"https://example.com/done-{index}"})
        db.update_job(job_id, status="done")
        completed_ids.append(job_id)
    failed_id = db.create_job("prepare_url", "failed", {"url": "https://example.com/failed"})
    db.update_job(failed_id, status="failed")

    response = client.get("/api/jobs?limit=8")

    assert response.status_code == 200
    assert [job["id"] for job in response.json()] == [
        active_ids[4],
        active_ids[3],
        active_ids[2],
        active_ids[1],
        active_ids[0],
        failed_id,
        completed_ids[2],
        completed_ids[1],
    ]


def test_retry_refuses_a_non_failed_job(client, monkeypatch):
    monkeypatch.setattr(jobs, "retry_failed_job", lambda job_id: 0)

    response = client.post("/api/jobs/10/retry")

    assert response.status_code == 400


def test_retry_clones_a_failed_job_payload(isolated_db):
    payload = {"url": "https://example.com/alice", "slug": "alice", "options": {}}
    failed_id = db.create_job("prepare_url", "Alice", payload)
    db.update_job(failed_id, status="failed", error="Forge unavailable")

    retry_id = jobs.retry_failed_job(failed_id)

    retry = db.get_job(retry_id)
    assert retry_id != failed_id
    assert retry["kind"] == "prepare_url"
    assert retry["status"] == "queued"
    assert retry["label"] == "Alice"
    assert retry["payload"] == payload


def test_retry_returns_the_new_job(client, monkeypatch):
    retried = {"id": 12, "kind": "prepare_url", "status": "queued", "progress": "", "payload": {}}
    monkeypatch.setattr(jobs, "retry_failed_job", lambda job_id: 12)
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
