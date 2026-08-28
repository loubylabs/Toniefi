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

def get_credentials() -> tuple[str, str]:
    """Read the saved username and password from one locked snapshot."""
    with _lock:
        rows = connect().execute(
            "SELECT key,value FROM settings WHERE key IN (?,?)",
            ("tonies_username", "tonies_password"),
        ).fetchall()
    values = {row["key"]: row["value"] for row in rows}
    return values.get("tonies_username", ""), values.get("tonies_password", "")


def replace_credentials(username: str, password: str) -> None:
    """Replace both saved credential fields in one SQLite transaction."""
    conn = connect()
    with _lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            for key, value in (
                ("tonies_username", username),
                ("tonies_password", password),
            ):
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def delete_credentials() -> None:
    """Delete both saved credential fields as one indivisible operation."""
    conn = connect()
    with _lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM settings WHERE key IN (?,?)",
                ("tonies_username", "tonies_password"),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


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


def create_forge_job_once(label: str, payload: dict[str, Any]) -> int:
    """Return the active Forge job for a slug or enqueue exactly one."""
    slug = payload.get("slug")
    if not slug:
        raise ValueError("A Forge job needs a collection slug.")
    conn = connect()
    with _lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id,payload FROM jobs WHERE kind='forge' "
                "AND status IN ('queued','running') ORDER BY id",
            ).fetchall()
            for row in rows:
                try:
                    existing = json.loads(row["payload"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if existing.get("slug") == slug:
                    conn.commit()
                    return int(row["id"])
            now = time.time()
            cursor = conn.execute(
                "INSERT INTO jobs(kind,status,label,payload,created_at,updated_at) "
                "VALUES('forge','queued',?,?,?,?)",
                (label, json.dumps(payload), now, now),
            )
            conn.commit()
            return int(cursor.lastrowid)
        except BaseException:
            conn.rollback()
            raise


class OperationConflict(RuntimeError):
    """An idempotency key was reused for a different confirmed operation."""


def _existing_operation(
    conn: sqlite3.Connection,
    operation_key: str,
    operation_digest: str,
) -> list[int] | None:
    rows = conn.execute("SELECT id,payload FROM jobs WHERE kind='push' ORDER BY id").fetchall()
    existing = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        if payload.get("operation_key") == operation_key:
            existing.append((int(row["id"]), payload))
    if not existing:
        return None
    if any(payload.get("operation_digest") != operation_digest for _, payload in existing):
        raise OperationConflict("This operation key belongs to a different push batch.")
    return [job_id for job_id, _ in existing]


def existing_idempotent_jobs(operation_key: str, operation_digest: str) -> list[int] | None:
    with _lock:
        return _existing_operation(connect(), operation_key, operation_digest)


def create_idempotent_jobs(
    operation_key: str,
    operation_digest: str,
    entries: list[tuple[str, str, dict[str, Any]]],
) -> tuple[list[int], bool]:
    """Atomically return an existing batch or create the entire batch."""
    if not entries:
        raise ValueError("A push batch needs at least one assignment.")
    now = time.time()
    conn = connect()
    with _lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = _existing_operation(conn, operation_key, operation_digest)
            if existing is not None:
                conn.commit()
                return existing, False

            created = []
            total = len(entries)
            for position, (kind, label, payload) in enumerate(entries, start=1):
                stored = {
                    **payload,
                    "operation_key": operation_key,
                    "operation_digest": operation_digest,
                    "batch_position": position,
                    "batch_size": total,
                }
                cursor = conn.execute(
                    "INSERT INTO jobs(kind,status,label,payload,created_at,updated_at) "
                    "VALUES(?,'queued',?,?,?,?)",
                    (kind, label, json.dumps(stored), now, now),
                )
                created.append(int(cursor.lastrowid))
            conn.commit()
            return created, True
        except Exception:
            conn.rollback()
            raise


def clone_failed_job(job_id: int) -> int:
    """Clone a failed job into a new queued job, preserving the original."""
    now = time.time()
    conn = connect()
    with _lock:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or row["status"] != "failed" or row["kind"] == "push":
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


def jobs_for_history(limit: int = 40) -> list[dict[str, Any]]:
    """Return recent job history in strict newest-first order."""
    rows = connect().execute(
        "SELECT * FROM jobs ORDER BY id DESC LIMIT ?",
        (max(0, int(limit)),),
    ).fetchall()
    return [_hydrate(row) for row in rows]


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


def referenced_collection_stage_ids() -> set[str]:
    """Return hidden collection stages that an unfinished job may resume."""
    rows = connect().execute(
        "SELECT payload FROM jobs WHERE status IN ('queued','running','failed') "
        "AND kind IN ('prepare_url','librivox','upload_prepare')"
    ).fetchall()
    identities = set()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (AttributeError, json.JSONDecodeError, TypeError):
            continue
        identity = payload.get("collection_stage_id") or payload.get("stage_id")
        if isinstance(identity, str) and identity:
            identities.add(identity)
    return identities


def fail_stale_running() -> list[dict[str, Any]]:
    """Fail jobs interrupted by restart and return exactly those rows."""
    conn = connect()
    with _lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status='running' ORDER BY id"
            ).fetchall()
            now = time.time()
            conn.execute(
                "UPDATE jobs SET status='failed', error='interrupted by restart', updated_at=? "
                "WHERE status='running'",
                (now,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    transitioned = []
    for row in rows:
        job = _hydrate(row)
        job.update(status="failed", error="interrupted by restart", updated_at=now)
        transitioned.append(job)
    return transitioned


def _hydrate(row: sqlite3.Row) -> dict[str, Any]:
    job = dict(row)
    for key in ("payload", "result"):
        try:
            job[key] = json.loads(job[key])
        except (json.JSONDecodeError, TypeError):
            job[key] = {}
    return job
