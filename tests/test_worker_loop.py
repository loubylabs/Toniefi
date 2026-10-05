from __future__ import annotations

import sqlite3

import pytest

from app import config, db, jobs


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "portal.db")
    db.init()
    yield tmp_path
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


@pytest.fixture
def stop_after(monkeypatch):
    """Let the worker loop run until the given number of idle waits."""
    def arm(idle_waits: int):
        remaining = [idle_waits]

        def wait(_timeout):
            remaining[0] -= 1
            if remaining[0] <= 0:
                jobs._stop.set()
            return jobs._stop.is_set()

        monkeypatch.setattr(jobs._stop, "wait", wait)
    yield arm
    jobs._stop.clear()


def test_worker_logs_how_long_a_job_waited(isolated_db, monkeypatch, capsys, stop_after):
    job_id = db.create_job("push", "Send", {})
    monkeypatch.setattr(jobs, "_handle", lambda job: {})
    stop_after(1)

    jobs._worker()

    assert f"Job {job_id} (push) started after " in capsys.readouterr().out
    assert db.get_job(job_id)["status"] == "done"


def test_worker_survives_a_failed_claim(isolated_db, monkeypatch, stop_after):
    job_id = db.create_job("push", "Send", {})
    real_claim = db.claim_job
    calls = []

    def flaky_claim():
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_claim()

    monkeypatch.setattr(db, "claim_job", flaky_claim)
    monkeypatch.setattr(jobs, "_handle", lambda job: {})
    stop_after(2)

    jobs._worker()

    assert db.get_job(job_id)["status"] == "done"
