from __future__ import annotations

import json
import threading
from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs, library, main, push


class ObservableRLock:
    def __init__(self, contended: threading.Event):
        self._condition = threading.Condition()
        self._owner = None
        self._depth = 0
        self._contended = contended

    def __enter__(self):
        ident = threading.get_ident()
        with self._condition:
            if self._owner == ident:
                self._depth += 1
                return self
            if self._owner is not None:
                self._contended.set()
            while self._owner is not None:
                self._condition.wait()
            self._owner = ident
            self._depth = 1
        return self

    def __exit__(self, *_):
        with self._condition:
            self._depth -= 1
            if self._depth == 0:
                self._owner = None
                self._condition.notify_all()


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
    monkeypatch.setattr(config, "TONIE_LIMIT_SECONDS", 5400)
    config.ensure_dirs()
    db.init()
    slug = library.create("Night Stories")
    path = config.LIBRARY_DIR / slug
    for name in ("one.mp3", "two.mp3"):
        (path / name).write_bytes(name.encode())
    manifest_path = path / library.MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["stage"] = "forged"
    manifest["tracks"] = [
        {"name": "one.mp3", "title": "One", "seconds": 1000, "size": 7, "mtime": 1},
        {"name": "two.mp3", "title": "Two", "seconds": 1000, "size": 7, "mtime": 1},
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    yield slug
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


def batch_body(slug: str) -> dict:
    manifest = library.get(slug)
    return {
        "operation_key": "send-night-stories-001",
        "assignments": [{
            "household_id": "house-1",
            "tonie_id": "tonie-1",
            "replace": False,
            "remote_chapters": [
                {"id": "remote-a", "title": "Already there"},
            ],
            "sources": [{
                "slug": slug,
                "manifest_fingerprint": manifest["manifest_fingerprint"],
                "files": ["one.mp3", "two.mp3"],
            }],
        }],
    }


def second_collection(title: str, tracks: list[tuple[str, str, int]]) -> str:
    """A second forged collection, so a batch can carry more than one slug."""
    slug = library.create(title)
    path = config.LIBRARY_DIR / slug
    for name, _, _ in tracks:
        (path / name).write_bytes(name.encode())
    manifest_path = path / library.MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["stage"] = "forged"
    manifest["tracks"] = [
        {"name": name, "title": track_title, "seconds": seconds, "size": len(name), "mtime": 1}
        for name, track_title, seconds in tracks
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return slug


def test_batch_enqueue_is_atomic_and_idempotent(isolated):
    client = TestClient(main.app)
    body = batch_body(isolated)

    first = client.post("/api/push/batch", json=body)
    repeated = client.post("/api/push/batch", json=body)

    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    assert first.json()["operation_key"] == body["operation_key"]
    assert len(first.json()["job_ids"]) == 1
    stored = db.get_job(first.json()["job_ids"][0])
    source = stored["payload"]["sources"][0]
    assert source["files"] == ["one.mp3", "two.mp3"]
    assert source["manifest_fingerprint"] == body["assignments"][0]["sources"][0]["manifest_fingerprint"]
    assert stored["payload"]["remote_chapters"] == body["assignments"][0]["remote_chapters"]
    assert len(db.jobs_for_refresh()) == 1


def test_confirmed_receipt_is_recoverable_after_local_state_moves_on(isolated):
    client = TestClient(main.app)
    body = batch_body(isolated)
    first = client.post("/api/push/batch", json=body)
    library.rename_track(isolated, "one.mp3", "Changed after enqueue")

    repeated = client.post("/api/push/batch", json=body)

    assert repeated.status_code == 200
    assert repeated.json() == first.json()


def test_operation_key_reuse_with_different_payload_conflicts(isolated):
    client = TestClient(main.app)
    body = batch_body(isolated)
    assert client.post("/api/push/batch", json=body).status_code == 200
    body["assignments"][0]["replace"] = True

    conflict = client.post("/api/push/batch", json=body)

    assert conflict.status_code == 409
    assert len(db.jobs_for_refresh()) == 1


def test_old_single_push_endpoint_is_retired(isolated):
    response = TestClient(main.app).post("/api/push", json={
        "slug": isolated,
        "household_id": "house-1",
        "tonie_id": "tonie-1",
        "group_index": 1,
        "replace": True,
    })
    assert response.status_code == 404


class StubCloud:
    def __init__(self):
        self.chapters = [{"id": "remote-a", "title": "Already there", "seconds": 300}]
        self.calls = []

    def check_login(self):
        self.calls.append("login")

    def get_tonie(self, household_id, tonie_id):
        self.calls.append("get")
        return {
            "id": tonie_id,
            "name": "Blue",
            "secondsPresent": sum(item.get("seconds", 0) for item in self.chapters),
            "chapters": [dict(item) for item in self.chapters],
        }

    def clear_tonie(self, household_id, tonie_id):
        self.calls.append("clear")
        self.chapters = []

    def upload_file(self, path):
        self.calls.append(f"upload:{path.name}")
        return f"file-{path.name}"

    def add_chapter(self, household_id, tonie_id, title, file_id):
        self.calls.append(f"add:{title}")
        self.chapters.append({"id": file_id, "title": title, "seconds": 1000})

    def close(self):
        self.calls.append("close")


def test_worker_fails_stale_remote_before_replace_or_upload(isolated, monkeypatch):
    body = batch_body(isolated)
    cloud = StubCloud()
    cloud.chapters[0]["title"] = "Changed elsewhere"
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)
    payload = {**body["assignments"][0], "replace": True}

    with pytest.raises(push.StalePush, match="Tonie changed"):
        push.push_confirmed(payload)

    assert "clear" not in cloud.calls
    assert not any(call.startswith("upload:") for call in cloud.calls)


def test_worker_fails_stale_local_fingerprint_before_cloud_access(isolated, monkeypatch):
    body = batch_body(isolated)
    library.rename_track(isolated, "one.mp3", "Changed locally")
    cloud = StubCloud()
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)
    payload = body["assignments"][0]

    with pytest.raises(push.StalePush, match="collection changed"):
        push.push_confirmed(payload)

    assert cloud.calls == []


def test_worker_revalidates_append_capacity_from_fresh_remote_read(isolated, monkeypatch):
    body = batch_body(isolated)
    cloud = StubCloud()
    cloud.chapters = [{"id": "remote-a", "title": "Already there", "seconds": 4000}]
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)
    payload = body["assignments"][0]

    with pytest.raises(push.StalePush, match="no longer has enough free space"):
        push.push_confirmed(payload)

    assert not any(call.startswith("upload:") for call in cloud.calls)


def test_worker_uses_exact_confirmed_files_and_titles(isolated, monkeypatch):
    body = batch_body(isolated)
    cloud = StubCloud()
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)
    payload = body["assignments"][0]

    result = push.push_confirmed(payload)

    assert [call for call in cloud.calls if call.startswith("upload:")] == [
        "upload:one.mp3", "upload:two.mp3",
    ]
    assert [call for call in cloud.calls if call.startswith("add:")] == ["add:One", "add:Two"]
    assert result["uploaded"][0]["title"] == "One"


def test_worker_dispatch_has_no_group_index_resolution(isolated, monkeypatch):
    body = batch_body(isolated)
    cloud = StubCloud()
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)
    payload = body["assignments"][0]
    job = {"id": db.create_job("push", "Send", payload), "kind": "push", "payload": payload}

    jobs._handle(job)

    assert "group_index" not in payload
    assert "upload:one.mp3" in cloud.calls


def test_manifest_mutations_share_one_read_modify_write_lock(isolated, monkeypatch):
    real_read = library._read_manifest
    first_read = threading.Event()
    resume = threading.Event()
    paused = False

    def read_with_pause(path):
        nonlocal paused
        result = real_read(path)
        if threading.current_thread().name == "title-writer" and not paused:
            paused = True
            first_read.set()
            assert resume.wait(5)
        return result

    monkeypatch.setattr(library, "_read_manifest", read_with_pause)
    title_writer = threading.Thread(
        name="title-writer",
        target=lambda: library.set_title(isolated, "Renamed collection"),
    )
    track_writer = threading.Thread(
        name="track-writer",
        target=lambda: library.rename_track(isolated, "one.mp3", "Renamed track"),
    )
    title_writer.start()
    assert first_read.wait(5)
    track_writer.start()
    assert track_writer.is_alive()
    resume.set()
    title_writer.join(5)
    track_writer.join(5)

    manifest = library.get(isolated)
    assert manifest["title"] == "Renamed collection"
    assert manifest["tracks"][0]["title"] == "Renamed track"


def test_two_push_workers_serialize_one_tonie_from_read_through_upload(isolated, monkeypatch):
    body = batch_body(isolated)
    entered_final_read = threading.Event()
    release_final_read = threading.Event()
    second_contended = threading.Event()
    events = []
    thread_errors = []

    class SharedCloud(StubCloud):
        def __init__(self):
            super().__init__()
            self.reads = {}

        def get_tonie(self, household_id, tonie_id):
            name = threading.current_thread().name
            self.reads[name] = self.reads.get(name, 0) + 1
            if name == "push-worker-one" and self.reads[name] == 2:
                events.append("first-final-read-enter")
                entered_final_read.set()
                assert release_final_read.wait(5)
                events.append("first-final-read-return")
            elif name == "push-worker-two":
                events.append("second-read")
            return super().get_tonie(household_id, tonie_id)

    cloud = SharedCloud()
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)
    monkeypatch.setattr(library, "collection_lease", nullcontext)
    monkeypatch.setattr(push, "_target_locks", {
        ("house-1", "tonie-1"): ObservableRLock(second_contended),
    })
    payload = {**body["assignments"][0], "replace": True}

    def run_push():
        try:
            push.push_confirmed(payload)
        except BaseException as exc:
            thread_errors.append(exc)

    first = threading.Thread(name="push-worker-one", target=run_push)
    second = threading.Thread(name="push-worker-two", target=run_push)
    first.start()
    assert entered_final_read.wait(5)
    second.start()
    assert second_contended.wait(5)
    release_final_read.set()
    first.join(5)
    second.join(5)

    assert not first.is_alive() and not second.is_alive()
    assert events.index("first-final-read-return") < events.index("second-read")
    assert len(thread_errors) == 1
    assert isinstance(thread_errors[0], push.StalePush)
    assert cloud.calls.count("clear") == 1
    assert len([call for call in cloud.calls if call.startswith("upload:")]) == 2


def test_chapter_write_waits_for_confirmed_push_final_read(
    isolated,
    monkeypatch,
):
    body = batch_body(isolated)
    entered_final_read = threading.Event()
    release_final_read = threading.Event()
    chapter_contended = threading.Event()
    events = []
    thread_errors = []

    class PausedCloud(StubCloud):
        def __init__(self):
            super().__init__()
            self.push_reads = 0

        def get_tonie(self, household_id, tonie_id):
            if threading.current_thread().name == "confirmed-push":
                self.push_reads += 1
                if self.push_reads == 2:
                    events.append("push-final-read-enter")
                    entered_final_read.set()
                    assert release_final_read.wait(5)
                    events.append("push-final-read-return")
            else:
                events.append("chapter-read")
            return super().get_tonie(household_id, tonie_id)

        def set_chapters(self, household_id, tonie_id, chapters):
            events.append("chapter-mutation")
            self.chapters = list(chapters)

        def households(self):
            return [{"id": "house-1", "name": "Home"}]

    cloud = PausedCloud()
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)
    monkeypatch.setattr(push, "_target_locks", {
        ("house-1", "tonie-1"): ObservableRLock(chapter_contended),
    })
    payload = {**body["assignments"][0], "replace": True}

    def run(target):
        try:
            target()
        except BaseException as exc:
            thread_errors.append(exc)

    push_thread = threading.Thread(name="confirmed-push", target=run, args=(lambda: push.push_confirmed(payload),))
    chapter_thread = threading.Thread(name="chapter-writer", target=run, args=(lambda: push.set_tonie_chapters(
        "house-1",
        "tonie-1",
        [
            {"id": "file-one.mp3", "title": "One"},
            {"id": "file-two.mp3", "title": "Two"},
        ],
        [
            {"id": "file-one.mp3", "title": "One"},
            {"id": "file-two.mp3", "title": "Two"},
        ],
    ),))
    push_thread.start()
    assert entered_final_read.wait(5)
    chapter_thread.start()
    assert chapter_contended.wait(5)
    release_final_read.set()
    push_thread.join(5)
    chapter_thread.join(5)

    assert not push_thread.is_alive() and not chapter_thread.is_alive()
    assert thread_errors == []
    assert events.index("push-final-read-return") < events.index("chapter-read")
    assert events.index("chapter-read") < events.index("chapter-mutation")


def test_collection_lease_blocks_local_mutation_from_remote_read_through_upload(
    isolated,
    monkeypatch,
):
    body = batch_body(isolated)
    entered_remote_read = threading.Event()
    release_remote_read = threading.Event()
    mutation_contended = threading.Event()
    events = []
    thread_errors = []

    class PausedCloud(StubCloud):
        def __init__(self):
            super().__init__()
            self.read_count = 0

        def get_tonie(self, household_id, tonie_id):
            self.read_count += 1
            events.append(f"remote-read-{self.read_count}")
            if self.read_count == 1:
                entered_remote_read.set()
                assert release_remote_read.wait(5)
            return super().get_tonie(household_id, tonie_id)

    cloud = PausedCloud()
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)
    monkeypatch.setattr(library, "_manifest_lock", ObservableRLock(mutation_contended))
    payload = {**body["assignments"][0], "replace": True}

    def run_push():
        try:
            push.push_confirmed(payload)
        except BaseException as exc:
            thread_errors.append(exc)

    def remove_confirmed_file():
        try:
            library.delete_track(isolated, "one.mp3")
            events.append("mutation-done")
        except BaseException as exc:
            thread_errors.append(exc)

    worker = threading.Thread(name="leased-push", target=run_push)
    mutation = threading.Thread(name="local-mutation", target=remove_confirmed_file)
    worker.start()
    assert entered_remote_read.wait(5)
    mutation.start()
    assert mutation_contended.wait(5)
    release_remote_read.set()
    worker.join(5)
    mutation.join(5)

    assert not worker.is_alive() and not mutation.is_alive()
    assert thread_errors == []
    assert events.index("remote-read-2") < events.index("mutation-done")
    assert not (config.LIBRARY_DIR / isolated / "one.mp3").exists()


def test_failed_push_job_cannot_be_cloned_by_generic_retry(isolated):
    client = TestClient(main.app)
    body = batch_body(isolated)
    receipt = client.post("/api/push/batch", json=body).json()
    job_id = receipt["job_ids"][0]
    original = db.get_job(job_id)
    db.update_job(job_id, status="failed", error="remote stale")

    response = client.post(f"/api/jobs/{job_id}/retry")

    assert response.status_code == 409
    assert response.json()["detail"] == "Creative Tonie sends must be selected and confirmed again in the Library."
    assert db.jobs_for_refresh()[0]["id"] == job_id
    assert db.get_job(job_id)["payload"] == original["payload"]
    assert jobs.present(db.get_job(job_id))["retryable"] is False


def test_storage_clone_refuses_failed_push_without_jobs_service(isolated):
    job_id = db.create_job("push", "Confirmed send", {"operation_key": "one"})
    db.update_job(job_id, status="failed", error="remote stale")

    assert db.clone_failed_job(job_id) == 0
    assert [job["id"] for job in db.jobs_for_refresh()] == [job_id]


def test_failed_prepare_job_remains_explicitly_retryable(isolated):
    job_id = db.create_job("prepare_url", "Prepare", {"url": "https://example.test/story"})
    db.update_job(job_id, status="failed", error="network")

    assert jobs.present(db.get_job(job_id))["retryable"] is True


def test_two_collections_send_to_one_tonie_as_one_job(isolated):
    """The whole point of the change: one Tonie, several stories, one action."""
    client = TestClient(main.app)
    other = second_collection("Sea Tales", [("three.mp3", "Three", 500)])
    body = batch_body(isolated)
    body["assignments"][0]["sources"].append({
        "slug": other,
        "manifest_fingerprint": library.get(other)["manifest_fingerprint"],
        "files": ["three.mp3"],
    })

    response = client.post("/api/push/batch", json=body)

    assert response.status_code == 200
    job_ids = response.json()["job_ids"]
    assert len(job_ids) == 1
    stored = db.get_job(job_ids[0])
    assert [source["slug"] for source in stored["payload"]["sources"]] == [isolated, other]


def test_a_partial_collection_is_refused(isolated):
    """Sending half a story is never what the operator confirmed."""
    client = TestClient(main.app)
    body = batch_body(isolated)
    body["assignments"][0]["sources"][0]["files"] = ["one.mp3"]

    response = client.post("/api/push/batch", json=body)

    assert response.status_code == 409
    assert db.jobs_for_refresh(limit=10) == []


def test_a_duplicated_track_is_refused(isolated):
    client = TestClient(main.app)
    body = batch_body(isolated)
    body["assignments"][0]["sources"][0]["files"] = ["one.mp3", "one.mp3", "two.mp3"]

    response = client.post("/api/push/batch", json=body)

    assert response.status_code == 409
    assert db.jobs_for_refresh(limit=10) == []


def test_tracks_out_of_manifest_order_are_refused(isolated):
    """Manifest order is the reviewed order, so the payload cannot restate it."""
    client = TestClient(main.app)
    body = batch_body(isolated)
    body["assignments"][0]["sources"][0]["files"] = ["two.mp3", "one.mp3"]

    response = client.post("/api/push/batch", json=body)

    assert response.status_code == 409
    assert db.jobs_for_refresh(limit=10) == []


def test_interleaved_collections_are_refused(isolated):
    """A1, B1, A2 is an order no capacity plan produces."""
    client = TestClient(main.app)
    other = second_collection("Sea Tales", [("three.mp3", "Three", 500)])
    body = batch_body(isolated)
    body["assignments"][0]["sources"] = [
        {"slug": isolated, "manifest_fingerprint": library.get(isolated)["manifest_fingerprint"], "files": ["one.mp3"]},
        {"slug": other, "manifest_fingerprint": library.get(other)["manifest_fingerprint"], "files": ["three.mp3"]},
        {"slug": isolated, "manifest_fingerprint": library.get(isolated)["manifest_fingerprint"], "files": ["two.mp3"]},
    ]

    response = client.post("/api/push/batch", json=body)

    assert response.status_code == 409
    assert db.jobs_for_refresh(limit=10) == []


def test_one_collection_split_across_two_tonies_is_accepted(isolated, monkeypatch):
    """A collection over the usable limit legitimately spans two assignments."""
    client = TestClient(main.app)
    body = batch_body(isolated)
    body["assignments"] = [
        {
            "household_id": "house-1",
            "tonie_id": "tonie-1",
            "replace": False,
            "remote_chapters": [],
            "sources": [{
                "slug": isolated,
                "manifest_fingerprint": library.get(isolated)["manifest_fingerprint"],
                "files": ["one.mp3"],
            }],
        },
        {
            "household_id": "house-1",
            "tonie_id": "tonie-2",
            "replace": False,
            "remote_chapters": [],
            "sources": [{
                "slug": isolated,
                "manifest_fingerprint": library.get(isolated)["manifest_fingerprint"],
                "files": ["two.mp3"],
            }],
        },
    ]
    # Each track is 1000s; a 1500s usable limit puts them in separate groups.
    monkeypatch.setattr(config, "TONIE_LIMIT_SECONDS", 1500)

    response = client.post("/api/push/batch", json=body)

    assert response.status_code == 200
    assert len(response.json()["job_ids"]) == 2


def test_a_selection_that_overflows_one_tonie_is_refused(isolated, monkeypatch):
    """Two collections that do not pack into one group cannot claim they do."""
    client = TestClient(main.app)
    body = batch_body(isolated)
    monkeypatch.setattr(config, "TONIE_LIMIT_SECONDS", 1500)

    response = client.post("/api/push/batch", json=body)

    assert response.status_code == 409
    assert db.jobs_for_refresh(limit=10) == []


def test_worker_uploads_tracks_from_two_collections(isolated, monkeypatch):
    """Each track resolves through its own slug, or the second story 404s."""
    other = second_collection("Sea Tales", [("three.mp3", "Three", 500)])
    payload = {
        "household_id": "house-1",
        "tonie_id": "tonie-1",
        "replace": False,
        "remote_chapters": [],
        "sources": [
            {"slug": isolated, "manifest_fingerprint": library.get(isolated)["manifest_fingerprint"], "files": ["one.mp3", "two.mp3"]},
            {"slug": other, "manifest_fingerprint": library.get(other)["manifest_fingerprint"], "files": ["three.mp3"]},
        ],
    }
    uploaded: list[str] = []

    class FakeClient:
        def check_login(self): return None
        def get_tonie(self, *_): return {"chapters": [], "secondsPresent": 0, "name": "Emily"}
        def upload_file(self, path):
            uploaded.append(path.name)
            return f"file-{path.name}"
        def add_chapter(self, *_): return None
        def clear_tonie(self, *_): return None
        def close(self): return None

    monkeypatch.setattr(push, "client_from_settings", lambda: FakeClient())

    result = push.push_confirmed(payload)

    assert uploaded == ["one.mp3", "two.mp3", "three.mp3"]
    assert result["chapters"] == 0
