from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs, library, main, push


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
        "slug": slug,
        "manifest_fingerprint": library.manifest_fingerprint(manifest),
        "assignments": [{
            "household_id": "house-1",
            "tonie_id": "tonie-1",
            "files": ["one.mp3", "two.mp3"],
            "replace": False,
            "remote_chapters": [
                {"id": "remote-a", "title": "Already there"},
            ],
        }],
    }


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
    assert stored["payload"]["files"] == ["one.mp3", "two.mp3"]
    assert stored["payload"]["manifest_fingerprint"] == body["manifest_fingerprint"]
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
    payload = {**body["assignments"][0], "slug": isolated,
               "manifest_fingerprint": body["manifest_fingerprint"]}
    payload["replace"] = True

    with pytest.raises(push.StalePush, match="Tonie changed"):
        push.push_confirmed(payload)

    assert "clear" not in cloud.calls
    assert not any(call.startswith("upload:") for call in cloud.calls)


def test_worker_fails_stale_local_fingerprint_before_cloud_access(isolated, monkeypatch):
    body = batch_body(isolated)
    library.rename_track(isolated, "one.mp3", "Changed locally")
    cloud = StubCloud()
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)
    payload = {**body["assignments"][0], "slug": isolated,
               "manifest_fingerprint": body["manifest_fingerprint"]}

    with pytest.raises(push.StalePush, match="collection changed"):
        push.push_confirmed(payload)

    assert cloud.calls == []


def test_worker_revalidates_append_capacity_from_fresh_remote_read(isolated, monkeypatch):
    body = batch_body(isolated)
    cloud = StubCloud()
    cloud.chapters = [{"id": "remote-a", "title": "Already there", "seconds": 4000}]
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)
    payload = {**body["assignments"][0], "slug": isolated,
               "manifest_fingerprint": body["manifest_fingerprint"]}

    with pytest.raises(push.StalePush, match="no longer has enough free space"):
        push.push_confirmed(payload)

    assert not any(call.startswith("upload:") for call in cloud.calls)


def test_worker_uses_exact_confirmed_files_and_titles(isolated, monkeypatch):
    body = batch_body(isolated)
    cloud = StubCloud()
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)
    payload = {**body["assignments"][0], "slug": isolated,
               "manifest_fingerprint": body["manifest_fingerprint"]}

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
    payload = {**body["assignments"][0], "slug": isolated,
               "manifest_fingerprint": body["manifest_fingerprint"]}
    job = {"id": db.create_job("push", "Send", payload), "kind": "push", "payload": payload}

    jobs._handle(job)

    assert "group_index" not in payload
    assert "upload:one.mp3" in cloud.calls


def test_manifest_mutations_share_one_read_modify_write_lock(isolated, monkeypatch):
    import threading

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
