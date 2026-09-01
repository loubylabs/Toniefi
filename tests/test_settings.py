from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import config, db, main, push, tonies, version


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


def test_status_reports_the_application_version_and_build(client, monkeypatch):
    monkeypatch.setattr(version, "APP_VERSION", "2.3.4")
    monkeypatch.setattr(version, "BUILD", "abc1234")

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["version"] == "2.3.4"
    assert response.json()["build"] == "abc1234"


def test_forge_defaults_persist_as_one_complete_validated_profile(client):
    current = client.get("/api/settings/forge-defaults")

    assert current.status_code == 200
    assert current.json() == {
        "use_chapters": True,
        "normalize": True,
        "clean_titles": True,
        "trim_head": 0,
        "trim_tail": 0,
        "split_oversized": True,
    }

    selected = {
        "use_chapters": False,
        "normalize": True,
        "clean_titles": False,
        "trim_head": 1.5,
        "trim_tail": 2.5,
        "split_oversized": True,
    }
    saved = client.put("/api/settings/forge-defaults", json=selected)

    assert saved.status_code == 200
    assert saved.json() == selected
    assert client.get("/api/settings/forge-defaults").json() == selected


def test_forge_defaults_refuse_unknown_settings(client):
    response = client.put(
        "/api/settings/forge-defaults",
        json={"use_chapters": False, "force_chapters": False},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("use_chapters", "false"),
        ("trim_head", -0.1),
        ("trim_tail", "1.5"),
    ],
)
def test_forge_defaults_refuse_coerced_or_negative_values(client, field, value):
    selected = {
        "use_chapters": False,
        "normalize": True,
        "clean_titles": True,
        "trim_head": 0,
        "trim_tail": 0,
        "split_oversized": True,
    }
    selected[field] = value

    response = client.put("/api/settings/forge-defaults", json=selected)

    assert response.status_code == 422


def test_forge_defaults_refuse_non_finite_numbers(client):
    response = client.put(
        "/api/settings/forge-defaults",
        content=(
            '{"use_chapters":false,"normalize":true,"clean_titles":true,'
            '"trim_head":0,"trim_tail":NaN,"split_oversized":true}'
        ),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422


def test_forge_defaults_update_requires_the_complete_profile(client):
    response = client.put(
        "/api/settings/forge-defaults",
        json={"use_chapters": False},
    )

    assert response.status_code == 422


def test_prepare_requests_use_the_same_strict_forge_validation(client):
    response = client.post(
        "/api/prepare",
        json={
            "sources": [{"url": "https://example.test/story"}],
            "options": {
                "use_chapters": False,
                "normalize": "false",
                "clean_titles": True,
                "trim_head": 0,
                "trim_tail": 0,
                "split_oversized": True,
            },
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "stored",
    [
        "not-json",
        "[]",
        json.dumps({"use_chapters": False}),
        json.dumps({"use_chapters": False, "force_chapters": False}),
        json.dumps({"trim_head": -1}),
    ],
)
def test_invalid_stored_forge_defaults_are_reported_instead_of_reset(client, stored):
    connection = db.connect()
    connection.execute(
        "INSERT INTO settings(key,value) VALUES(?,?)",
        (db.FORGE_DEFAULTS_KEY, stored),
    )
    connection.commit()

    response = client.get("/api/settings/forge-defaults")

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Saved Forge defaults are invalid. Edit any Forge setting to replace them."
    )


def test_complete_stored_forge_defaults_refuse_an_unknown_field(client):
    stored = {
        "use_chapters": False,
        "normalize": True,
        "clean_titles": True,
        "trim_head": 0,
        "trim_tail": 0,
        "split_oversized": True,
        "force_chapters": False,
    }
    connection = db.connect()
    connection.execute(
        "INSERT INTO settings(key,value) VALUES(?,?)",
        (db.FORGE_DEFAULTS_KEY, json.dumps(stored)),
    )
    connection.commit()

    response = client.get("/api/settings/forge-defaults")

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Saved Forge defaults are invalid. Edit any Forge setting to replace them."
    )


def test_complete_stored_forge_defaults_refuse_a_non_boolean_boolean_field(client):
    stored = {
        "use_chapters": 0,
        "normalize": True,
        "clean_titles": True,
        "trim_head": 0,
        "trim_tail": 0,
        "split_oversized": True,
    }
    connection = db.connect()
    connection.execute(
        "INSERT INTO settings(key,value) VALUES(?,?)",
        (db.FORGE_DEFAULTS_KEY, json.dumps(stored)),
    )
    connection.commit()

    response = client.get("/api/settings/forge-defaults")

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Saved Forge defaults are invalid. Edit any Forge setting to replace them."
    )


def test_complete_stored_forge_defaults_refuse_a_non_finite_numeric_field(client):
    stored = {
        "use_chapters": False,
        "normalize": True,
        "clean_titles": True,
        "trim_head": float("nan"),
        "trim_tail": 0,
        "split_oversized": True,
    }
    connection = db.connect()
    connection.execute(
        "INSERT INTO settings(key,value) VALUES(?,?)",
        (db.FORGE_DEFAULTS_KEY, json.dumps(stored)),
    )
    connection.commit()

    response = client.get("/api/settings/forge-defaults")

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Saved Forge defaults are invalid. Edit any Forge setting to replace them."
    )


def test_a_complete_forge_defaults_write_repairs_invalid_stored_data(client):
    connection = db.connect()
    connection.execute(
        "INSERT INTO settings(key,value) VALUES(?,?)",
        (db.FORGE_DEFAULTS_KEY, "not-json"),
    )
    connection.commit()
    selected = {
        "use_chapters": False,
        "normalize": True,
        "clean_titles": True,
        "trim_head": 0,
        "trim_tail": 0,
        "split_oversized": True,
    }

    saved = client.put("/api/settings/forge-defaults", json=selected)

    assert saved.status_code == 200
    assert client.get("/api/settings/forge-defaults").json() == selected


def test_delete_credentials_removes_both_saved_values_and_reports_none(client):
    db.replace_credentials("saved@example.com", "secret")

    response = client.delete("/api/settings/credentials")

    assert response.status_code == 200
    assert response.json() == {
        "configured": False,
        "source": "none",
        "username": "",
    }
    assert db.get_credentials() == ("", "")
    assert "secret" not in response.text
    assert client.get("/api/status").json()["credentials"] == response.json()


def test_delete_credentials_is_idempotent(client):
    first = client.delete("/api/settings/credentials")
    second = client.delete("/api/settings/credentials")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()


def test_delete_credentials_leaves_environment_credentials_active(client, monkeypatch):
    db.replace_credentials("saved@example.com", "saved-secret")
    monkeypatch.setattr(config, "TONIES_USERNAME", "environment@example.com")
    monkeypatch.setattr(config, "TONIES_PASSWORD", "environment-secret")

    response = client.delete("/api/settings/credentials")

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "source": "environment",
        "username": "environment@example.com",
    }
    assert db.get_credentials() == ("", "")
    assert "environment-secret" not in response.text


@pytest.mark.parametrize(
    ("environment", "saved", "expected"),
    [
        (("", ""), ("", ""), ("none", False, "", "")),
        (("", ""), ("saved@example.com", ""), ("saved", False, "saved@example.com", "")),
        (("", ""), ("", "saved-password"), ("saved", False, "", "saved-password")),
        (("", ""), ("saved@example.com", "saved-password"), ("saved", True, "saved@example.com", "saved-password")),
        (("environment@example.com", ""), ("", ""), ("environment", False, "environment@example.com", "")),
        (("environment@example.com", ""), ("saved@example.com", ""), ("environment", False, "environment@example.com", "")),
        (("environment@example.com", ""), ("", "saved-password"), ("environment", False, "environment@example.com", "")),
        (("environment@example.com", ""), ("saved@example.com", "saved-password"), ("environment", False, "environment@example.com", "")),
        (("", "environment-password"), ("", ""), ("environment", False, "", "environment-password")),
        (("", "environment-password"), ("saved@example.com", ""), ("environment", False, "", "environment-password")),
        (("", "environment-password"), ("", "saved-password"), ("environment", False, "", "environment-password")),
        (("", "environment-password"), ("saved@example.com", "saved-password"), ("environment", False, "", "environment-password")),
        (("environment@example.com", "environment-password"), ("", ""), ("environment", True, "environment@example.com", "environment-password")),
        (("environment@example.com", "environment-password"), ("saved@example.com", ""), ("environment", True, "environment@example.com", "environment-password")),
        (("environment@example.com", "environment-password"), ("", "saved-password"), ("environment", True, "environment@example.com", "environment-password")),
        (("environment@example.com", "environment-password"), ("saved@example.com", "saved-password"), ("environment", True, "environment@example.com", "environment-password")),
    ],
)
def test_credential_pair_selection_never_mixes_sources(
    client,
    monkeypatch,
    environment,
    saved,
    expected,
):
    environment_username, environment_password = environment
    saved_username, saved_password = saved
    monkeypatch.setattr(config, "TONIES_USERNAME", environment_username)
    monkeypatch.setattr(config, "TONIES_PASSWORD", environment_password)
    db.replace_credentials(saved_username, saved_password)

    selected = push.select_credentials()

    source, configured, username, password = expected
    assert selected == {
        "source": source,
        "configured": configured,
        "username": username,
        "password": password,
    }


def test_partial_environment_credentials_block_saved_fallback(client, monkeypatch):
    db.replace_credentials("saved@example.com", "saved-password")
    monkeypatch.setattr(config, "TONIES_USERNAME", "environment@example.com")
    monkeypatch.setattr(config, "TONIES_PASSWORD", "")

    with pytest.raises(tonies.AuthError, match="Environment credentials are incomplete"):
        push.client_from_settings()

    response = client.get("/api/status")
    assert response.json()["credentials"] == {
        "configured": False,
        "source": "environment",
        "username": "environment@example.com",
    }
    assert "saved-password" not in response.text


def test_status_and_removal_never_return_passwords(client, monkeypatch):
    db.replace_credentials("saved@example.com", "saved-password")
    monkeypatch.setattr(config, "TONIES_USERNAME", "")
    monkeypatch.setattr(config, "TONIES_PASSWORD", "environment-password")

    status_response = client.get("/api/status")
    removal_response = client.delete("/api/settings/credentials")

    for response in (status_response, removal_response):
        assert "password" not in response.text.lower()
        assert "saved-password" not in response.text
        assert "environment-password" not in response.text


def test_connection_test_closes_client_after_failed_login(client, monkeypatch):
    class FailingClient:
        def __init__(self):
            self.closed = False

        def check_login(self):
            raise tonies.AuthError("myTonies rejected those credentials.")

        def close(self):
            self.closed = True

    cloud = FailingClient()
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)

    response = client.post("/api/settings/test")

    assert response.status_code == 400
    assert cloud.closed is True


def test_connection_test_closes_client_after_successful_login(client, monkeypatch):
    class SuccessfulClient:
        def __init__(self):
            self.closed = False

        def check_login(self):
            return {"email": "family@example.com"}

        def close(self):
            self.closed = True

    cloud = SuccessfulClient()
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)

    response = client.post("/api/settings/test")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "email": "family@example.com"}
    assert cloud.closed is True
