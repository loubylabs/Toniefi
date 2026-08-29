"""Peeking at a playlist, and downloading only the entries you picked."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import audio, config, db, ingest, library


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
        {"index": 1, "id": "aaa", "title": "First", "duration": 61.0, "available": True},
        {"index": 2, "id": "bbb", "title": "Second", "duration": 122.5, "available": True},
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
    assert command[command.index("--playlist-items") + 1] == "1,3-5"
