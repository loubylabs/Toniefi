"""Tiny SQLite layer. The filesystem is the source of truth for audio;
this database only holds job state and settings."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from . import config

_lock = threading.Lock()
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    label       TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL DEFAULT '{}',
    progress    TEXT NOT NULL DEFAULT '',
    result      TEXT NOT NULL DEFAULT '{}',
    error       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
"""


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.ensure_dirs()
        conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn


def init() -> None:
    conn = connect()
    with _lock:
        conn.executescript(SCHEMA)
        conn.commit()


# ---------------------------------------------------------------- settings

def get_setting(key: str, default: str = "") -> str:
    row = connect().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    conn = connect()
    with _lock:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


# -------------------------------------------------------------------- jobs

def create_job(kind: str, label: str, payload: dict[str, Any]) -> int:
    return create_jobs([(kind, label, payload)])[0]


def create_jobs(entries: list[tuple[str, str, dict[str, Any]]]) -> list[int]:
    """Create one logical batch atomically and return ids in input order."""
    if not entries:
        return []
    now = time.time()
    conn = connect()
    with _lock:
        created = []
        try:
            for kind, label, payload in entries:
                cursor = conn.execute(
                    "INSERT INTO jobs(kind,status,label,payload,created_at,updated_at) "
                    "VALUES(?,'queued',?,?,?,?)",
                    (kind, label, json.dumps(payload), now, now),
                )
                created.append(int(cursor.lastrowid))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return created


def retry_failed_job(job_id: int) -> int:
    """Clone a failed job into a new queued job, preserving the original."""
    now = time.time()
    conn = connect()
    with _lock:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or row["status"] != "failed":
            return 0
        cur = conn.execute(
            "INSERT INTO jobs(kind,status,label,payload,created_at,updated_at) "
            "VALUES(?,'queued',?,?,?,?)",
            (row["kind"], row["label"], row["payload"], now, now),
        )
        conn.commit()
        return int(cur.lastrowid)


def claim_job() -> dict[str, Any] | None:
    """Atomically move the oldest queued job to running."""
    conn = connect()
    with _lock:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        cur = conn.execute(
            "UPDATE jobs SET status='running', updated_at=? WHERE id=? AND status='queued'",
            (time.time(), row["id"]),
        )
        if cur.rowcount == 0:
            # Another worker claimed it between the SELECT and the UPDATE.
            conn.commit()
            return None
        conn.commit()
    job = dict(row)
    job["payload"] = json.loads(job["payload"])
    job["status"] = "running"
    return job


def update_job(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    for key in ("payload", "result"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key])
    fields["updated_at"] = time.time()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn = connect()
    with _lock:
        conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), job_id))
        conn.commit()


def get_job(job_id: int) -> dict[str, Any] | None:
    row = connect().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _hydrate(row) if row else None


def jobs_for_refresh(limit: int = 40) -> list[dict[str, Any]]:
    """Return every active job, then failed and completed history up to limit."""
    conn = connect()
    active = conn.execute(
        "SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY id DESC"
    ).fetchall()
    remaining = max(0, int(limit) - len(active))
    history = []
    if remaining:
        history = conn.execute(
            "SELECT * FROM jobs WHERE status NOT IN ('queued','running') "
            "ORDER BY CASE WHEN status='failed' THEN 0 ELSE 1 END, id DESC LIMIT ?",
            (remaining,),
        ).fetchall()
    return [_hydrate(row) for row in [*active, *history]]


def active_upload_stages() -> set[str]:
    rows = connect().execute(
        "SELECT payload FROM jobs WHERE kind='upload_prepare' "
        "AND status IN ('queued','running')"
    ).fetchall()
    stages = set()
    for row in rows:
        try:
            stage = json.loads(row["payload"]).get("stage")
        except (AttributeError, json.JSONDecodeError, TypeError):
            continue
        if isinstance(stage, str):
            stages.add(stage)
    return stages


def requeue_stale_running() -> None:
    """Anything left 'running' when the process died is not coming back."""
    conn = connect()
    with _lock:
        conn.execute(
            "UPDATE jobs SET status='failed', error='interrupted by restart', updated_at=? "
            "WHERE status='running'",
            (time.time(),),
        )
        conn.commit()


def _hydrate(row: sqlite3.Row) -> dict[str, Any]:
    job = dict(row)
    for key in ("payload", "result"):
        try:
            job[key] = json.loads(job[key])
        except (json.JSONDecodeError, TypeError):
            job[key] = {}
    return job
