from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db, forge, ingest, jobs, library, main, prepare, push, tonies


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "UPLOAD_STAGE_DIR", tmp_path / "data" / "upload-staging", raising=False)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "portal.db")
    monkeypatch.setattr(config, "TONIE_LIMIT_SECONDS", 5400)
    monkeypatch.setattr(config, "TONIE_HEADROOM_SECONDS", 30)
    config.ensure_dirs()
    db.init()
    yield tmp_path
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


def make_collection(title: str = "Bedtime Story", *, stage: str = "extracted") -> str:
    slug = library.create(title, source="upload")
    path = config.LIBRARY_DIR / slug
    tracks = []
    for index, name in enumerate(("one.mp3", "two.mp3"), start=1):
        target = path / name
        target.write_bytes(name.encode("utf-8"))
        stat = target.stat()
        tracks.append({
            "name": name,
            "title": f"Chapter {index}",
            "seconds": 1000,
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
        })
    manifest_path = path / library.MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["stage"] = stage
    manifest["tracks"] = tracks
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return slug


@pytest.mark.parametrize("fail_after", [1, 2, 3, 4])
def test_forge_failure_after_each_audio_transform_keeps_visible_collection_unchanged(
    isolated,
    monkeypatch,
    fail_after,
):
    slug = make_collection()
    path = config.LIBRARY_DIR / slug
    original_files = {item.name: item.read_bytes() for item in path.glob("*.mp3")}
    original_manifest = (path / library.MANIFEST).read_bytes()
    transforms = 0

    def transform(target: Path, suffix: bytes):
        nonlocal transforms
        target.write_bytes(target.read_bytes() + suffix)
        transforms += 1
        if transforms == fail_after:
            raise RuntimeError(f"transform {fail_after} stopped")

    monkeypatch.setattr(forge, "trim_track", lambda target, *_: transform(target, b"|trim"))
    monkeypatch.setattr(forge, "normalize_track", lambda target, **_: transform(target, b"|level"))
    monkeypatch.setattr(forge.audio, "duration_seconds", lambda path: 1000)

    with pytest.raises(RuntimeError, match=f"transform {fail_after} stopped"):
        forge.run(
            slug,
            operation_id=f"forge-transform-{fail_after}",
            normalize=True,
            clean_titles=False,
            trim_head=1,
            split_oversized=False,
        )

    assert {item.name: item.read_bytes() for item in path.glob("*.mp3")} == original_files
    assert (path / library.MANIFEST).read_bytes() == original_manifest
    assert library.get(slug)["stage"] == "extracted"


def test_forge_retry_applies_trim_and_normalization_exactly_once(isolated, monkeypatch):
    slug = make_collection()
    path = config.LIBRARY_DIR / slug
    fail = True

    def trim(target: Path, *_):
        target.write_bytes(target.read_bytes() + b"|trim")

    def normalize(target: Path, **_):
        nonlocal fail
        target.write_bytes(target.read_bytes() + b"|level")
        if fail:
            fail = False
            raise RuntimeError("normalization stopped")

    monkeypatch.setattr(forge, "trim_track", trim)
    monkeypatch.setattr(forge, "normalize_track", normalize)
    monkeypatch.setattr(forge.audio, "duration_seconds", lambda path: 1000)

    with pytest.raises(RuntimeError, match="normalization stopped"):
        forge.run(
            slug,
            operation_id="forge-retry-after-transform-failure",
            normalize=True,
            clean_titles=False,
            trim_head=1,
            split_oversized=False,
        )

    result = forge.run(
        slug,
        operation_id="forge-retry-after-transform-failure",
        normalize=True,
        clean_titles=False,
        trim_head=1,
        split_oversized=False,
    )

    assert (path / "one.mp3").read_bytes() == b"one.mp3|trim|level"
    assert (path / "two.mp3").read_bytes() == b"two.mp3|trim|level"
    assert result["stage"] == "forged"


def test_manual_forge_retry_after_published_process_death_is_a_verified_noop(
    isolated,
    monkeypatch,
):
    slug = make_collection()
    path = config.LIBRARY_DIR / slug
    transforms = {"trim": 0, "normalize": 0}

    def trim(target: Path, *_):
        transforms["trim"] += 1
        target.write_bytes(target.read_bytes() + b"|trim")

    def normalize(target: Path, **_):
        transforms["normalize"] += 1
        target.write_bytes(target.read_bytes() + b"|level")

    monkeypatch.setattr(forge, "trim_track", trim)
    monkeypatch.setattr(forge, "normalize_track", normalize)
    monkeypatch.setattr(forge.audio, "duration_seconds", lambda path: 1000)
    job_id = db.create_forge_job_once(
        f"Forge {slug}",
        {
            "slug": slug,
            "normalize": True,
            "clean_titles": False,
            "trim_head": 1,
            "trim_tail": 0,
            "split_oversized": False,
        },
    )
    claimed = db.claim_job()
    assert claimed["id"] == job_id

    first = jobs._handle(claimed)
    interrupted = db.fail_stale_running()
    library.recover_collection_publications()
    retry_id = jobs.retry_failed_job(job_id)
    resolved = db.get_job(retry_id)

    assert [job["id"] for job in interrupted] == [job_id]
    assert retry_id == job_id
    assert resolved["status"] == "done"
    assert db.claim_job() is None
    assert first["slug"] == resolved["result"]["slug"] == slug
    assert transforms == {"trim": 2, "normalize": 2}
    assert (path / "one.mp3").read_bytes() == b"one.mp3|trim|level"
    assert (path / "two.mp3").read_bytes() == b"two.mp3|trim|level"


def test_duplicate_history_retry_reuses_the_active_forge_job(isolated):
    slug = make_collection()
    failed_id = db.create_forge_job_once(f"Forge {slug}", {"slug": slug})
    db.update_job(failed_id, status="failed", error="worker stopped")

    first_retry = jobs.retry_failed_job(failed_id)
    second_retry = jobs.retry_failed_job(failed_id)
    active = [
        job for job in db.jobs_for_refresh()
        if job["kind"] == "forge" and job["status"] in {"queued", "running"}
    ]

    assert second_retry == first_retry
    assert [job["id"] for job in active] == [first_retry]


@pytest.mark.parametrize("stale_path", ["direct", "cloned"])
def test_forged_stage_is_terminal_for_every_stale_forge_worker_path(
    isolated,
    monkeypatch,
    stale_path,
):
    slug = make_collection()
    path = config.LIBRARY_DIR / slug
    transforms = {"trim": 0, "normalize": 0}

    def trim(target: Path, *_):
        transforms["trim"] += 1
        target.write_bytes(target.read_bytes() + b"|trim")

    def normalize(target: Path, **_):
        transforms["normalize"] += 1
        target.write_bytes(target.read_bytes() + b"|level")

    monkeypatch.setattr(forge, "trim_track", trim)
    monkeypatch.setattr(forge, "normalize_track", normalize)
    monkeypatch.setattr(forge.audio, "duration_seconds", lambda path: 1000)
    payload = {
        "slug": slug,
        "normalize": True,
        "clean_titles": False,
        "trim_head": 1,
        "trim_tail": 0,
        "split_oversized": False,
    }
    failed_id = db.create_forge_job_once(f"Forge {slug}", payload)
    failed = db.get_job(failed_id)
    db.update_job(failed_id, status="failed", error="first attempt stopped")
    completed_id = db.create_forge_job_once(f"Forge {slug}", payload)
    completed = db.claim_job()

    assert completed["id"] == completed_id
    assert completed["payload"]["forge_operation_id"] != failed["payload"]["forge_operation_id"]
    published = jobs._handle(completed)
    db.update_job(completed_id, status="done", result=published)

    stale = db.get_job(failed_id)
    if stale_path == "cloned":
        clone_id = db.clone_failed_job(failed_id)
        stale = db.claim_job()
        assert stale["id"] == clone_id
    stale_result = jobs._handle(stale)

    assert stale_result["slug"] == slug
    assert transforms == {"trim": 2, "normalize": 2}
    assert (path / "one.mp3").read_bytes() == b"one.mp3|trim|level"
    assert (path / "two.mp3").read_bytes() == b"two.mp3|trim|level"


def test_failed_forge_history_resolves_to_the_terminal_collection(isolated):
    slug = make_collection(stage="forged")
    path = config.LIBRARY_DIR / slug
    manifest = json.loads((path / library.MANIFEST).read_text(encoding="utf-8"))
    manifest["forge_operation_id"] = "forge-completed-by-newer-job"
    (path / library.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    failed_id = db.create_forge_job_once(f"Forge {slug}", {"slug": slug})
    db.update_job(failed_id, status="failed", error="older operation stopped")

    before = jobs.present(db.get_job(failed_id))
    response = TestClient(main.app).post(f"/api/jobs/{failed_id}/retry")

    assert before["retryable"] is False
    assert response.status_code == 200
    assert response.json()["status"] == "done"
    assert response.json()["result"]["slug"] == slug
    assert db.get_job(failed_id)["error"] == ""


def test_init_migrates_all_legacy_forge_jobs_once_for_claim_and_retry(isolated):
    conn = db.connect()
    migration_name = "2026-08-28-forge-operation-ids"
    conn.execute("DELETE FROM schema_migrations WHERE name=?", (migration_name,))
    legacy_ids = {}
    for position, status in enumerate(("queued", "running", "failed"), start=1):
        cursor = conn.execute(
            "INSERT INTO jobs(kind,status,label,payload,created_at,updated_at) "
            "VALUES('forge',?,?,?,?,?)",
            (
                status,
                f"Legacy {status}",
                json.dumps({"slug": f"legacy-{status}"}),
                float(position),
                float(position),
            ),
        )
        legacy_ids[status] = int(cursor.lastrowid)
    conn.commit()

    db.init()
    migrated = {status: db.get_job(job_id) for status, job_id in legacy_ids.items()}
    operation_ids = {
        status: job["payload"]["forge_operation_id"]
        for status, job in migrated.items()
    }
    db.init()

    claimed = db.claim_job()
    retry_id = jobs.retry_failed_job(legacy_ids["failed"])
    retry = db.get_job(retry_id)

    assert all(identity.startswith("forge-") for identity in operation_ids.values())
    assert len(set(operation_ids.values())) == 3
    assert {
        status: db.get_job(job_id)["payload"]["forge_operation_id"]
        for status, job_id in legacy_ids.items()
    } == operation_ids
    assert claimed["id"] == legacy_ids["queued"]
    assert claimed["payload"]["forge_operation_id"] == operation_ids["queued"]
    assert migrated["running"]["payload"]["forge_operation_id"] == operation_ids["running"]
    assert retry["payload"]["forge_operation_id"] == operation_ids["failed"]
    assert conn.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE name=?",
        (migration_name,),
    ).fetchone()[0] == 1


def test_generic_job_creation_cannot_bypass_canonical_forge_payloads(isolated):
    with pytest.raises(ValueError, match="create_forge_job_once"):
        db.create_job("forge", "Legacy bypass", {"slug": "legacy-bypass"})


def test_hidden_collection_stage_publishes_once_and_is_absent_from_library(isolated, monkeypatch):
    stage = library.begin_collection_stage(
        "url-job-41",
        title="The Secret Garden",
        source="url",
        extra={"url": "https://example.test/story"},
    )
    (stage.path / "one.mp3").write_bytes(b"complete audio")
    library.rescan_collection_stage(stage.identity)
    library.complete_collection_stage(stage.identity)
    monkeypatch.setattr(forge.audio, "duration_seconds", lambda path: 60)

    assert library.list_all() == []

    first = forge.run_collection_stage(
        stage.identity,
        normalize=False,
        clean_titles=False,
        split_oversized=False,
    )
    second = forge.run_collection_stage(
        stage.identity,
        normalize=False,
        clean_titles=False,
        split_oversized=False,
    )

    assert first["slug"] == "the-secret-garden"
    assert second["slug"] == "the-secret-garden"
    assert [collection["slug"] for collection in library.list_all()] == ["the-secret-garden"]
    assert not stage.path.exists()


def test_same_title_hidden_jobs_reserve_distinct_retry_stable_final_slugs(
    isolated,
    monkeypatch,
):
    stages = [
        library.begin_collection_stage(
            identity,
            title="Shared Story",
            source=source,
        )
        for identity, source in (
            ("url-shared-story", "url"),
            ("librivox-shared-story", "librivox"),
            ("upload-shared-story", "upload"),
        )
    ]

    assert [stage.slug for stage in stages] == [
        "shared-story",
        "shared-story-2",
        "shared-story-3",
    ]

    monkeypatch.setattr(forge.audio, "duration_seconds", lambda path: 60)
    published = []
    for stage in stages:
        (stage.path / "story.mp3").write_bytes(stage.identity.encode("utf-8"))
        library.rescan_collection_stage(stage.identity)
        library.complete_collection_stage(stage.identity)
        first = forge.run_collection_stage(
            stage.identity,
            normalize=False,
            clean_titles=False,
            split_oversized=False,
        )
        retry = forge.run_collection_stage(
            stage.identity,
            normalize=False,
            clean_titles=False,
            split_oversized=False,
        )
        assert retry["slug"] == first["slug"] == stage.slug
        published.append(first["slug"])

    assert published == ["shared-story", "shared-story-2", "shared-story-3"]
    assert {item["slug"] for item in library.list_all()} == set(published)


def test_librivox_network_failure_resumes_hidden_stage_and_publishes_one_collection(
    isolated,
    monkeypatch,
):
    book = {"title": "Little Women", "authors": [], "url_librivox": "https://book.test"}
    sections = [
        {"title": "Chapter One", "listen_url": "https://audio.test/one.mp3"},
        {"title": "Chapter Two", "listen_url": "https://audio.test/two.mp3"},
    ]
    monkeypatch.setattr(ingest, "librivox_sections", lambda _: (book, sections))
    attempts = []

    def interrupted_download(client, url, target):
        attempts.append(url)
        if url.endswith("two.mp3") and attempts.count(url) == 1:
            raise RuntimeError("network stopped")
        target.write_bytes(url.encode("utf-8"))

    monkeypatch.setattr(ingest, "_stream_download", interrupted_download)
    monkeypatch.setattr(forge.audio, "duration_seconds", lambda path: 60)

    with pytest.raises(RuntimeError, match="network stopped"):
        ingest.import_librivox("41", stage_id="librivox-41")

    assert library.list_all() == []
    hidden = library.collection_stage("librivox-41")
    assert hidden is not None
    assert (Path(hidden["path"]) / "001-chapter-one.mp3").is_file()

    ingest.import_librivox("41", stage_id="librivox-41")
    result = forge.run_collection_stage(
        "librivox-41",
        normalize=False,
        clean_titles=False,
        split_oversized=False,
    )

    assert attempts.count("https://audio.test/one.mp3") == 1
    assert result["stage"] == "forged"
    assert [item["slug"] for item in library.list_all()] == ["little-women"]


def test_librivox_job_retry_resumes_an_incomplete_hidden_extraction(
    isolated,
    monkeypatch,
):
    stage = library.begin_collection_stage(
        "librivox-interrupted",
        title="Interrupted Book",
        source="librivox",
    )
    (stage.path / "001-only-part.mp3").write_bytes(b"partial")
    library.rescan_collection_stage(stage.identity)
    calls = []

    monkeypatch.setattr(jobs.library, "find_published_stage", lambda _: None)
    monkeypatch.setattr(
        jobs.ingest,
        "import_librivox",
        lambda book_id, *, stage_id, progress: calls.append((book_id, stage_id))
        or {"slug": stage.slug, "stage": "extracted"},
    )
    monkeypatch.setattr(
        jobs.forge,
        "run_collection_stage",
        lambda stage_id, **options: {"slug": stage.slug, "stage": "forged"},
    )

    result = jobs._handle({
        "id": db.create_job("librivox", "Interrupted", {
            "book_id": "41",
            "stage_id": stage.identity,
            "options": {},
        }),
        "kind": "librivox",
        "payload": {
            "book_id": "41",
            "stage_id": stage.identity,
            "options": {},
        },
    })

    assert calls == [("41", stage.identity)]
    assert result["stage"] == "forged"


def test_url_job_retry_restarts_an_incomplete_hidden_extraction(
    isolated,
    monkeypatch,
):
    stage = library.begin_collection_stage(
        "url-interrupted",
        title="Interrupted Story",
        source="url",
    )
    (stage.path / "001-only-part.mp3").write_bytes(b"partial")
    library.rescan_collection_stage(stage.identity)
    calls = []

    monkeypatch.setattr(prepare.library, "find_published_stage", lambda _: None)
    monkeypatch.setattr(
        prepare.ingest,
        "import_url",
        lambda url, *, stage_id, use_chapters, progress: calls.append((url, stage_id))
        or {"slug": stage.slug, "stage": "extracted"},
    )
    monkeypatch.setattr(
        prepare.forge,
        "run_collection_stage",
        lambda stage_id, **options: {"slug": stage.slug, "stage": "forged"},
    )

    result = prepare.run(
        {
            "url": "https://video.test/interrupted",
            "stage_id": stage.identity,
            "options": {},
        },
        progress=lambda _: None,
        checkpoint=lambda _: None,
    )

    assert calls == [("https://video.test/interrupted", stage.identity)]
    assert result["stage"] == "forged"


def test_forge_refuses_an_incomplete_hidden_extraction(isolated, monkeypatch):
    stage = library.begin_collection_stage(
        "url-incomplete-forge",
        title="Incomplete Story",
        source="url",
    )
    (stage.path / "001-part.mp3").write_bytes(b"partial")
    library.rescan_collection_stage(stage.identity)
    monkeypatch.setattr(forge.audio, "duration_seconds", lambda path: 60)

    with pytest.raises(RuntimeError, match="extraction is not complete"):
        forge.run_collection_stage(
            stage.identity,
            normalize=False,
            clean_titles=False,
            split_oversized=False,
        )

    assert library.list_all() == []


def test_url_retry_after_publish_interruption_returns_exactly_one_collection(
    isolated,
    monkeypatch,
):
    checkpoints = []
    imports = 0
    real_forge = forge.run_collection_stage
    interrupted = True

    def import_url(url, *, stage_id, use_chapters, progress):
        nonlocal imports
        imports += 1
        stage = library.begin_collection_stage(
            stage_id,
            title="The Wind in the Willows",
            source="url",
            extra={"url": url},
        )
        (stage.path / "001-river-bank.mp3").write_bytes(b"river bank")
        library.rescan_collection_stage(stage_id)
        return library.complete_collection_stage(stage_id)

    def publish_then_interrupt(stage_id, **options):
        nonlocal interrupted
        result = real_forge(
            stage_id,
            normalize=False,
            clean_titles=False,
            split_oversized=False,
            progress=options["progress"],
        )
        if interrupted:
            interrupted = False
            raise KeyboardInterrupt("process stopped after publication")
        return result

    monkeypatch.setattr(prepare.ingest, "import_url", import_url)
    monkeypatch.setattr(prepare.forge, "run_collection_stage", publish_then_interrupt)
    monkeypatch.setattr(forge.audio, "duration_seconds", lambda path: 60)
    payload = {"url": "https://video.test/story"}

    with pytest.raises(KeyboardInterrupt, match="after publication"):
        prepare.run(payload, progress=lambda _: None, checkpoint=checkpoints.append)

    resumed_payload = checkpoints[-1]
    result = prepare.run(
        resumed_payload,
        progress=lambda _: None,
        checkpoint=checkpoints.append,
    )

    assert imports == 1
    assert result["stage"] == "forged"
    assert len(library.list_all()) == 1


def test_ready_new_collection_publication_recovers_after_interrupted_rename(
    isolated,
    monkeypatch,
):
    source = library.begin_collection_stage(
        "url-recovery",
        title="Recovery Story",
        source="url",
    )
    (source.path / "one.mp3").write_bytes(b"prepared")
    library.rescan_collection_stage(source.identity)
    library.complete_collection_stage(source.identity)
    stage = library.create_forge_stage_from_collection_stage(
        source.identity,
        "prepare-url-recovery",
    )
    library.set_forge_state_at_path(stage, {"normalized": True})
    visible = config.LIBRARY_DIR / source.slug
    real_replace = Path.replace

    def interrupt(stage_path: Path, target: Path):
        if stage_path == stage and target == visible:
            raise OSError("publication rename stopped")
        return real_replace(stage_path, target)

    monkeypatch.setattr(Path, "replace", interrupt)
    with pytest.raises(OSError, match="rename stopped"):
        library.publish_forged_collection_stage(
            source.identity,
            stage,
            "prepare-url-recovery",
        )

    assert library.list_all() == []
    assert stage.is_dir()

    monkeypatch.setattr(Path, "replace", real_replace)
    library.recover_collection_publications()

    assert [item["slug"] for item in library.list_all()] == ["recovery-story"]
    assert library.get("recovery-story")["stage"] == "forged"
    assert not source.path.exists()


def test_recovery_consumes_published_hidden_source_before_retry_and_completion(
    isolated,
    monkeypatch,
):
    source = library.begin_collection_stage(
        "url-visible-before-source-cleanup",
        title="Visible Receipt",
        source="url",
    )
    (source.path / "one.mp3").write_bytes(b"prepared")
    library.rescan_collection_stage(source.identity)
    library.complete_collection_stage(source.identity)
    failed_id = db.create_job(
        "prepare_url",
        "Visible receipt",
        {"url": "https://video.test/receipt", "stage_id": source.identity},
    )
    db.update_job(failed_id, status="failed", error="stopped after publication")
    monkeypatch.setattr(forge.audio, "duration_seconds", lambda path: 60)
    real_discard = library.discard_collection_stage
    interrupted = True

    def interrupt_cleanup(identity: str):
        nonlocal interrupted
        if interrupted and identity == source.identity:
            interrupted = False
            raise KeyboardInterrupt("process stopped before source cleanup")
        real_discard(identity)

    monkeypatch.setattr(library, "discard_collection_stage", interrupt_cleanup)
    with pytest.raises(KeyboardInterrupt, match="before source cleanup"):
        forge.run_collection_stage(
            source.identity,
            normalize=False,
            clean_titles=False,
            split_oversized=False,
        )

    assert [item["slug"] for item in library.list_all()] == [source.slug]
    assert source.path.is_dir()
    assert source.identity in db.referenced_collection_stage_ids()

    monkeypatch.setattr(library, "discard_collection_stage", real_discard)
    library.recover_collection_publications()
    assert not source.path.exists()
    recovered = forge.run_collection_stage(
        source.identity,
        normalize=False,
        clean_titles=False,
        split_oversized=False,
    )
    db.update_job(failed_id, status="done", result=recovered, error="")
    library.sweep_collection_stages(db.referenced_collection_stage_ids())

    assert recovered["slug"] == source.slug
    assert not source.path.exists()
    assert [item["slug"] for item in library.list_all()] == [source.slug]


def test_sweep_consumes_published_hidden_source_despite_failed_job_reference(
    isolated,
    monkeypatch,
):
    source = library.begin_collection_stage(
        "upload-published-before-source-cleanup",
        title="Sweep Receipt",
        source="upload",
    )
    (source.path / "one.mp3").write_bytes(b"prepared")
    library.rescan_collection_stage(source.identity)
    library.complete_collection_stage(source.identity)
    failed_id = db.create_job(
        "upload_prepare",
        "Sweep receipt",
        {"stage": "upload-retained", "collection_stage_id": source.identity},
    )
    db.update_job(failed_id, status="failed", error="stopped after publication")
    monkeypatch.setattr(forge.audio, "duration_seconds", lambda path: 60)
    real_discard = library.discard_collection_stage

    monkeypatch.setattr(
        library,
        "discard_collection_stage",
        lambda identity: (_ for _ in ()).throw(KeyboardInterrupt("cleanup stopped")),
    )
    with pytest.raises(KeyboardInterrupt, match="cleanup stopped"):
        forge.run_collection_stage(
            source.identity,
            normalize=False,
            clean_titles=False,
            split_oversized=False,
        )

    monkeypatch.setattr(library, "discard_collection_stage", real_discard)
    assert source.identity in db.referenced_collection_stage_ids()
    library.sweep_collection_stages(db.referenced_collection_stage_ids())

    assert not source.path.exists()
    assert [item["slug"] for item in library.list_all()] == [source.slug]


def test_collection_stage_sweep_keeps_retryable_jobs_and_removes_abandoned_stages(isolated):
    retryable = library.begin_collection_stage(
        "url-retryable",
        title="Retryable",
        source="url",
    )
    abandoned = library.begin_collection_stage(
        "url-abandoned",
        title="Abandoned",
        source="url",
    )
    db.create_job(
        "prepare_url",
        "Retryable",
        {"url": "https://video.test/retry", "stage_id": retryable.identity},
    )

    library.sweep_collection_stages(db.referenced_collection_stage_ids())

    assert retryable.path.is_dir()
    assert not abandoned.path.exists()


def test_replacement_publication_failure_restores_visible_collection(
    isolated,
    monkeypatch,
):
    slug = make_collection()
    visible = config.LIBRARY_DIR / slug
    replacement = library.create_replacement_stage(slug, "forge-bedtime-story")
    (replacement / "one.mp3").write_bytes(b"new one")
    (replacement / "two.mp3").write_bytes(b"new two")
    original_replace = Path.replace

    def interrupt(stage_path: Path, target: Path):
        if stage_path == replacement and target == visible:
            raise OSError("simulated publication interruption")
        return original_replace(stage_path, target)

    monkeypatch.setattr(Path, "replace", interrupt)

    with pytest.raises(OSError, match="publication interruption"):
        library.publish_replacement(slug, replacement, "forge-bedtime-story")

    assert visible.is_dir()
    assert (visible / "one.mp3").read_bytes() == b"one.mp3"
    assert library.get(slug)["stage"] == "extracted"

    monkeypatch.setattr(Path, "replace", original_replace)
    library.recover_collection_publications()
    assert visible.is_dir()
    assert not any(path.name.startswith(".toniefi-forge-") for path in config.LIBRARY_DIR.iterdir())


def test_upload_staging_uses_persistent_upload_directory_and_status_reports_limit(isolated):
    client = TestClient(main.app)
    response = client.post(
        "/api/uploads/prepare",
        files=[("files", ("story.mp3", b"story", "audio/mpeg"))],
    )

    assert response.status_code == 200
    job = db.get_job(response.json()["job_id"])
    stage = config.UPLOAD_STAGE_DIR / job["payload"]["stage"]
    assert stage.is_dir()
    assert config.WORK_DIR not in stage.parents
    status = client.get("/api/status").json()
    assert status["upload_max_bytes"] == jobs.UPLOAD_MAX_BYTES
    assert status["upload_max_human"] == "20 GiB"
    assert status["upload_stage_dir"] == str(config.UPLOAD_STAGE_DIR)


def test_push_batch_rejects_an_extracted_collection(isolated):
    slug = make_collection(stage="extracted")
    manifest = library.get(slug)
    body = {
        "operation_key": "extracted-collection",
        "slug": slug,
        "manifest_fingerprint": library.manifest_fingerprint(manifest),
        "assignments": [{
            "household_id": "house-1",
            "tonie_id": "tonie-1",
            "files": ["one.mp3", "two.mp3"],
            "replace": True,
            "remote_chapters": [],
        }],
    }

    response = TestClient(main.app).post("/api/push/batch", json=body)

    assert response.status_code == 409
    assert "Forge" in response.json()["detail"]
    assert db.jobs_for_refresh() == []


def test_push_worker_rejects_extracted_collection_before_cloud_access(isolated, monkeypatch):
    slug = make_collection(stage="extracted")
    manifest = library.get(slug)
    payload = {
        "slug": slug,
        "manifest_fingerprint": library.manifest_fingerprint(manifest),
        "household_id": "house-1",
        "tonie_id": "tonie-1",
        "files": ["one.mp3", "two.mp3"],
        "replace": True,
        "remote_chapters": [],
    }
    monkeypatch.setattr(push, "client_from_settings", lambda: pytest.fail("cloud must stay untouched"))

    with pytest.raises(push.StalePush, match="Forge"):
        push.push_confirmed(payload)


@pytest.mark.parametrize("replace", [True, False])
def test_push_rejects_projection_above_usable_headroom(isolated, monkeypatch, replace):
    slug = make_collection(stage="forged")
    manifest = library.get(slug)
    for track in manifest["tracks"]:
        track["seconds"] = 2685.5
    manifest_path = config.LIBRARY_DIR / slug / library.MANIFEST
    stored = json.loads(manifest_path.read_text(encoding="utf-8"))
    stored["tracks"] = manifest["tracks"]
    manifest_path.write_text(json.dumps(stored), encoding="utf-8")
    manifest = library.get(slug)

    class Cloud:
        def check_login(self):
            return None

        def get_tonie(self, household_id, tonie_id):
            return {
                "name": "Blue",
                "secondsPresent": 1 if not replace else 0,
                "chapters": [] if replace else [{"id": "old", "title": "Old"}],
            }

        def close(self):
            return None

    monkeypatch.setattr(push, "client_from_settings", Cloud)
    payload = {
        "slug": slug,
        "manifest_fingerprint": library.manifest_fingerprint(manifest),
        "household_id": "house-1",
        "tonie_id": "tonie-1",
        "files": ["one.mp3", "two.mp3"],
        "replace": replace,
        "remote_chapters": [] if replace else [{"id": "old", "title": "Old"}],
    }

    with pytest.raises((RuntimeError, push.StalePush), match="usable|space"):
        push.push_confirmed(payload)


def test_manual_forge_route_enqueues_once_for_extracted_collection(isolated):
    slug = make_collection(stage="extracted")
    client = TestClient(main.app)

    first = client.post("/api/forge", json={"slug": slug})
    second = client.post("/api/forge", json={"slug": slug})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    queued = [job for job in db.jobs_for_refresh() if job["kind"] == "forge"]
    assert len(queued) == 1


def test_saved_credentials_are_read_as_one_atomic_pair_during_replace(isolated, monkeypatch):
    db.replace_credentials("old@example.com", "old-secret")
    first_write = threading.Event()
    allow_replace = threading.Event()
    real_connect = db.connect

    class PausedConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, parameters=()):
            result = self.connection.execute(sql, parameters)
            if parameters == ("tonies_username", "new@example.com"):
                first_write.set()
                assert allow_replace.wait(5)
            return result

        def __getattr__(self, name):
            return getattr(self.connection, name)

    paused = PausedConnection(real_connect())
    monkeypatch.setattr(db, "connect", lambda: paused)
    observed = []
    replace_thread = threading.Thread(
        target=lambda: db.replace_credentials("new@example.com", "new-secret"),
    )
    replace_thread.start()
    assert first_write.wait(5)
    read_thread = threading.Thread(target=lambda: observed.append(db.get_credentials()))
    read_thread.start()
    allow_replace.set()
    replace_thread.join(5)
    read_thread.join(5)

    assert observed == [("new@example.com", "new-secret")]


def test_saved_credential_delete_is_interruption_safe(isolated, monkeypatch):
    db.replace_credentials("family@example.com", "secret")
    connection = db.connect()

    class InterruptedConnection:
        def execute(self, sql, parameters=()):
            if sql.startswith("DELETE FROM settings"):
                raise OSError("database interrupted")
            return connection.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(connection, name)

    monkeypatch.setattr(db, "connect", lambda: InterruptedConnection())

    with pytest.raises(OSError, match="database interrupted"):
        db.delete_credentials()

    monkeypatch.setattr(db, "connect", lambda: connection)
    assert db.get_credentials() == ("family@example.com", "secret")
    assert "secret" not in json.dumps(TestClient(main.app).get("/api/status").json())


def test_incomplete_saved_pair_never_builds_a_cloud_client(isolated, monkeypatch):
    db.replace_credentials("family@example.com", "secret")
    connection = db.connect()
    connection.execute("DELETE FROM settings WHERE key='tonies_password'")
    connection.commit()
    monkeypatch.setattr(config, "TONIES_USERNAME", "")
    monkeypatch.setattr(config, "TONIES_PASSWORD", "")

    with pytest.raises(tonies.AuthError, match="incomplete"):
        push.client_from_settings()
