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

UPLOAD_MAX_FILES = 500
UPLOAD_MAX_BYTES = 20 * 1024 * 1024 * 1024
UPLOAD_STAGE_RETENTION_SECONDS = 24 * 60 * 60
UPLOAD_STAGE_MARKER = ".toniefi-upload-stage.json"
_now = time.time


def _owned_upload_stage(stage: Path) -> bool:
    return (
        stage.parent == config.WORK_DIR.resolve()
        and stage.name.startswith("upload-")
        and not stage.is_symlink()
        and (stage / UPLOAD_STAGE_MARKER).is_file()
    )


def mark_upload_stage(stage: Path) -> None:
    stage = stage.resolve()
    if not _owned_upload_stage(stage):
        return
    (stage / UPLOAD_STAGE_MARKER).write_text(
        json.dumps({"retained_at": _now()}),
        encoding="utf-8",
    )


def sweep_upload_staging() -> None:
    """Remove expired TonieFi upload stages and leave all other work intact."""
    config.ensure_dirs()
    cutoff = _now() - UPLOAD_STAGE_RETENTION_SECONDS
    active_stages = db.active_upload_stages()
    for stage in config.WORK_DIR.iterdir():
        if not stage.is_dir() or stage.is_symlink() or not stage.name.startswith("upload-"):
            continue
        marker = stage / UPLOAD_STAGE_MARKER
        if not marker.is_file():
            continue
        if stage.name in active_stages:
            continue
        try:
            retained_at = float(json.loads(marker.read_text(encoding="utf-8"))["retained_at"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            continue
        if retained_at <= cutoff:
            shutil.rmtree(stage)


def create_upload_stage() -> tuple[str, Path]:
    config.ensure_dirs()
    stage_name = f"upload-{uuid4().hex}"
    stage = config.WORK_DIR / stage_name
    stage.mkdir()
    (stage / UPLOAD_STAGE_MARKER).write_text(
        json.dumps({"retained_at": _now()}),
        encoding="utf-8",
    )
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


def enqueue_many(entries: list[tuple[str, str, dict]]) -> list[int]:
    return db.create_jobs(entries)


def present(job: dict) -> dict:
    """Add display-only phase data without changing stored job history."""
    displayed = dict(job)
    stored_progress = displayed.get("progress", "")
    prefix, separator, message = stored_progress.partition(": ")
    if separator and prefix in {"extracting", "forging"}:
        displayed["progress"] = message

    status = displayed.get("status", "")
    kind = displayed.get("kind", "")
    if status == "failed":
        displayed["phase"] = "failed"
    elif separator and prefix in {"extracting", "forging"}:
        displayed["phase"] = prefix
    elif status == "done" and kind in {"prepare_url", "upload_prepare", "forge"}:
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

    def progress(message: str) -> None:
        db.update_job(job_id, progress=message)

    if kind == "librivox":
        current = dict(payload)
        options = {**prepare.DEFAULT_OPTIONS, **current.get("options", {})}
        slug = current.get("slug")
        collection = library.get(slug) if slug else None
        if collection and collection.get("stage") == "forged":
            return collection
        if not collection:
            extracted = ingest.import_librivox(
                current["book_id"],
                lambda message: progress(f"extracting: {message}"),
            )
            slug = extracted["slug"]
            current["slug"] = slug
            current["options"] = options
            db.update_job(job_id, payload=current)
        return forge.run(
            slug,
            normalize=options["normalize"],
            clean_titles=options["clean_titles"],
            trim_head=options["trim_head"],
            trim_tail=options["trim_tail"],
            split_oversized=options["split_oversized"],
            progress=lambda message: progress(f"forging: {message}"),
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
        stage = (config.WORK_DIR / current["stage"]).resolve()
        work_root = config.WORK_DIR.resolve()
        files = current.get("files", [])
        slug = current.get("slug")
        collection = library.get(slug) if slug else None
        if collection and collection.get("stage") == "forged":
            if stage.parent == work_root:
                shutil.rmtree(stage, ignore_errors=True)
            return collection
        if stage.parent != work_root or not stage.is_dir():
            raise RuntimeError(
                "Upload staging expired after 24 hours. "
                "Select the files and submit the collection again."
            )
        if slug and not collection:
            current.pop("slug", None)
            current.pop("owns_collection", None)
            current["next_file"] = 0
            slug = None
        if not slug:
            fallback_title = files[0]["name"] if files else "Uploaded collection"
            slug = library.create(current.get("title") or fallback_title, source="upload")
            current["slug"] = slug
            current["next_file"] = 0
            current["owns_collection"] = True
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
                    slug=slug,
                    target_name=target,
                )
                current["next_file"] = index + 1
                current.pop("pending_file", None)
                db.update_job(job_id, payload=current)
        except Exception:
            mark_upload_stage(stage)
            if current.get("owns_collection"):
                try:
                    library.delete(slug)
                except (FileNotFoundError, ValueError):
                    pass
                current.pop("slug", None)
                current.pop("owns_collection", None)
                current.pop("pending_file", None)
                current["next_file"] = 0
                db.update_job(job_id, payload=current)
            raise

        try:
            result = forge.run(
                slug,
                normalize=options["normalize"],
                clean_titles=options["clean_titles"],
                trim_head=options["trim_head"],
                trim_tail=options["trim_tail"],
                split_oversized=options["split_oversized"],
                progress=lambda message: progress(f"forging: {message}"),
            )
        except Exception:
            mark_upload_stage(stage)
            raise
        shutil.rmtree(stage)
        return result

    if kind == "forge":
        return forge.run(
            payload["slug"],
            normalize=payload.get("normalize", True),
            clean_titles=payload.get("clean_titles", True),
            trim_head=float(payload.get("trim_head") or 0),
            trim_tail=float(payload.get("trim_tail") or 0),
            split_oversized=payload.get("split_oversized", True),
            progress=progress,
        )

    if kind == "push":
        return push.push(
            payload["slug"],
            payload["household_id"],
            payload["tonie_id"],
            names=payload.get("names"),
            group_index=payload.get("group_index"),
            replace=payload.get("replace", True),
            progress=progress,
        )

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
    sweep_upload_staging()
    db.requeue_stale_running()
    for _ in range(config.WORKER_THREADS):
        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        _threads.append(thread)


def stop() -> None:
    _stop.set()
