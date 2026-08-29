"""Peeking at a playlist, and downloading only the entries you picked."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import audio, config, db, ingest, main, prepare


@pytest.fixture
def flat_playlist(monkeypatch):
    """Answer yt-dlp's flat-playlist probe with a canned payload."""
    recorded: list[list[str]] = []

    def install(payload: dict, returncode: int = 0, stderr: str = "") -> list[list[str]]:
        def run(command, **kwargs):
            recorded.append(list(command))
            return SimpleNamespace(returncode=returncode, stdout=json.dumps(payload), stderr=stderr)

        monkeypatch.setattr(ingest.shutil, "which", lambda name: "/usr/bin/yt-dlp")
        monkeypatch.setattr(ingest.subprocess, "run", run)
        return recorded

    return install


def test_playlist_preview_numbers_every_entry_from_one(flat_playlist):
    flat_playlist({
        "_type": "playlist",
        "title": "How Search Works",
        "entries": [
            {"id": "aaa", "title": "First", "duration": 61},
            {"id": "bbb", "title": "Second", "duration": 122.5},
        ],
    })

    preview = ingest.playlist_preview("https://www.youtube.com/playlist?list=PL1")

    assert preview["title"] == "How Search Works"
    assert preview["entries"] == [
        {"index": 1, "id": "aaa", "title": "First", "available": True},
        {"index": 2, "id": "bbb", "title": "Second", "available": True},
    ]


def test_playlist_preview_keeps_the_number_of_an_unavailable_entry(flat_playlist):
    flat_playlist({
        "_type": "playlist",
        "title": "Mixed",
        "entries": [
            {"id": "aaa", "title": "First", "duration": 10},
            None,
            {"id": "ccc", "title": "Third", "duration": 30},
        ],
    })

    entries = ingest.playlist_preview("https://www.youtube.com/playlist?list=PL1")["entries"]

    assert [entry["index"] for entry in entries] == [1, 2, 3]
    assert [entry["available"] for entry in entries] == [True, False, True]


def test_playlist_preview_of_a_single_video_has_no_entries(flat_playlist):
    flat_playlist({"_type": "video", "id": "aaa", "title": "Just One"})

    preview = ingest.playlist_preview("https://www.youtube.com/watch?v=aaa")

    assert preview["entries"] == []


def test_playlist_preview_reports_what_yt_dlp_complained_about(flat_playlist):
    flat_playlist({}, returncode=1, stderr="ERROR: playlist does not exist")

    with pytest.raises(RuntimeError, match="playlist does not exist"):
        ingest.playlist_preview("https://www.youtube.com/playlist?list=nope")


@pytest.fixture
def isolated_library(monkeypatch, tmp_path):
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "portal.db")
    config.ensure_dirs()
    db.init()
    monkeypatch.setattr(audio, "duration_seconds", lambda path: 10)
    yield
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


@pytest.fixture
def fake_download(monkeypatch):
    """Stand in for yt-dlp, recording its command and laying down its output."""
    recorded: list[list[str]] = []

    def install(videos, chapters=(), info=None):
        def run(command, **kwargs):
            recorded.append(list(command))
            root = Path(command[command.index("-o") + 1]).parent
            for name in videos:
                (root / name).write_bytes(name.encode())
            if chapters:
                chapter_root = Path(command[command.index("-o", command.index("-o") + 1) + 1]
                                    .split(":", 1)[1]).parent
                chapter_root.mkdir(parents=True, exist_ok=True)
                for name in chapters:
                    (chapter_root / name).write_bytes(name.encode())
            (root / "video.info.json").write_text(json.dumps(info or {}), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(ingest.shutil, "which", lambda name: "/usr/bin/yt-dlp")
        monkeypatch.setattr(ingest.subprocess, "run", run)
        return recorded

    return install


def test_a_video_link_that_also_names_a_playlist_downloads_only_that_video(
    isolated_library, fake_download,
):
    recorded = fake_download(["000-Only.mp3"])

    ingest.import_url(
        "https://www.youtube.com/watch?v=aaa&list=PL1",
        stage_id="url-single",
        use_chapters=False,
    )

    command = recorded[0]
    assert "--no-playlist" in command
    assert "--yes-playlist" not in command


def test_picked_playlist_numbers_reach_yt_dlp(isolated_library, fake_download):
    recorded = fake_download(["001-One.mp3", "003-Three.mp3"])

    ingest.import_url(
        "https://www.youtube.com/watch?v=aaa&list=PL1",
        stage_id="url-picked",
        use_chapters=False,
        playlist_items=[1, 3, 4, 5],
    )

    command = recorded[0]
    assert "--yes-playlist" in command
    assert "--no-playlist" not in command
    assert command[command.index("--playlist-items") + 1] == "1,3:5"


def test_a_playlist_keeps_videos_that_have_no_chapters(isolated_library, fake_download):
    fake_download(
        ["001-Video One.mp3", "002-Video Two.mp3"],
        chapters=["001-001-Opening.mp3", "001-002-Middle.mp3"],
        info={"title": "Video One", "playlist_title": "Story Time"},
    )

    result = ingest.import_url(
        "https://www.youtube.com/playlist?list=PL1",
        stage_id="url-mixed",
        playlist_items=[1, 2],
    )

    assert [track["title"] for track in result["tracks"]] == ["Opening", "Middle", "Video Two"]


def test_chapter_files_are_named_with_their_video_number(isolated_library, fake_download):
    recorded = fake_download(["001-Video One.mp3"])

    ingest.import_url("https://www.youtube.com/playlist?list=PL1", stage_id="url-template")

    template = next(part for part in recorded[0] if part.startswith("chapter:"))
    assert "%(playlist_index" in template


def test_a_playlist_is_named_after_the_playlist_not_its_first_video(isolated_library, fake_download):
    fake_download(
        ["001-Video One.mp3", "002-Video Two.mp3"],
        info={"title": "Video One", "playlist_title": "Story Time"},
    )

    result = ingest.import_url(
        "https://www.youtube.com/playlist?list=PL1",
        stage_id="url-named",
        playlist_items=[1, 2],
    )

    assert result["title"] == "Story Time"


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def test_preview_route_lists_the_playlist(client, flat_playlist):
    flat_playlist({
        "_type": "playlist",
        "title": "How Search Works",
        "entries": [{"id": "aaa", "title": "First", "duration": 61}],
    })

    response = client.post("/api/playlist/preview", json={"url": "https://www.youtube.com/playlist?list=PL1"})

    assert response.status_code == 200
    assert response.json() == {
        "title": "How Search Works",
        "entries": [{"index": 1, "id": "aaa", "title": "First", "available": True}],
    }


def test_preview_route_refuses_a_url_that_is_not_http(client):
    response = client.post("/api/playlist/preview", json={"url": "file:///etc/passwd"})

    assert response.status_code == 400


def test_preview_route_passes_on_what_yt_dlp_complained_about(client, flat_playlist):
    flat_playlist({}, returncode=1, stderr="ERROR: playlist does not exist")

    response = client.post("/api/playlist/preview", json={"url": "https://www.youtube.com/playlist?list=nope"})

    assert response.status_code == 502
    assert "playlist does not exist" in response.json()["detail"]


def test_prepare_carries_the_picked_numbers_into_the_job(client, monkeypatch):
    payloads = []
    monkeypatch.setattr(main.jobs, "enqueue_many",
                        lambda entries: payloads.extend(entry[2] for entry in entries) or ["job-1"])

    response = client.post("/api/prepare", json={
        "sources": [{"url": "https://www.youtube.com/playlist?list=PL1", "playlist_items": [1, 3]}],
    })

    assert response.status_code == 200
    assert payloads[0]["playlist_items"] == [1, 3]


def test_prepare_run_forwards_the_picked_numbers_to_the_download(monkeypatch):
    seen = {}
    monkeypatch.setattr(prepare.ingest, "import_url",
                        lambda url, **kw: seen.update(kw) or {"slug": "alice"})
    monkeypatch.setattr(prepare.forge, "run_collection_stage", lambda stage_id, **kw: {"slug": "alice"})
    monkeypatch.setattr(prepare.library, "find_published_stage", lambda stage_id: None)
    monkeypatch.setattr(prepare.library, "collection_stage", lambda stage_id: None)

    prepare.run(
        {"url": "https://www.youtube.com/playlist?list=PL1", "playlist_items": [2, 4], "stage_id": "url-x"},
        progress=lambda message: None,
        checkpoint=lambda payload: None,
    )

    assert seen["playlist_items"] == [2, 4]


def test_numbers_that_are_all_rubbish_download_the_single_video(isolated_library, fake_download):
    recorded = fake_download(["000-Only.mp3"])

    ingest.import_url(
        "https://www.youtube.com/watch?v=aaa&list=PL1",
        stage_id="url-rubbish",
        use_chapters=False,
        playlist_items=[0, -3],
    )

    assert "--no-playlist" in recorded[0]
    assert "--playlist-items" not in recorded[0]
