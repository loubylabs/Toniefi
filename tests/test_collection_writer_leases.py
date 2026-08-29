from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import audio, config, db, ingest, library, push


class ObservableRLock:
    """A deterministic reentrant lock that exposes cross-thread contention."""

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
def isolated_writer(monkeypatch, tmp_path):
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
    slug = library.create("Confirmed Story")
    path = config.LIBRARY_DIR / slug
    for name in ("one.mp3", "two.mp3"):
        (path / name).write_bytes(name.encode())
    manifest_path = path / library.MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["stage"] = "forged"
    manifest["tracks"] = []
    for name, title in (("one.mp3", "One"), ("two.mp3", "Two")):
        stat = (path / name).stat()
        manifest["tracks"].append({
            "name": name,
            "title": title,
            "seconds": 1000,
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
        })
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    yield slug
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


def confirmed_payload(slug: str) -> dict:
    manifest = library.get(slug)
    return {
        "household_id": "house-1",
        "tonie_id": "tonie-1",
        "replace": True,
        "remote_chapters": [{"id": "remote-a", "title": "Existing"}],
        "sources": [{
            "slug": slug,
            "manifest_fingerprint": manifest["manifest_fingerprint"],
            "files": ["one.mp3", "two.mp3"],
        }],
    }


class PushCloud:
    def __init__(self, events, race_checkpoint, writer_write):
        self.events = events
        self.race_checkpoint = race_checkpoint
        self.writer_write = writer_write
        self.chapters = [{"id": "remote-a", "title": "Existing"}]

    def check_login(self):
        return None

    def get_tonie(self, household_id, tonie_id):
        self.events.append("remote-read")
        return {"name": "Fox", "chapters": list(self.chapters), "secondsPresent": 0}

    def clear_tonie(self, household_id, tonie_id):
        self.events.append("remote-clear")
        self.chapters = []

    def upload_file(self, path):
        self.events.append(f"upload-start:{path.name}")
        self.race_checkpoint.set()
        assert self.writer_write.wait(5)
        path.read_bytes()
        self.events.append(f"upload-read:{path.name}")
        return f"file-{path.name}"

    def add_chapter(self, household_id, tonie_id, title, file_id):
        self.events.append(f"remote-add:{title}")
        self.chapters.append({"id": file_id, "title": title})

    def close(self):
        return None


def assert_writer_does_not_overlap_confirmed_push(
    monkeypatch,
    slug,
    writer,
    writer_ready,
    allow_writer,
    writer_write,
    events,
):
    race_checkpoint = threading.Event()
    monkeypatch.setattr(library, "_manifest_lock", ObservableRLock(race_checkpoint))
    cloud = PushCloud(events, race_checkpoint, writer_write)
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)
    payload = confirmed_payload(slug)
    errors = []

    def run(target):
        try:
            target()
        except BaseException as exc:
            errors.append(exc)

    writer_thread = threading.Thread(name="collection-writer", target=run, args=(writer,))
    push_thread = threading.Thread(name="confirmed-push", target=run, args=(lambda: push.push_confirmed(payload),))
    writer_thread.start()
    assert writer_ready.wait(5)
    push_thread.start()
    assert race_checkpoint.wait(5)
    allow_writer.set()
    writer_thread.join(5)
    push_thread.join(5)

    assert not writer_thread.is_alive() and not push_thread.is_alive()
    assert all(isinstance(error, push.StalePush) for error in errors)
    if "remote-read" in events:
        first_read = events.index("remote-read")
        final_read = len(events) - 1 - events[::-1].index("remote-read")
        write_at = events.index("writer-write")
        assert not first_read < write_at < final_read


def test_librivox_download_does_not_overlap_confirmed_push(isolated_writer, monkeypatch):
    writer_ready = threading.Event()
    allow_writer = threading.Event()
    writer_write = threading.Event()
    events = []
    book = {"title": "Public Story", "sections": [], "authors": [], "url_librivox": "https://example.test/book"}
    sections = [{"title": "Opening", "listen_url": "https://example.test/opening.mp3"}]

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size):
            events.append("writer-write")
            writer_write.set()
            yield b"audio"

    class Client:
        def __init__(self, **kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def stream(self, method, url):
            return Response()

    def progress(message):
        if message.startswith("Downloading"):
            writer_ready.set()
            allow_writer.wait()

    monkeypatch.setattr(ingest, "librivox_sections", lambda book_id: (book, sections))
    monkeypatch.setattr(ingest.httpx, "Client", Client)
    monkeypatch.setattr(ingest.audio, "duration_seconds", lambda path: 10)
    writer = lambda: ingest.import_librivox(
        "7",
        stage_id="librivox-writer-test",
        progress=progress,
    )
    assert_writer_does_not_overlap_confirmed_push(
        monkeypatch, isolated_writer, writer, writer_ready, allow_writer, writer_write, events,
    )


def test_url_audio_move_does_not_overlap_confirmed_push(isolated_writer, monkeypatch):
    writer_ready = threading.Event()
    allow_writer = threading.Event()
    writer_write = threading.Event()
    events = []
    real_move = shutil.move

    def run_download(command, **kwargs):
        template = Path(command[command.index("-o") + 1])
        (template.parent / "000-Story.mp3").write_bytes(b"url-audio")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def progress(message):
        if message.startswith("Storing"):
            writer_ready.set()
            allow_writer.wait()

    def move(source, target):
        events.append("writer-write")
        writer_write.set()
        return real_move(source, target)

    monkeypatch.setattr(ingest.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    monkeypatch.setattr(ingest.subprocess, "run", run_download)
    monkeypatch.setattr(ingest.shutil, "move", move)
    monkeypatch.setattr(ingest.audio, "duration_seconds", lambda path: 10)
    writer = lambda: ingest.import_url(
        "https://example.test/story",
        stage_id="url-writer-test",
        use_chapters=False,
        progress=progress,
    )
    assert_writer_does_not_overlap_confirmed_push(
        monkeypatch, isolated_writer, writer, writer_ready, allow_writer, writer_write, events,
    )


def test_staged_upload_placement_does_not_overlap_confirmed_push(isolated_writer, monkeypatch, tmp_path):
    writer_ready = threading.Event()
    allow_writer = threading.Event()
    writer_write = threading.Event()
    events = []
    collection_stage = library.begin_collection_stage(
        "upload-writer-test",
        title="Upload Target",
        source="upload",
    )
    source = tmp_path / "staged.mp3"
    source.write_bytes(b"staged-audio")
    real_copy = shutil.copyfileobj

    def copyfileobj(source_file, target_file, length):
        writer_ready.set()
        allow_writer.wait()
        result = real_copy(source_file, target_file, length)
        events.append("writer-write")
        writer_write.set()
        return result

    monkeypatch.setattr(ingest.shutil, "copyfileobj", copyfileobj)
    monkeypatch.setattr(ingest.audio, "duration_seconds", lambda path: 10)
    writer = lambda: ingest.import_upload(
        source,
        filename="Story.mp3",
        stage_id=collection_stage.identity,
        target_name="001-story.mp3",
    )
    assert_writer_does_not_overlap_confirmed_push(
        monkeypatch, isolated_writer, writer, writer_ready, allow_writer, writer_write, events,
    )
