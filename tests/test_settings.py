from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, db, main


@pytest.fixture
def isolated_settings(monkeypatch, tmp_path):
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "toniefi.db")
    monkeypatch.setattr(config, "TONIES_USERNAME", "")
    monkeypatch.setattr(config, "TONIES_PASSWORD", "")
    db.init()
    yield
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


@pytest.fixture
def client(isolated_settings) -> TestClient:
    return TestClient(main.app)


def test_delete_credentials_removes_both_saved_values_and_reports_none(client):
    db.set_setting("tonies_username", "saved@example.com")
    db.set_setting("tonies_password", "secret")

    response = client.delete("/api/settings/credentials")

    assert response.status_code == 200
    assert response.json() == {
        "configured": False,
        "source": "none",
        "username": "",
    }
    assert db.get_setting("tonies_username") == ""
    assert db.get_setting("tonies_password") == ""
    assert "secret" not in response.text
    assert client.get("/api/status").json()["credentials"] == response.json()


def test_delete_credentials_is_idempotent(client):
    first = client.delete("/api/settings/credentials")
    second = client.delete("/api/settings/credentials")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()


def test_delete_credentials_leaves_environment_credentials_active(client, monkeypatch):
    db.set_setting("tonies_username", "saved@example.com")
    db.set_setting("tonies_password", "saved-secret")
    monkeypatch.setattr(config, "TONIES_USERNAME", "environment@example.com")
    monkeypatch.setattr(config, "TONIES_PASSWORD", "environment-secret")

    response = client.delete("/api/settings/credentials")

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "source": "environment",
        "username": "environment@example.com",
    }
    assert db.get_setting("tonies_username") == ""
    assert db.get_setting("tonies_password") == ""
    assert "environment-secret" not in response.text
