"""A small background worker. Extraction, forging and uploads all take
minutes, and an HTTP request should not be holding the bag while they run."""
from __future__ import annotations

import shutil
import threading
import traceback

from . import config, db, forge, ingest, library, prepare, push

_threads: list[threading.Thread] = []
_stop = threading.Event()


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
            raise RuntimeError("The staged upload files are missing. Upload the collection again.")
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
                progress(f"extracting: Importing file {index + 1} of {len(files)}: {item['name']}")
                ingest.import_upload(
                    item["name"],
                    (stage / item["stored"]).read_bytes(),
                    slug,
                )
                current["next_file"] = index + 1
                db.update_job(job_id, payload=current)
        except Exception:
            if current.get("owns_collection"):
                try:
                    library.delete(slug)
                except (FileNotFoundError, ValueError):
                    pass
                current.pop("slug", None)
                current.pop("owns_collection", None)
                current["next_file"] = 0
                db.update_job(job_id, payload=current)
            raise

        result = forge.run(
            slug,
            normalize=options["normalize"],
            clean_titles=options["clean_titles"],
            trim_head=options["trim_head"],
            trim_tail=options["trim_tail"],
            split_oversized=options["split_oversized"],
            progress=lambda message: progress(f"forging: {message}"),
        )
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
    db.requeue_stale_running()
    for _ in range(config.WORKER_THREADS):
        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        _threads.append(thread)


def stop() -> None:
    _stop.set()
