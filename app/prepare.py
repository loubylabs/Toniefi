"""Extract a source and run its default Forge sequence as one resumable job."""
from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from . import forge, ingest, library

DEFAULT_OPTIONS = {
    "use_chapters": True,
    "normalize": True,
    "clean_titles": True,
    "trim_head": 0,
    "trim_tail": 0,
    "split_oversized": True,
}

Progress = Callable[..., None]
"""A progress reporter: progress(message) or progress(message, percent).

`percent` is a float 0-100 when the phase can measure itself, and None when it
cannot. It is always passed through rather than remembered, so a phase with
nothing to measure clears the last figure instead of leaving a full bar over
work that is still running.
"""
Checkpoint = Callable[[dict[str, Any]], None]


def run(payload: dict[str, Any], *, progress: Progress, checkpoint: Checkpoint) -> dict[str, Any]:
    current = dict(payload)
    options = {**DEFAULT_OPTIONS, **current.get("options", {})}
    current["options"] = options
    stage_id = current.get("stage_id")
    if not stage_id:
        stage_id = f"url-{uuid4().hex}"
        current["stage_id"] = stage_id
        checkpoint(current)
    published = library.find_published_stage(stage_id)
    if published:
        return published
    extracted = library.collection_stage(stage_id)
    if not extracted or not library.collection_stage_ready(stage_id):
        extracted = ingest.import_url(
            current["url"],
            stage_id=stage_id,
            use_chapters=options["use_chapters"],
            playlist_items=current.get("playlist_items"),
            progress=lambda message, percent=None: progress(f"extracting: {message}", percent),
        )
        current["slug"] = extracted["slug"]
        checkpoint(current)
    return forge.run_collection_stage(
        stage_id,
        normalize=options["normalize"],
        clean_titles=options["clean_titles"],
        trim_head=options["trim_head"],
        trim_tail=options["trim_tail"],
        split_oversized=options["split_oversized"],
        progress=lambda message, percent=None: progress(f"forging: {message}", percent),
    )
