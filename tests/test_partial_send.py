import pytest

from app import push


def test_a_failure_partway_names_what_already_landed():
    error = push.PartialSend(
        underlying="Upload to storage rejected (503).",
        uploaded=17,
        total=30,
        tonie="Bedtime Bear",
    )
    message = str(error)
    assert "17" in message
    assert "30" in message
    assert "Bedtime Bear" in message
    assert "Upload to storage rejected (503)." in message
    assert "again" not in message.lower()


def test_a_failure_before_anything_landed_says_so():
    error = push.PartialSend(
        underlying="myTonies rejected those credentials.",
        uploaded=0,
        total=30,
        tonie="Bedtime Bear",
    )
    assert "Nothing was added" in str(error)


def test_the_error_keeps_the_counts_for_a_caller_to_read():
    error = push.PartialSend(underlying="boom", uploaded=3, total=9, tonie="Bear")
    assert (error.uploaded, error.total, error.tonie) == (3, 9, "Bear")


def test_a_worker_failure_midway_reports_what_landed(monkeypatch, tmp_path):
    from app import config, library

    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "toniefi.db")
    config.ensure_dirs()

    resolved = [
        ("a", {"name": "001.mp3", "title": "One", "seconds": 10, "size": 10}),
        ("a", {"name": "002.mp3", "title": "Two", "seconds": 10, "size": 10}),
        ("a", {"name": "003.mp3", "title": "Three", "seconds": 10, "size": 10}),
    ]
    for _, track in resolved:
        (config.LIBRARY_DIR / "a").mkdir(parents=True, exist_ok=True)
        (config.LIBRARY_DIR / "a" / track["name"]).write_bytes(b"0123456789")

    monkeypatch.setattr(library, "track_path", lambda slug, name: config.LIBRARY_DIR / slug / name)

    class Client:
        def __init__(self):
            self.added = 0

        def check_login(self):
            return {}

        def get_tonie(self, household_id, tonie_id):
            return {"name": "Bedtime Bear", "chapters": [], "secondsPresent": 0}

        def upload_file(self, path, on_bytes=None):
            if self.added >= 2:
                raise RuntimeError("Upload to storage rejected (503).")
            return f"file-{self.added}"

        def add_chapter(self, household_id, tonie_id, title, file_id):
            self.added += 1

        def close(self):
            return None

    monkeypatch.setattr(push, "client_from_settings", Client)
    payload = {
        "household_id": "h1",
        "tonie_id": "t1",
        "replace": False,
        "remote_chapters": [],
        "sources": [],
    }
    with pytest.raises(push.PartialSend) as caught:
        push._push_confirmed_tracks(payload, resolved, lambda *_, **__: None)
    assert caught.value.uploaded == 2
    assert caught.value.total == 3
    assert "Bedtime Bear" in str(caught.value)
    assert "again" not in str(caught.value).lower()
