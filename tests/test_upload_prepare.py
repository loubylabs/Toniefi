from __future__ import annotations

import json
import threading
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app import config, db, ingest, jobs, library, main

STAGE_MARKER = ".toniefi-upload-stage.json"


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


def owned_stage(name: str, retained_at: float):
    stage = config.WORK_DIR / name
    stage.mkdir(parents=True)
    (stage / STAGE_MARKER).write_text(
        json.dumps({"retained_at": retained_at}),
        encoding="utf-8",
    )
    return stage


def pause_sweep_after_discovery(monkeypatch, stage):
    path_type = type(config.WORK_DIR)
    real_iterdir = path_type.iterdir
    discovered = threading.Event()
    resume = threading.Event()

    def paused_iterdir(path):
        entries = list(real_iterdir(path))
        if path == config.WORK_DIR and stage in entries:
            discovered.set()
            assert resume.wait(5), "sweep was not resumed"
        return iter(entries)

    monkeypatch.setattr(path_type, "iterdir", paused_iterdir)
    return discovered, resume


def capture_sweep_errors(errors):
    try:
        jobs.sweep_upload_staging()
    except BaseException as exc:
        errors.append(exc)


def test_upload_endpoint_stages_the_whole_selection_as_one_job(client, isolated_db, monkeypatch):
    monkeypatch.setattr(jobs, "_now", lambda: 2_000_000, raising=False)
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
    assert json.loads((stage / STAGE_MARKER).read_text(encoding="utf-8")) == {
        "retained_at": 2_000_000,
    }
    assert len(db.jobs_for_refresh()) == 1


def test_worker_startup_sweeps_only_expired_owned_upload_staging(monkeypatch, isolated_db):
    now = 2_000_000
    expired = owned_stage("upload-expired", now - (24 * 60 * 60) - 1)
    active = owned_stage("upload-active", now - (24 * 60 * 60) - 1)
    db.create_job("upload_prepare", "Active upload", {"stage": active.name})
    retained = owned_stage("upload-retained", now - (24 * 60 * 60) + 1)
    unrelated = config.WORK_DIR / "upload-unrelated"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(jobs, "_now", lambda: now, raising=False)
    monkeypatch.setattr(config, "WORKER_THREADS", 0)
    jobs._stop.clear()

    try:
        jobs.start()
    finally:
        jobs.stop()
        jobs._stop.clear()

    assert not expired.exists()
    assert active.is_dir()
    assert retained.is_dir()
    assert (unrelated / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_restart_transition_returns_only_jobs_newly_interrupted(isolated_db):
    interrupted = db.create_job("upload_prepare", "Interrupted", {"stage": "upload-interrupted"})
    historical = db.create_job("upload_prepare", "Historical", {"stage": "upload-historical"})
    queued = db.create_job("upload_prepare", "Queued", {"stage": "upload-queued"})
    db.update_job(interrupted, status="running")
    db.update_job(historical, status="failed", error="older failure")

    transitioned = db.fail_stale_running()

    assert [job["id"] for job in transitioned] == [interrupted]
    assert transitioned[0]["kind"] == "upload_prepare"
    assert transitioned[0]["payload"] == {"stage": "upload-interrupted"}
    assert db.get_job(interrupted)["status"] == "failed"
    assert db.get_job(historical)["error"] == "older failure"
    assert db.get_job(queued)["status"] == "queued"
    assert db.fail_stale_running() == []


def test_restart_renews_only_newly_interrupted_upload_staging_before_sweep(
    monkeypatch,
    isolated_db,
):
    now = 2_000_000
    old = now - (24 * 60 * 60) - 1
    interrupted_stage = owned_stage("upload-interrupted", old)
    historical_stage = owned_stage("upload-historical", old)
    abandoned_stage = owned_stage("upload-abandoned", old)
    interrupted = db.create_job(
        "upload_prepare",
        "Interrupted upload",
        {"stage": interrupted_stage.name},
    )
    historical = db.create_job(
        "upload_prepare",
        "Historical failure",
        {"stage": historical_stage.name},
    )
    db.update_job(interrupted, status="running")
    db.update_job(historical, status="failed", error="older failure")
    monkeypatch.setattr(jobs, "_now", lambda: now, raising=False)
    monkeypatch.setattr(config, "WORKER_THREADS", 0)
    jobs._stop.clear()

    try:
        jobs.start()
    finally:
        jobs.stop()
        jobs._stop.clear()

    assert db.get_job(interrupted)["status"] == "failed"
    assert db.get_job(historical)["error"] == "older failure"
    assert json.loads((interrupted_stage / STAGE_MARKER).read_text(encoding="utf-8")) == {
        "retained_at": now,
    }
    assert not historical_stage.exists()
    assert not abandoned_stage.exists()


def test_concurrent_sweep_cannot_remove_a_leased_old_stage(monkeypatch, isolated_db):
    now = 2_000_000
    monkeypatch.setattr(jobs, "_now", lambda: now, raising=False)
    _, stage = jobs.create_upload_stage()
    (stage / STAGE_MARKER).write_text(
        json.dumps({"retained_at": now - (24 * 60 * 60) - 1}),
        encoding="utf-8",
    )

    sweep = threading.Thread(target=jobs.sweep_upload_staging)
    sweep.start()
    sweep.join()

    assert stage.is_dir()
    jobs.remove_upload_stage(stage)
    assert not stage.exists()


def test_sweep_rechecks_upload_job_ownership_after_stage_discovery(
    monkeypatch,
    isolated_db,
):
    now = 2_000_000
    monkeypatch.setattr(jobs, "_now", lambda: now, raising=False)
    stage_name, stage = jobs.create_upload_stage()
    (stage / STAGE_MARKER).write_text(
        json.dumps({"retained_at": now - (24 * 60 * 60) - 1}),
        encoding="utf-8",
    )
    discovered, resume = pause_sweep_after_discovery(monkeypatch, stage)
    sweep_errors = []
    sweep = threading.Thread(target=capture_sweep_errors, args=(sweep_errors,))
    payload = upload_payload(stage_name)
    payload.pop("stage")

    sweep.start()
    assert discovered.wait(5), "sweep did not discover the stage"
    try:
        job_id = jobs.enqueue_upload_stage(
            stage,
            "Upload Family Stories",
            payload,
        )
    finally:
        resume.set()
        sweep.join(5)

    assert not sweep.is_alive()
    assert sweep_errors == []
    persisted = db.get_job(job_id)
    assert persisted["status"] == "queued"
    assert persisted["payload"]["stage"] == stage_name
    assert stage.is_dir()


def test_sweep_rechecks_retry_job_ownership_after_stage_discovery(
    monkeypatch,
    isolated_db,
):
    now = 2_000_000
    stage = owned_stage("upload-retry-race", now - (24 * 60 * 60) - 1)
    failed_id = db.create_job(
        "upload_prepare",
        "Upload Family Stories",
        upload_payload(stage.name),
    )
    db.update_job(failed_id, status="failed", error="Forge unavailable")
    monkeypatch.setattr(jobs, "_now", lambda: now, raising=False)
    discovered, resume = pause_sweep_after_discovery(monkeypatch, stage)
    sweep_errors = []
    sweep = threading.Thread(target=capture_sweep_errors, args=(sweep_errors,))

    sweep.start()
    assert discovered.wait(5), "sweep did not discover the stage"
    try:
        retry_id = jobs.retry_failed_job(failed_id)
    finally:
        resume.set()
        sweep.join(5)

    assert not sweep.is_alive()
    assert sweep_errors == []
    assert db.get_job(retry_id)["status"] == "queued"
    assert stage.is_dir()


def test_sweep_removes_expired_unleased_unreferenced_owned_stage(
    monkeypatch,
    isolated_db,
):
    now = 2_000_000
    stage = owned_stage("upload-expired-unowned", now - (24 * 60 * 60) - 1)
    monkeypatch.setattr(jobs, "_now", lambda: now, raising=False)

    jobs.sweep_upload_staging()

    assert not stage.exists()


def test_upload_endpoint_sweeps_expired_staging_before_accepting_a_batch(
    client,
    isolated_db,
    monkeypatch,
):
    now = 2_000_000
    expired = owned_stage("upload-expired", now - (24 * 60 * 60) - 1)
    monkeypatch.setattr(jobs, "_now", lambda: now, raising=False)

    response = client.post(
        "/api/uploads/prepare",
        files=[("files", ("story.mp3", b"story", "audio/mpeg"))],
    )

    assert response.status_code == 200
    assert not expired.exists()


def test_upload_stream_renews_the_stage_heartbeat_for_each_chunk(
    client,
    isolated_db,
    monkeypatch,
):
    tick = 2_000_000

    def clock():
        nonlocal tick
        current = tick
        tick += 1
        return current

    monkeypatch.setattr(jobs, "_now", clock, raising=False)
    response = client.post(
        "/api/uploads/prepare",
        files=[("files", ("story.mp3", b"x" * (2 * 1024 * 1024 + 1), "audio/mpeg"))],
    )

    assert response.status_code == 200
    stage = config.WORK_DIR / db.get_job(response.json()["job_id"])["payload"]["stage"]
    assert json.loads((stage / STAGE_MARKER).read_text(encoding="utf-8")) == {
        "retained_at": 2_000_003,
    }


def test_upload_failure_releases_and_removes_its_pre_job_stage(
    client,
    isolated_db,
    monkeypatch,
):
    created = []
    real_create = jobs.create_upload_stage

    def capture_stage():
        result = real_create()
        created.append(result[1])
        return result

    monkeypatch.setattr(jobs, "create_upload_stage", capture_stage)
    monkeypatch.setattr(
        jobs,
        "enqueue_upload_stage",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("database stopped")),
    )

    with pytest.raises(RuntimeError, match="database stopped"):
        client.post(
            "/api/uploads/prepare",
            files=[("files", ("story.mp3", b"story", "audio/mpeg"))],
        )

    stage = created[0]
    assert not stage.exists()
    stage.mkdir()
    (stage / STAGE_MARKER).write_text(
        json.dumps({"retained_at": 1_000_000}),
        encoding="utf-8",
    )
    monkeypatch.setattr(jobs, "_now", lambda: 2_000_000, raising=False)
    jobs.sweep_upload_staging()
    assert not stage.exists()


def test_upload_endpoint_rejects_more_than_500_files_without_staging(client, isolated_db):
    response = client.post(
        "/api/uploads/prepare",
        files=[
            ("files", (f"chapter-{index}.mp3", b"x", "audio/mpeg"))
            for index in range(501)
        ],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "A collection can contain at most 500 uploaded files."
    assert list(config.WORK_DIR.iterdir()) == []


def test_upload_endpoint_rejects_total_bytes_over_the_limit_and_removes_staging(
    client,
    isolated_db,
    monkeypatch,
):
    monkeypatch.setattr(jobs, "UPLOAD_MAX_BYTES", 5, raising=False)

    response = client.post(
        "/api/uploads/prepare",
        files=[
            ("files", ("one.mp3", b"one", "audio/mpeg")),
            ("files", ("two.mp3", b"two", "audio/mpeg")),
        ],
    )

    assert response.status_code == 413
    assert response.json()["detail"] == (
        "The selected files exceed the upload limit of 5 bytes. "
        "Choose fewer or smaller files and submit the collection again."
    )
    assert list(config.WORK_DIR.iterdir()) == []


def test_import_upload_streams_a_path_to_one_deterministic_target(monkeypatch, isolated_db):
    source = config.WORK_DIR / "staged.mp3"
    source.write_bytes(b"audio data")
    slug = library.create("Family Stories", source="upload")
    monkeypatch.setattr(ingest.audio, "duration_seconds", lambda path: 12.5)

    first = ingest.import_upload(
        source,
        filename="Same Name.mp3",
        slug=slug,
        target_name="001-same-name.mp3",
    )
    second = ingest.import_upload(
        source,
        filename="Same Name.mp3",
        slug=slug,
        target_name="001-same-name.mp3",
    )

    target = config.LIBRARY_DIR / slug / "001-same-name.mp3"
    assert target.read_bytes() == b"audio data"
    assert [track["name"] for track in first["tracks"]] == ["001-same-name.mp3"]
    assert first["tracks"][0]["title"] == "Same Name"
    assert second["track_count"] == 1


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
        lambda source, *, filename, slug, target_name: calls.append(
            ("import", filename, source.read_bytes(), slug, target_name)
        ) or {"slug": slug},
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
        ("import", "chapter-one.mp3", b"one", "family-stories", "001-chapter-one.mp3"),
        ("import", "chapter-two.mp3", b"two", "family-stories", "002-chapter-two.mp3"),
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

    def import_file(source, *, filename, slug, target_name):
        attempts.append(filename)
        if filename == "chapter-two.mp3":
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


def test_upload_failure_refreshes_the_24_hour_staging_retention_clock(monkeypatch, isolated_db):
    stage = owned_stage("upload-failure-retained", 1_000_000)
    (stage / "000.mp3").write_bytes(b"one")
    (stage / "001.mp3").write_bytes(b"two")
    payload = upload_payload(stage.name)
    monkeypatch.setattr(jobs, "_now", lambda: 2_000_000, raising=False)
    monkeypatch.setattr(jobs.library, "create", lambda title, source: "family-stories")
    monkeypatch.setattr(jobs.library, "get", lambda slug: None)
    monkeypatch.setattr(jobs.library, "delete", lambda slug: None)
    monkeypatch.setattr(jobs.db, "update_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        jobs.ingest,
        "import_upload",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("import stopped")),
    )

    with pytest.raises(RuntimeError, match="import stopped"):
        jobs._handle({"id": 55, "kind": "upload_prepare", "payload": payload})

    assert json.loads((stage / STAGE_MARKER).read_text(encoding="utf-8")) == {
        "retained_at": 2_000_000,
    }


def test_upload_retry_after_staging_expiry_gives_resubmission_guidance(monkeypatch, isolated_db):
    payload = {
        **upload_payload("upload-expired"),
        "slug": "family-stories",
        "next_file": 1,
        "owns_collection": True,
    }
    monkeypatch.setattr(jobs.library, "get", lambda slug: {"slug": slug, "stage": "extracted"})

    with pytest.raises(
        RuntimeError,
        match=(
            "Upload staging expired after 24 hours. "
            "Select the files and submit the collection again."
        ),
    ):
        jobs._handle({"id": 56, "kind": "upload_prepare", "payload": payload})


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


def test_upload_retry_after_write_before_checkpoint_is_idempotent(monkeypatch, isolated_db):
    stage = owned_stage("upload-crash-window", 2_000_000)
    (stage / "000.mp3").write_bytes(b"first")
    (stage / "001.mp3").write_bytes(b"second")
    payload = {
        **upload_payload(stage.name),
        "files": [
            {"name": "same-name.mp3", "stored": "000.mp3"},
            {"name": "same-name.mp3", "stored": "001.mp3"},
        ],
    }
    job_id = db.create_job("upload_prepare", "Upload Same Names", payload)
    real_update_job = db.update_job
    crashed = False

    def crash_before_completed_checkpoint(target_job_id, **fields):
        nonlocal crashed
        updated_payload = fields.get("payload", {})
        if updated_payload.get("next_file") == 1 and not crashed:
            crashed = True
            raise KeyboardInterrupt("simulated process exit")
        real_update_job(target_job_id, **fields)

    monkeypatch.setattr(jobs.db, "update_job", crash_before_completed_checkpoint)
    monkeypatch.setattr(
        jobs.forge,
        "run",
        lambda *args, **kwargs: pytest.fail("Forge cannot run before restart"),
    )

    with pytest.raises(KeyboardInterrupt, match="simulated process exit"):
        jobs._handle({"id": job_id, "kind": "upload_prepare", "payload": payload})

    checkpoint = db.get_job(job_id)["payload"]
    slug = checkpoint["slug"]
    pending_before_retry = checkpoint.get("pending_file")
    forge_tracks = []
    monkeypatch.setattr(jobs.db, "update_job", real_update_job)

    def finish_forge(target_slug, **options):
        forge_tracks.extend(track["name"] for track in jobs.library.get(target_slug, refresh=True)["tracks"])
        return {"slug": target_slug, "stage": "forged"}

    monkeypatch.setattr(jobs.forge, "run", finish_forge)

    result = jobs._handle({"id": job_id, "kind": "upload_prepare", "payload": checkpoint})

    assert pending_before_retry == {"position": 0, "target": "001-same-name.mp3"}
    assert forge_tracks == ["001-same-name.mp3", "002-same-name.mp3"]
    assert result == {"slug": slug, "stage": "forged"}
