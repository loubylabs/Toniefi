from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, db, main


@pytest.fixture
def isolated_activity(monkeypatch, tmp_path):
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "toniefi.db")
    db.init()
    yield
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


def test_activity_history_is_chronological_and_old_failures_do_not_displace_newest(
    isolated_activity,
):
    ids = db.create_jobs([
        ("prepare_url", f"Story {number}", {"url": f"https://example.com/{number}"})
        for number in range(1, 51)
    ])
    for job_id in ids[:45]:
        db.update_job(job_id, status="failed", error="Old failure")
    for job_id in ids[45:]:
        db.update_job(job_id, status="done", progress="Finished")

    history_ids = [job["id"] for job in db.jobs_for_history(40)]

    assert history_ids == list(range(50, 10, -1))
    assert 50 not in [job["id"] for job in db.jobs_for_refresh(40)]


def test_activity_history_route_uses_the_chronological_resource(isolated_activity):
    ids = db.create_jobs([
        ("prepare_url", f"Story {number}", {"url": f"https://example.com/{number}"})
        for number in range(1, 4)
    ])
    for job_id in ids:
        db.update_job(job_id, status="done", progress="Finished")

    response = TestClient(main.app).get("/api/jobs/history")

    assert response.status_code == 200
    assert [job["id"] for job in response.json()] == [3, 2, 1]
