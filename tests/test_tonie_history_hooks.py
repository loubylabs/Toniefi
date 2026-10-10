"""Version capture around every Tonie read and write, against stub clouds.

No network, no real account: the Tonie Cloud has no sandbox and no undo.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, db, library, main, push, tonies


class StubCloud:
    """Stands in for tonies.TonieCloud on the list and chapter-write paths."""

    def __init__(self, chapters: list[dict]) -> None:
        self.chapters = chapters
        self.set_calls: list[list[dict]] = []

    def _payload(self, tonie_id: str = "t1") -> dict:
        return {"id": tonie_id, "name": "Creative Tonie",
                "secondsPresent": sum(c["seconds"] for c in self.chapters),
                "chapters": [dict(c) for c in self.chapters]}

    def households(self) -> list[dict]:
        return [{"id": "h1", "name": "Home"}]

    def all_creative_tonies(self) -> list[dict]:
        tonie = self._payload()
        tonie["householdId"] = "h1"
        tonie["householdName"] = "Home"
        return [tonie]

    def get_tonie(self, household_id: str, tonie_id: str) -> dict:
        return self._payload(tonie_id)

    def set_chapters(self, household_id: str, tonie_id: str, chapters: list[dict]):
        self.set_calls.append([dict(c) for c in chapters])
        self.chapters = [dict(c) for c in chapters]

    def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _database():
    config.ensure_dirs()
    db.init()


@pytest.fixture
def cloud(monkeypatch) -> StubCloud:
    stub = StubCloud([
        {"id": "a", "title": "One", "file": "f-a", "seconds": 60.0, "transcoding": False},
        {"id": "b", "title": "Two", "file": "f-b", "seconds": 70.0, "transcoding": False},
    ])
    monkeypatch.setattr(push, "client_from_settings", lambda: stub)
    return stub


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


URL = "/api/tonies/h1/t1/chapters"
BASE = [{"id": "a", "title": "One"}, {"id": "b", "title": "Two"}]


def _titles(version: dict) -> list[str]:
    return [c["title"] for c in version["chapters"]]


def test_listing_records_one_seen_version_and_a_repeat_records_nothing(client, cloud):
    assert client.get("/api/tonies").status_code == 200
    stored = db.tonie_versions("h1", "t1")
    assert [(v["source"], _titles(v)) for v in stored] == [("seen", ["One", "Two"])]

    assert client.get("/api/tonies").status_code == 200
    assert len(db.tonie_versions("h1", "t1")) == 1


def test_a_chapter_write_records_the_old_list_then_the_new(client, cloud):
    resp = client.put(URL, json={
        "base": BASE,
        "chapters": [{"id": "a", "title": "Renamed"}, {"id": "b", "title": "Two"}],
    })
    assert resp.status_code == 200
    stored = db.tonie_versions("h1", "t1")
    assert [(v["source"], _titles(v)) for v in stored] == [
        ("seen", ["One", "Two"]),
        ("toniefi", ["Renamed", "Two"]),
    ]


def test_a_stale_write_still_records_what_the_cloud_holds(client, cloud):
    resp = client.put(URL, json={
        "base": [{"id": "a", "title": "One"}],
        "chapters": [{"id": "a", "title": "One"}],
    })
    assert resp.status_code == 409
    assert cloud.set_calls == []
    stored = db.tonie_versions("h1", "t1")
    assert [(v["source"], _titles(v)) for v in stored] == [("seen", ["One", "Two"])]


def test_a_broken_history_store_never_fails_a_landed_write(client, cloud, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "record_tonie_version", broken)
    resp = client.put(URL, json={
        "base": BASE,
        "chapters": [{"id": "a", "title": "Renamed"}, {"id": "b", "title": "Two"}],
    })
    assert resp.status_code == 200
    assert [c["title"] for c in cloud.set_calls[0]] == ["Renamed", "Two"]


class SendCloud:
    """Stands in for tonies.TonieCloud on the send path."""

    def __init__(self, fail_upload_at: int | None = None) -> None:
        self.chapters: list[dict] = [{"id": "old", "title": "Old", "seconds": 5.0}]
        self.uploads = 0
        self.fail_upload_at = fail_upload_at
        self.get_error: Exception | None = None

    def check_login(self):
        return {}

    def get_tonie(self, household_id, tonie_id):
        if self.get_error is not None:
            raise self.get_error
        return {"name": "Bedtime Bear", "secondsPresent": sum(c["seconds"] for c in self.chapters),
                "chapters": [dict(c) for c in self.chapters]}

    def upload_file(self, path, on_bytes=None):
        self.uploads += 1
        if self.uploads == self.fail_upload_at:
            raise RuntimeError("Upload to storage rejected (503).")
        return f"file-{self.uploads}"

    def add_chapter(self, household_id, tonie_id, title, file_id):
        self.chapters.append({"id": file_id, "title": title, "seconds": 10.0})

    def close(self):
        return None


def _send_fixture(monkeypatch, cloud: SendCloud):
    resolved = [
        ("a", {"name": "001.mp3", "title": "One", "seconds": 10, "size": 10}),
        ("a", {"name": "002.mp3", "title": "Two", "seconds": 10, "size": 10}),
    ]
    (config.LIBRARY_DIR / "a").mkdir(parents=True, exist_ok=True)
    for _, track in resolved:
        (config.LIBRARY_DIR / "a" / track["name"]).write_bytes(b"0123456789")
    monkeypatch.setattr(library, "track_path", lambda slug, name: config.LIBRARY_DIR / slug / name)
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)
    payload = {
        "household_id": "h1",
        "tonie_id": "t1",
        "replace": False,
        "remote_chapters": [{"id": "old", "title": "Old"}],
        "sources": [],
    }
    return payload, resolved


def test_a_send_records_the_list_before_and_after(monkeypatch):
    cloud = SendCloud()
    payload, resolved = _send_fixture(monkeypatch, cloud)
    push._push_confirmed_tracks(payload, resolved, lambda *_, **__: None)
    stored = db.tonie_versions("h1", "t1")
    assert [(v["source"], _titles(v)) for v in stored] == [
        ("seen", ["Old"]),
        ("toniefi", ["Old", "One", "Two"]),
    ]


def test_a_partial_send_records_what_landed(monkeypatch):
    cloud = SendCloud(fail_upload_at=2)
    payload, resolved = _send_fixture(monkeypatch, cloud)
    with pytest.raises(push.PartialSend) as caught:
        push._push_confirmed_tracks(payload, resolved, lambda *_, **__: None)
    assert caught.value.uploaded == 1
    stored = db.tonie_versions("h1", "t1")
    assert [(v["source"], _titles(v)) for v in stored] == [
        ("seen", ["Old"]),
        ("toniefi", ["Old", "One"]),
    ]


def test_a_failed_capture_read_never_masks_the_partial_send(monkeypatch):
    cloud = SendCloud(fail_upload_at=2)
    payload, resolved = _send_fixture(monkeypatch, cloud)
    original_upload = cloud.upload_file

    def upload_then_break_reads(path, on_bytes=None):
        try:
            return original_upload(path, on_bytes)
        except RuntimeError:
            cloud.get_error = tonies.TonieCloudError("myTonies is down.")
            raise

    cloud.upload_file = upload_then_break_reads
    with pytest.raises(push.PartialSend) as caught:
        push._push_confirmed_tracks(payload, resolved, lambda *_, **__: None)
    assert caught.value.uploaded == 1
    assert "Upload to storage rejected" in str(caught.value)
    stored = db.tonie_versions("h1", "t1")
    assert [v["source"] for v in stored] == ["seen"]


def test_the_versions_route_lists_newest_first_with_changes(client, cloud):
    client.get("/api/tonies")
    client.put(URL, json={
        "base": BASE,
        "chapters": [{"id": "a", "title": "Renamed"}, {"id": "b", "title": "Two"}],
    })
    resp = client.get("/api/tonies/h1/t1/versions")
    assert resp.status_code == 200
    body = resp.json()
    assert [(v["source"], _titles(v)) for v in body] == [
        ("toniefi", ["Renamed", "Two"]),
        ("seen", ["One", "Two"]),
    ]
    assert body[0]["changes"]["renamed"] == 1
    assert body[1]["changes"]["first"] is True
    assert body[0]["chapters"][0]["duration"]

    empty = client.get("/api/tonies/h1/nope/versions")
    assert empty.status_code == 200
    assert empty.json() == []
