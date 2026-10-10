"""Every test gets its own database, so a hook never writes to a real one."""
from __future__ import annotations

import pytest

from app import config, db


def _drop_cached_connection() -> None:
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


@pytest.fixture(autouse=True)
def _isolated_database(monkeypatch, tmp_path):
    _drop_cached_connection()
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "toniefi.db")
    yield
    _drop_cached_connection()
