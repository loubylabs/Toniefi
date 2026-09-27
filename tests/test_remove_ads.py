from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import adcut, config, db, forge, jobs, library, main, podcast
from tests.test_final_integrity import isolated, make_collection  # noqa: F401


def make_podcast(*, source: str = "podcast") -> str:
    slug = make_collection("Silly Stories", stage="forged")
    path = config.LIBRARY_DIR / slug
    manifest = json.loads((path / library.MANIFEST).read_text(encoding="utf-8"))
    for track in manifest["tracks"]:
        (path / track["name"]).unlink()
    manifest["tracks"] = [
        {"name": "001-first-story.mp3", "title": "First Story"},
        {"name": "002-second-story-part01.mp3", "title": "Second Story (part 1)"},
        {"name": "003-gone.mp3", "title": "Gone"},
    ]
    for track in manifest["tracks"]:
        (path / track["name"]).write_bytes(track["name"].encode())
    manifest.update(source=source, feed_url="https://feed.example/rss",
                    forge={"normalized": True, "titles_cleaned": True, "trim_head": 0, "trim_tail": 0, "split": True})
    (path / library.MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return slug


FEED = podcast.Feed(title="Silly Stories", author="Maker", cover=None, episodes=[
    podcast.Episode(1, "g1", "First Story", "https://cdn.example/1.mp3", None, None),
    podcast.Episode(2, "g2", "Second Story", "https://cdn.example/2.mp3", None, None),
])


@pytest.fixture
def fakes(monkeypatch):
    seen = {"downloads": [], "compared": [], "cut": []}

    monkeypatch.setattr(podcast, "read_feed", lambda url: FEED)

    def download(client, url, dest):
        seen["downloads"].append(url)
        dest.write_bytes(b"fresh")

    monkeypatch.setattr(forge.ingest, "_stream_download", download)

    def find_extra(track: Path, fresh: Path):
        seen["compared"].append((track.name, fresh.read_bytes()))
        return adcut.Finding(0.97, [(10.0, 40.0)] if track.name.startswith("001") else [])

    monkeypatch.setattr(adcut, "find_extra", find_extra)

    def cut(track: Path, cuts):
        seen["cut"].append((track.name, cuts))
        track.write_bytes(track.read_bytes() + b"|cut")

    monkeypatch.setattr(adcut, "cut", cut)
    monkeypatch.setattr(forge.audio, "duration_seconds", lambda path: 1000)
    return seen


def test_remove_ads_cuts_each_matched_chapter_and_records_the_check(isolated, fakes):
    slug = make_podcast()
    path = config.LIBRARY_DIR / slug

    result = forge.remove_ads(slug, operation_id="ads-a")

    assert sorted(fakes["downloads"]) == ["https://cdn.example/1.mp3", "https://cdn.example/2.mp3"]
    assert [name for name, _ in fakes["compared"]] == ["001-first-story.mp3", "002-second-story-part01.mp3"]
    assert fakes["cut"] == [("001-first-story.mp3", [(10.0, 40.0)])]
    assert (path / "001-first-story.mp3").read_bytes() == b"001-first-story.mp3|cut"
    assert (path / "002-second-story-part01.mp3").read_bytes() == b"002-second-story-part01.mp3"
    state = result["forge"]
    assert state["ads_cut_seconds"] == 30.0
    assert state["ads_last"]["cut_seconds"] == 30.0
    assert state["ads_last"]["chapters_changed"] == 1
    assert state["ads_last"]["unchecked"] == ["Gone"]
    assert state["normalized"] is True
    assert not list(config.WORK_DIR.glob("tmp-remove-ads-*"))


def test_remove_ads_adds_to_the_recorded_total(isolated, fakes):
    slug = make_podcast()
    forge.remove_ads(slug, operation_id="ads-a")
    result = forge.remove_ads(slug, operation_id="ads-b")
    assert result["forge"]["ads_cut_seconds"] == 60.0


def test_remove_ads_retry_after_publication_does_not_cut_twice(isolated, fakes):
    slug = make_podcast()
    forge.remove_ads(slug, operation_id="ads-a")
    again = forge.remove_ads(slug, operation_id="ads-a")
    assert len(fakes["cut"]) == 1
    assert again["forge"]["ads_cut_seconds"] == 30.0


def test_a_failed_download_leaves_that_chapter_unchecked(isolated, fakes, monkeypatch):
    slug = make_podcast()

    def download(client, url, dest):
        if url.endswith("2.mp3"):
            raise httpx.ConnectError("offline")
        dest.write_bytes(b"fresh")

    monkeypatch.setattr(forge.ingest, "_stream_download", download)

    result = forge.remove_ads(slug, operation_id="ads-c")

    assert result["forge"]["ads_last"]["unchecked"] == ["Second Story (part 1)", "Gone"]
    assert result["forge"]["ads_last"]["chapters_changed"] == 1


def test_a_fresh_copy_that_cannot_be_decoded_leaves_that_chapter_unchecked(isolated, fakes, monkeypatch):
    slug = make_podcast()
    path = config.LIBRARY_DIR / slug

    def find_extra(track: Path, fresh: Path):
        if track.name.startswith("002"):
            raise forge.audio.AudioError("Could not decode 001.mp3.")
        return adcut.Finding(0.97, [(10.0, 40.0)])

    monkeypatch.setattr(adcut, "find_extra", find_extra)

    result = forge.remove_ads(slug, operation_id="ads-f")

    assert result["forge"]["ads_last"]["unchecked"] == ["Second Story (part 1)", "Gone"]
    assert result["forge"]["ads_last"]["chapters_changed"] == 1
    assert (path / "001-first-story.mp3").read_bytes() == b"001-first-story.mp3|cut"


def test_a_copy_that_does_not_line_up_leaves_that_chapter_unchecked(isolated, fakes, monkeypatch):
    slug = make_podcast()

    def find_extra(track: Path, fresh: Path):
        if track.name.startswith("002"):
            return adcut.Finding(0.2, [])
        return adcut.Finding(0.97, [(10.0, 40.0)])

    monkeypatch.setattr(adcut, "find_extra", find_extra)

    result = forge.remove_ads(slug, operation_id="ads-g")

    assert result["forge"]["ads_last"]["unchecked"] == ["Second Story (part 1)", "Gone"]
    assert result["forge"]["ads_last"]["chapters_changed"] == 1
    assert fakes["cut"] == [("001-first-story.mp3", [(10.0, 40.0)])]


def test_a_failure_leaves_the_visible_collection_unchanged(isolated, fakes, monkeypatch):
    slug = make_podcast()
    path = config.LIBRARY_DIR / slug
    before = {item.name: item.read_bytes() for item in path.iterdir() if item.is_file()}

    def cut(track, cuts):
        track.write_bytes(b"half")
        raise forge.audio.AudioError("ffmpeg broke")

    monkeypatch.setattr(adcut, "cut", cut)

    with pytest.raises(forge.audio.AudioError, match="ffmpeg broke"):
        forge.remove_ads(slug, operation_id="ads-d")

    assert {item.name: item.read_bytes() for item in path.iterdir() if item.is_file()} == before
    assert not list(config.WORK_DIR.glob("tmp-remove-ads-*"))


def test_remove_ads_refuses_a_collection_that_is_not_a_podcast(isolated, fakes):
    slug = make_podcast(source="url")
    with pytest.raises(RuntimeError, match="podcast"):
        forge.remove_ads(slug, operation_id="ads-e")


def test_remove_ads_refuses_a_collection_that_has_not_finished_forge(isolated, fakes):
    slug = make_collection()
    with pytest.raises(RuntimeError, match="Finish preparation"):
        forge.remove_ads(slug, operation_id="ads-h")
    assert not list(config.WORK_DIR.glob("tmp-remove-ads-*"))


def test_downloads_happen_before_the_lease(isolated, fakes, monkeypatch):
    slug = make_podcast()
    free = []

    def download(client, url, dest):
        # The lock is reentrant, so only another thread can tell whether
        # this one holds it.
        acquired = []

        def probe():
            acquired.append(library._manifest_lock.acquire(blocking=False))
            if acquired[0]:
                library._manifest_lock.release()

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join()
        free.append(acquired[0])
        dest.write_bytes(b"fresh")

    monkeypatch.setattr(forge.ingest, "_stream_download", download)

    forge.remove_ads(slug, operation_id="ads-i")

    assert free == [True, True]


def test_remove_ads_job_runs_remove_ads(isolated, fakes):
    slug = make_podcast()
    job_id = db.create_collection_job_once("remove_ads", f"Remove ads from {slug}", {"slug": slug})

    result = jobs._handle(db.get_job(job_id))

    assert result["forge"]["ads_cut_seconds"] == 30.0


def test_remove_ads_jobs_cannot_bypass_the_once_per_collection_guard(isolated):
    with pytest.raises(ValueError, match="create_collection_job_once"):
        db.create_job("remove_ads", "Bypass", {"slug": "bypass"})


def test_route_queues_one_job_per_collection(isolated):
    slug = make_podcast()
    client = TestClient(main.app)

    first = client.post(f"/api/collections/{slug}/remove-ads")
    second = client.post(f"/api/collections/{slug}/remove-ads")

    assert first.status_code == 200
    assert second.json()["job_id"] == first.json()["job_id"]
    job = db.get_job(first.json()["job_id"])
    assert job["kind"] == "remove_ads"
    assert job["payload"]["slug"] == slug
    assert job["payload"]["remove_ads_operation_id"].startswith("remove_ads-")


def test_route_refuses_a_collection_that_is_not_a_podcast(isolated):
    slug = make_podcast(source="url")
    response = TestClient(main.app).post(f"/api/collections/{slug}/remove-ads")
    assert response.status_code == 400
    assert db.jobs_for_refresh() == []


def test_route_refuses_a_collection_that_has_not_finished_forge(isolated):
    slug = make_collection()
    response = TestClient(main.app).post(f"/api/collections/{slug}/remove-ads")
    assert response.status_code == 409


def test_route_refuses_an_unknown_collection(isolated):
    response = TestClient(main.app).post("/api/collections/nobody/remove-ads")
    assert response.status_code == 404


def test_a_finished_remove_ads_job_shows_as_ready(isolated):
    job = {"id": 1, "kind": "remove_ads", "status": "done", "payload": {"slug": "s"}, "progress": ""}
    assert jobs.present(job)["phase"] == "ready"
