"""Extract a source and run its default Forge sequence as one resumable job."""
from __future__ import annotations

from typing import Any, Callable

from . import forge, ingest, library

DEFAULT_OPTIONS = {
    "use_chapters": True,
    "normalize": True,
    "clean_titles": True,
    "trim_head": 0,
    "trim_tail": 0,
    "split_oversized": True,
}

Progress = Callable[[str], None]
Checkpoint = Callable[[dict[str, Any]], None]


def run(payload: dict[str, Any], *, progress: Progress, checkpoint: Checkpoint) -> dict[str, Any]:
    current = dict(payload)
    options = {**DEFAULT_OPTIONS, **current.get("options", {})}
    slug = current.get("slug")
    collection = library.get(slug) if slug else None
    if collection and collection.get("stage") == "forged":
        return collection
    if not collection:
        extracted = ingest.import_url(
            current["url"],
            use_chapters=options["use_chapters"],
            progress=lambda message: progress(f"extracting: {message}"),
        )
        slug = extracted["slug"]
        current["slug"] = slug
        current["options"] = options
        checkpoint(current)
    return forge.run(
        slug,
        normalize=options["normalize"],
        clean_titles=options["clean_titles"],
        trim_head=options["trim_head"],
        trim_tail=options["trim_tail"],
        split_oversized=options["split_oversized"],
        progress=lambda message: progress(f"forging: {message}"),
    )
