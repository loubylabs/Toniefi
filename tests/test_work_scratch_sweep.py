"""Scratch left by a killed job must not survive a restart.

While /work was a tmpfs, a container restart emptied it and an interrupted
download cost nothing. On host storage the same directory persists, and every
hard stop leaves a whole audiobook behind for good. Nothing sweeps it, because
nothing ever had to.

Startup is the one moment when the answer is unambiguous: no worker is running,
so every ingest temporary directory in WORK_DIR is an orphan.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app import config, db, jobs


@pytest.fixture
def work_dir(monkeypatch, tmp_path):
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "portal.db")
    config.ensure_dirs()
    yield config.WORK_DIR
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


def orphan(work: Path, *, size: int = 3) -> Path:
    """A directory shaped exactly like the one a killed ingest leaves behind."""
    stage = Path(tempfile.mkdtemp(dir=work))
    (stage / "chapters").mkdir()
    (stage / "chapters" / "001.mp3").write_bytes(b"x" * size)
    return stage


def test_sweep_removes_scratch_a_killed_job_left_behind(work_dir):
    stale = orphan(work_dir)
    assert stale.is_dir()

    jobs.sweep_work_scratch()

    assert not stale.exists()
    assert work_dir.is_dir(), "the scratch root itself must survive"


def test_sweep_removes_every_orphan_not_just_the_first(work_dir):
    orphans = [orphan(work_dir) for _ in range(3)]

    jobs.sweep_work_scratch()

    assert [o for o in orphans if o.exists()] == []


def test_sweep_leaves_files_the_operator_put_there(work_dir):
    keep_file = work_dir / "notes.txt"
    keep_file.write_text("mine", encoding="utf-8")
    keep_dir = work_dir / "my-downloads"
    keep_dir.mkdir()
    (keep_dir / "keep.mp3").write_bytes(b"keep")

    jobs.sweep_work_scratch()

    assert keep_file.read_text(encoding="utf-8") == "mine"
    assert (keep_dir / "keep.mp3").read_bytes() == b"keep"


def test_sweep_does_not_follow_a_symlink_out_of_the_scratch_directory(work_dir, tmp_path):
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "library.mp3").write_bytes(b"do not touch")
    (work_dir / "tmplink").symlink_to(outside, target_is_directory=True)

    jobs.sweep_work_scratch()

    assert (outside / "library.mp3").read_bytes() == b"do not touch"


def test_sweep_survives_a_scratch_directory_it_cannot_remove(work_dir, monkeypatch):
    stale = orphan(work_dir)
    survivor = orphan(work_dir)

    real_rmtree = jobs.shutil.rmtree

    def refuse_one(path, *args, **kwargs):
        if Path(path) == stale:
            raise PermissionError(13, "Permission denied", str(path))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(jobs.shutil, "rmtree", refuse_one)

    jobs.sweep_work_scratch()

    assert stale.is_dir(), "the unremovable one stays"
    assert not survivor.exists(), "and it does not stop the rest of the sweep"


def test_sweep_creates_the_scratch_root_when_it_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "absent")
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    jobs.sweep_work_scratch()

    assert config.WORK_DIR.is_dir()


def test_start_sweeps_work_scratch(work_dir, monkeypatch):
    stale = orphan(work_dir)
    monkeypatch.setattr(jobs, "_worker", lambda: None)
    monkeypatch.setattr(jobs.threading, "Thread", lambda *a, **k: _NoThread())

    jobs.start()

    assert not stale.exists()


class _NoThread:
    def start(self) -> None:
        pass

    def join(self, timeout=None) -> None:
        pass
