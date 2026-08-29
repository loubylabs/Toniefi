"""A small background worker. Extraction, forging and uploads all take
minutes, and an HTTP request should not be holding the bag while they run."""
from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
from pathlib import Path
from uuid import uuid4

from . import audio, config, db, forge, ingest, library, prepare, push

_threads: list[threading.Thread] = []
_stop = threading.Event()
_upload_stage_lock = threading.RLock()
_upload_stage_leases: set[Path] = set()

UPLOAD_MAX_FILES = 500
UPLOAD_MAX_BYTES = 20 * 1024 * 1024 * 1024
UPLOAD_STAGE_RETENTION_SECONDS = 24 * 60 * 60
UPLOAD_STAGE_MARKER = ".toniefi-upload-stage.json"
_now = time.time


def _owned_upload_stage(stage: Path) -> bool:
    return (
        stage.parent == config.upload_stage_dir().resolve()
        and stage.name.startswith("upload-")
        and not stage.is_symlink()
        and (stage / UPLOAD_STAGE_MARKER).is_file()
    )


def mark_upload_stage(stage: Path) -> None:
    with _upload_stage_lock:
        stage = stage.resolve()
        if not _owned_upload_stage(stage):
            return
        (stage / UPLOAD_STAGE_MARKER).write_text(
            json.dumps({"retained_at": _now()}),
            encoding="utf-8",
        )


def remove_upload_stage(stage: Path) -> None:
    with _upload_stage_lock:
        stage = stage.resolve()
        _upload_stage_leases.discard(stage)
        if _owned_upload_stage(stage):
            shutil.rmtree(stage)


def _sweep_upload_stage(stage: Path) -> None:
    with _upload_stage_lock:
        if stage.is_symlink():
            return
        stage = stage.resolve()
        if stage in _upload_stage_leases or not _owned_upload_stage(stage):
            return
        if stage.name in db.active_upload_stages():
            return
        marker = stage / UPLOAD_STAGE_MARKER
        try:
            retained_at = float(json.loads(marker.read_text(encoding="utf-8"))["retained_at"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            return
        cutoff = _now() - UPLOAD_STAGE_RETENTION_SECONDS
        if retained_at <= cutoff:
            shutil.rmtree(stage)


def sweep_upload_staging() -> None:
    """Remove expired TonieFi upload stages and leave all other work intact."""
    config.ensure_dirs()
    for stage in config.upload_stage_dir().iterdir():
        if stage.name.startswith("upload-"):
            _sweep_upload_stage(stage)


def create_upload_stage() -> tuple[str, Path]:
    config.ensure_dirs()
    stage_name = f"upload-{uuid4().hex}"
    stage = config.upload_stage_dir() / stage_name
    with _upload_stage_lock:
        _upload_stage_leases.add(stage.resolve())
        try:
            stage.mkdir()
            (stage / UPLOAD_STAGE_MARKER).write_text(
                json.dumps({"retained_at": _now()}),
                encoding="utf-8",
            )
        except Exception:
            _upload_stage_leases.discard(stage.resolve())
            shutil.rmtree(stage, ignore_errors=True)
            raise
    return stage_name, stage


def upload_limit_label(limit: int) -> str:
    gibibyte = 1024 * 1024 * 1024
    if limit >= gibibyte and limit % gibibyte == 0:
        return f"{limit // gibibyte} GiB"
    return f"{limit} {'byte' if limit == 1 else 'bytes'}"


def upload_target_name(item: dict, position: int) -> str:
    source = Path(item["name"])
    return f"{position + 1:03d}-{audio.slugify(source.stem)}{source.suffix.lower()}"


def enqueue(kind: str, label: str, payload: dict) -> int:
    return db.create_job(kind, label, payload)


def enqueue_upload_stage(stage: Path, label: str, payload: dict) -> int:
    with _upload_stage_lock:
        stage = stage.resolve()
        if stage not in _upload_stage_leases or not _owned_upload_stage(stage):
            raise ValueError("Upload stage is not an owned active stage.")
        job_id = db.create_job(
            "upload_prepare",
            label,
            {**payload, "stage": stage.name},
        )
        _upload_stage_leases.discard(stage)
        return job_id


def enqueue_many(entries: list[tuple[str, str, dict]]) -> list[int]:
    return db.create_jobs(entries)


class PushRetryConflict(RuntimeError):
    """Confirmed sends must be rebuilt in the Library instead of cloning a job."""


INVALID_FORGE_SLUG_ERROR = "This Forge job has an invalid collection slug."


def _forge_completion(payload: object) -> dict | None:
    if not isinstance(payload, dict):
        raise library.InvalidPublicCollectionSlug()
    return library.completed_forge(payload.get("slug"))


def retry_failed_job(job_id: int) -> int:
    with _upload_stage_lock:
        job = db.get_job(job_id)
        if job and job.get("status") == "failed" and job.get("kind") == "push":
            raise PushRetryConflict(
                "Creative Tonie sends must be selected and confirmed again in the Library."
            )
        if job and job.get("status") == "failed" and job.get("kind") == "forge":
            completed = _forge_completion(job.get("payload"))
            if completed:
                db.update_job(
                    job_id,
                    status="done",
                    progress="Finished",
                    result=completed,
                    error="",
                )
                return job_id
        return db.clone_failed_job(job_id)


def present(job: dict) -> dict:
    """Add display-only phase data without changing stored job history."""
    displayed = dict(job)
    stored_progress = displayed.get("progress", "")
    prefix, separator, message = stored_progress.partition(": ")
    if separator and prefix in {"extracting", "forging"}:
        displayed["progress"] = message

    status = displayed.get("status", "")
    kind = displayed.get("kind", "")
    terminal_forge = False
    invalid_forge_slug = False
    if kind == "forge":
        try:
            terminal_forge = _forge_completion(displayed.get("payload")) is not None
        except library.InvalidPublicCollectionSlug:
            invalid_forge_slug = True
    collection_stage = ""
    if status == "done" and kind == "librivox":
        payload = displayed.get("payload") or {}
        result = displayed.get("result") or {}
        slug = result.get("slug") or payload.get("slug")
        collection = library.get(slug) if slug else None
        collection_stage = (collection or {}).get("stage", "")
        displayed["collection_stage"] = collection_stage
    displayed["retryable"] = (
        status == "failed"
        and kind != "push"
        and not terminal_forge
        and not invalid_forge_slug
    )
    if invalid_forge_slug:
        displayed["error"] = INVALID_FORGE_SLUG_ERROR
        displayed["phase"] = "failed"
    elif status == "failed":
        displayed["phase"] = "failed"
    elif separator and prefix in {"extracting", "forging"}:
        displayed["phase"] = prefix
    elif status == "done" and kind == "push":
        displayed["phase"] = "sent"
    elif status == "running" and kind == "push":
        displayed["phase"] = "sending"
    elif status == "done" and (
        kind in {"prepare_url", "upload_prepare", "forge"}
        or (kind == "librivox" and collection_stage == "forged")
    ):
        displayed["phase"] = "ready"
    elif status == "running" and kind in {"prepare_url", "librivox", "upload_prepare"}:
        displayed["phase"] = "extracting"
    elif status == "running" and kind == "forge":
        displayed["phase"] = "forging"
    else:
        displayed["phase"] = status
    return displayed


def _handle(job: dict) -> dict:
    kind = job["kind"]
    payload = job["payload"]
    job_id = job["id"]

    def progress(message: str, percent: float | None = None) -> None:
        # The percentage is written every time, including as NULL. Leaving a
        # stale figure behind would show a full bar during a phase that cannot
        # measure itself, which is a lie the indeterminate bar does not tell.
        db.update_job(job_id, progress=message, progress_percent=percent)

    if kind == "librivox":
        current = dict(payload)
        options = {**prepare.DEFAULT_OPTIONS, **current.get("options", {})}
        stage_id = current.get("stage_id")
        if not stage_id:
            stage_id = f"librivox-{uuid4().hex}"
            current["stage_id"] = stage_id
            current["options"] = options
            db.update_job(job_id, payload=current)
        published = library.find_published_stage(stage_id)
        if published:
            return published
        extracted = library.collection_stage(stage_id)
        if not extracted or not library.collection_stage_ready(stage_id):
            extracted = ingest.import_librivox(
                current["book_id"],
                stage_id=stage_id,
                progress=lambda message, percent=None: progress(f"extracting: {message}", percent),
            )
            current["slug"] = extracted["slug"]
            db.update_job(job_id, payload=current)
        return forge.run_collection_stage(
            stage_id,
            normalize=options["normalize"],
            clean_titles=options["clean_titles"],
            trim_head=options["trim_head"],
            trim_tail=options["trim_tail"],
            split_oversized=options["split_oversized"],
            progress=lambda message, percent=None: progress(f"forging: {message}", percent),
        )

    if kind == "prepare_url":
        return prepare.run(
            payload,
            progress=progress,
            checkpoint=lambda updated_payload: db.update_job(job_id, payload=updated_payload),
        )

    if kind == "upload_prepare":
        current = dict(payload)
        options = {**prepare.DEFAULT_OPTIONS, **current.get("options", {})}
        stage = (config.upload_stage_dir() / current["stage"]).resolve()
        stage_root = config.upload_stage_dir().resolve()
        files = current.get("files", [])
        collection_stage_id = current.get("collection_stage_id")
        if not collection_stage_id:
            collection_stage_id = current["stage"]
            current["collection_stage_id"] = collection_stage_id
            current["options"] = options
            db.update_job(job_id, payload=current)
        published = library.find_published_stage(collection_stage_id)
        if published:
            if stage.parent == stage_root:
                shutil.rmtree(stage, ignore_errors=True)
            return published
        if stage.parent != stage_root or not stage.is_dir():
            library.discard_collection_stage(collection_stage_id)
            raise RuntimeError(
                "Upload staging expired after 24 hours. "
                "Select the files and submit the collection again."
            )
        collection = library.collection_stage(collection_stage_id)
        if not collection:
            current["next_file"] = 0
            fallback_title = files[0]["name"] if files else "Uploaded collection"
            collection = library.begin_collection_stage(
                collection_stage_id,
                title=current.get("title") or fallback_title,
                source="upload",
            )
            current["slug"] = collection.slug
            db.update_job(job_id, payload=current)

        start = int(current.get("next_file") or 0)
        try:
            for index in range(start, len(files)):
                item = files[index]
                target = item.get("target") or upload_target_name(item, index)
                current["pending_file"] = {"position": index, "target": target}
                db.update_job(job_id, payload=current)
                progress(f"extracting: Importing file {index + 1} of {len(files)}: {item['name']}")
                ingest.import_upload(
                    stage / item["stored"],
                    filename=item["name"],
                    stage_id=collection_stage_id,
                    target_name=target,
                )
                current["next_file"] = index + 1
                current.pop("pending_file", None)
                db.update_job(job_id, payload=current)
            library.complete_collection_stage(collection_stage_id)
        except Exception:
            mark_upload_stage(stage)
            raise

        try:
            result = forge.run_collection_stage(
                collection_stage_id,
                normalize=options["normalize"],
                clean_titles=options["clean_titles"],
                trim_head=options["trim_head"],
                trim_tail=options["trim_tail"],
                split_oversized=options["split_oversized"],
                progress=lambda message, percent=None: progress(f"forging: {message}", percent),
            )
        except Exception:
            mark_upload_stage(stage)
            raise
        shutil.rmtree(stage)
        return result

    if kind == "forge":
        return forge.run(
            payload["slug"],
            operation_id=payload["forge_operation_id"],
            normalize=payload.get("normalize", True),
            clean_titles=payload.get("clean_titles", True),
            trim_head=float(payload.get("trim_head") or 0),
            trim_tail=float(payload.get("trim_tail") or 0),
            split_oversized=payload.get("split_oversized", True),
            progress=progress,
        )

    if kind == "push":
        return push.push_confirmed(payload, progress=progress)

    raise RuntimeError(f"Unknown job kind: {kind}")


def _worker() -> None:
    while not _stop.is_set():
        job = db.claim_job()
        if job is None:
            _stop.wait(1.5)
            continue
        try:
            result = _handle(job)
            db.update_job(job["id"], status="done", progress="Finished",
                          result=result or {}, error="")
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI verbatim
            traceback.print_exc()
            db.update_job(job["id"], status="failed",
                          error=str(exc) or exc.__class__.__name__)


def start() -> None:
    db.init()
    library.recover_collection_publications()
    interrupted = db.fail_stale_running()
    for job in interrupted:
        if job.get("kind") != "upload_prepare":
            continue
        stage_name = job.get("payload", {}).get("stage")
        if isinstance(stage_name, str):
            mark_upload_stage(config.upload_stage_dir() / stage_name)
    library.sweep_collection_stages(db.referenced_collection_stage_ids())
    sweep_upload_staging()
    for _ in range(config.WORKER_THREADS):
        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        _threads.append(thread)


def stop() -> None:
    _stop.set()
