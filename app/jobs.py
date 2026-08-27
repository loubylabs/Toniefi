"""A small background worker. Extraction, forging and uploads all take
minutes, and an HTTP request should not be holding the bag while they run."""
from __future__ import annotations

import threading
import traceback

from . import config, db, forge, ingest, push

_threads: list[threading.Thread] = []
_stop = threading.Event()


def enqueue(kind: str, label: str, payload: dict) -> int:
    return db.create_job(kind, label, payload)


def _handle(job: dict) -> dict:
    kind = job["kind"]
    payload = job["payload"]
    job_id = job["id"]

    def progress(message: str) -> None:
        db.update_job(job_id, progress=message)

    if kind == "librivox":
        return ingest.import_librivox(payload["book_id"], progress)

    if kind == "url":
        return ingest.import_url(
            payload["url"],
            title=payload.get("title"),
            slug=payload.get("slug"),
            use_chapters=payload.get("use_chapters", True),
            progress=progress,
        )

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
