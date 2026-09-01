"""Tiny SQLite layer. The filesystem is the source of truth for audio;
this database only holds job state and settings."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any
from uuid import uuid4

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
    updated_at  REAL NOT NULL,
    progress_percent REAL
);
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_migrations (
    name        TEXT PRIMARY KEY,
    applied_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
"""

FORGE_OPERATION_IDS_MIGRATION = "2026-08-28-forge-operation-ids"
PUSH_BATCH_SOURCES_MIGRATION = "2026-08-29-push-batch-sources"
EMPTY_PLAYLIST_PICKS_MIGRATION = "2026-08-29-empty-playlist-picks"
JOB_PROGRESS_PERCENT_MIGRATION = "2026-08-29-job-progress-percent"


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
        try:
            conn.executescript(SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            _migrate_forge_operation_ids(conn)
            _migrate_push_batch_sources(conn)
            _migrate_empty_playlist_picks(conn)
            _migrate_job_progress_percent(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def _migrate_job_progress_percent(conn: sqlite3.Connection) -> None:
    """Give an existing database the nullable percentage column.

    CREATE TABLE IF NOT EXISTS never alters a table that already exists, so a
    database created before this column needs the ALTER. NULL is the right
    default and the right absent value: it means the worker has not reported a
    figure, which is exactly what the front end already renders as an
    indeterminate bar rather than as zero progress.
    """
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name=?",
        (JOB_PROGRESS_PERCENT_MIGRATION,),
    ).fetchone()
    if applied:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "progress_percent" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN progress_percent REAL")
    conn.execute(
        "INSERT INTO schema_migrations(name,applied_at) VALUES(?,?)",
        (JOB_PROGRESS_PERCENT_MIGRATION, time.time()),
    )


def _migrate_forge_operation_ids(conn: sqlite3.Connection) -> None:
    """Give every persisted Forge job its required durable operation identity."""
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name=?",
        (FORGE_OPERATION_IDS_MIGRATION,),
    ).fetchone()
    if applied:
        return
    rows = conn.execute("SELECT id,payload FROM jobs WHERE kind='forge'").fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        identity = payload.get("forge_operation_id")
        if isinstance(identity, str) and identity:
            continue
        payload["forge_operation_id"] = f"forge-{uuid4().hex}"
        conn.execute(
            "UPDATE jobs SET payload=? WHERE id=?",
            (json.dumps(payload), row["id"]),
        )
    conn.execute(
        "INSERT INTO schema_migrations(name,applied_at) VALUES(?,?)",
        (FORGE_OPERATION_IDS_MIGRATION, time.time()),
    )


def _migrate_push_batch_sources(conn: sqlite3.Connection) -> None:
    """Give every persisted push job the multi-collection `sources` shape.

    A send queued before one assignment could carry several collections stored
    its one collection at the top level. The worker now reads `sources`, so a
    job left in the old shape is claimed after an upgrade and dies on a
    KeyError. The rewrite is lossless: the three retired keys become the single
    source they always described, and every other key is carried through.
    """
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name=?",
        (PUSH_BATCH_SOURCES_MIGRATION,),
    ).fetchone()
    if applied:
        return
    rows = conn.execute("SELECT id,payload FROM jobs WHERE kind='push'").fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            continue
        if isinstance(payload.get("sources"), list):
            continue
        slug = payload.pop("slug", None)
        fingerprint = payload.pop("manifest_fingerprint", None)
        files = payload.pop("files", None)
        if not isinstance(slug, str) or not isinstance(fingerprint, str) or not isinstance(files, list):
            # Nothing recognisable to carry across. Leaving the payload alone
            # keeps the job failing with its own error rather than one invented
            # by a migration guessing at a shape it never wrote.
            continue
        payload["sources"] = [{
            "slug": slug,
            "manifest_fingerprint": fingerprint,
            "files": files,
        }]
        conn.execute(
            "UPDATE jobs SET payload=? WHERE id=?",
            (json.dumps(payload), row["id"]),
        )
    conn.execute(
        "INSERT INTO schema_migrations(name,applied_at) VALUES(?,?)",
        (PUSH_BATCH_SOURCES_MIGRATION, time.time()),
    )


def _migrate_empty_playlist_picks(conn: sqlite3.Connection) -> None:
    """Drop the empty pick every prepare job queued before picking existed.

    The old Desk sent `playlist_items: []` for every source, whether or not
    anybody had opened the picker, and the download read that as "no pick, let
    the link speak for itself". The same empty list now means "picked nothing",
    which the download refuses, so an older job claimed or retried after an
    upgrade would die on a pick its user never made.

    Removing the key restores exactly what that payload used to do. A list that
    names entries is a pick somebody made and is carried through untouched.
    """
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name=?",
        (EMPTY_PLAYLIST_PICKS_MIGRATION,),
    ).fetchone()
    if applied:
        return
    rows = conn.execute("SELECT id,payload FROM jobs WHERE kind='prepare_url'").fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        picked = payload.get("playlist_items")
        if not isinstance(picked, list) or picked:
            continue
        payload.pop("playlist_items")
        conn.execute(
            "UPDATE jobs SET payload=? WHERE id=?",
            (json.dumps(payload), row["id"]),
        )
    conn.execute(
        "INSERT INTO schema_migrations(name,applied_at) VALUES(?,?)",
        (EMPTY_PLAYLIST_PICKS_MIGRATION, time.time()),
    )


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


# -------------------------------------------------------- Forge defaults

FORGE_DEFAULTS_KEY = "forge_defaults"
INVALID_FORGE_DEFAULTS_DETAIL = (
    "Saved Forge defaults are invalid. Edit any Forge setting to replace them."
)


class InvalidForgeDefaults(ValueError):
    """The saved row exists, but it cannot describe a Forge profile."""


def forge_defaults() -> dict[str, Any] | None:
    """Return the saved Forge profile, distinguishing absence from damage."""
    with _lock:
        row = connect().execute(
            "SELECT value FROM settings WHERE key=?", (FORGE_DEFAULTS_KEY,)
        ).fetchone()
    if not row:
        return None
    try:
        stored = json.loads(row["value"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise InvalidForgeDefaults(INVALID_FORGE_DEFAULTS_DETAIL) from exc
    if not isinstance(stored, dict):
        raise InvalidForgeDefaults(INVALID_FORGE_DEFAULTS_DETAIL)
    return stored


def replace_forge_defaults(options: dict[str, Any]) -> None:
    """Replace the complete saved Forge profile atomically."""
    conn = connect()
    with _lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (FORGE_DEFAULTS_KEY, json.dumps(options)),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


# --------------------------------------------------------- work cart state

DESK_DISMISSALS_KEY = "desk_dismissals"
DESK_DISMISSAL_LIMIT = 200


def _stored_desk_dismissals(conn: sqlite3.Connection) -> dict[str, float]:
    """Read the dismissal set, discarding anything that is not a timestamp.

    This value is one JSON object in `settings`, so it can be edited by hand or
    written by an older build. A row the work cart cannot use is dropped rather
    than raised: a corrupt blob must not take the Desk down with it.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key=?", (DESK_DISMISSALS_KEY,)
    ).fetchone()
    if not row:
        return {}
    try:
        stored = json.loads(row["value"])
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(stored, dict):
        return {}
    return {
        key: float(value)
        for key, value in stored.items()
        if isinstance(key, str)
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    }


def desk_dismissals() -> dict[str, float]:
    """Return every hidden work cart row key with the time it was hidden."""
    with _lock:
        return _stored_desk_dismissals(connect())


def add_desk_dismissals(keys: list[str], now: float) -> dict[str, float]:
    """Hide the named rows and return the whole set, newest last.

    A key already in the set is removed and re-added, so insertion order is
    dismissal order. That is what makes the trim below keep the newest.
    """
    conn = connect()
    with _lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            merged = {
                key: value
                for key, value in _stored_desk_dismissals(conn).items()
                if key not in set(keys)
            }
            merged.update({key: float(now) for key in keys})
            if len(merged) > DESK_DISMISSAL_LIMIT:
                merged = dict(list(merged.items())[-DESK_DISMISSAL_LIMIT:])
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (DESK_DISMISSALS_KEY, json.dumps(merged)),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return merged


# -------------------------------------------------------------------- jobs

def create_job(kind: str, label: str, payload: dict[str, Any]) -> int:
    return create_jobs([(kind, label, payload)])[0]


def create_jobs(entries: list[tuple[str, str, dict[str, Any]]]) -> list[int]:
    """Create one logical batch atomically and return ids in input order."""
    if not entries:
        return []
    if any(kind == "forge" for kind, _, _ in entries):
        raise ValueError("Forge jobs must use create_forge_job_once().")
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


def _active_forge_job(conn: sqlite3.Connection, slug: str) -> int | None:
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
            return int(row["id"])
    return None


def create_forge_job_once(label: str, payload: dict[str, Any]) -> int:
    """Return the active Forge job for a slug or enqueue exactly one."""
    slug = payload.get("slug")
    if not slug:
        raise ValueError("A Forge job needs a collection slug.")
    conn = connect()
    with _lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            active = _active_forge_job(conn, slug)
            if active is not None:
                conn.commit()
                return active
            stored_payload = dict(payload)
            stored_payload["forge_operation_id"] = f"forge-{uuid4().hex}"
            now = time.time()
            cursor = conn.execute(
                "INSERT INTO jobs(kind,status,label,payload,created_at,updated_at) "
                "VALUES('forge','queued',?,?,?,?)",
                (label, json.dumps(stored_payload), now, now),
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
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["status"] != "failed" or row["kind"] == "push":
                conn.commit()
                return 0
            if row["kind"] == "forge":
                try:
                    payload = json.loads(row["payload"])
                except (json.JSONDecodeError, TypeError):
                    payload = {}
                slug = payload.get("slug")
                if isinstance(slug, str) and slug:
                    active = _active_forge_job(conn, slug)
                    if active is not None:
                        conn.commit()
                        return active
            cur = conn.execute(
                "INSERT INTO jobs(kind,status,label,payload,created_at,updated_at) "
                "VALUES(?,'queued',?,?,?,?)",
                (row["kind"], row["label"], row["payload"], now, now),
            )
            conn.commit()
            return int(cur.lastrowid)
        except BaseException:
            conn.rollback()
            raise


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
