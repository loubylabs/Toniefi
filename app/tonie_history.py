"""What used to be on each Creative Tonie.

The Tonie Cloud replaces a chapter list whole and has no undo, so this keeps
every distinct list Toniefi has seen. Read only: there is no restore.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from . import audio, db

log = logging.getLogger(__name__)


def _snapshot(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"id": c.get("id") or "", "title": c.get("title") or "", "seconds": float(c.get("seconds") or 0)}
        for c in chapters
    ]


def remember(household_id: str, tonie_id: str, tonie: dict[str, Any], source: str) -> None:
    """Store this Tonie's chapter list if it changed. Never raises.

    It runs right after landed Tonie writes, where an exception would report
    a write the Cloud accepted as a failure.
    """
    try:
        db.record_tonie_version(
            household_id, tonie_id, tonie.get("name") or "",
            _snapshot(tonie.get("chapters") or []), source, time.time(),
        )
    except Exception:
        log.exception("Could not record a version of Tonie %s", tonie_id)


def changes(previous: list[dict[str, Any]] | None, current: list[dict[str, Any]]) -> dict[str, Any]:
    if previous is None:
        return {"first": True, "added": 0, "removed": 0, "renamed": 0, "reordered": False}
    before = {c["id"]: c["title"] for c in previous}
    after = {c["id"]: c["title"] for c in current}
    kept_before = [c["id"] for c in previous if c["id"] in after]
    kept_after = [c["id"] for c in current if c["id"] in before]
    return {
        "first": False,
        "added": len(after.keys() - before.keys()),
        "removed": len(before.keys() - after.keys()),
        "renamed": sum(1 for i in kept_after if before[i] != after[i]),
        "reordered": kept_before != kept_after,
    }


def versions(household_id: str, tonie_id: str) -> list[dict[str, Any]]:
    out = []
    previous = None
    for version in db.tonie_versions(household_id, tonie_id):
        version["changes"] = changes(previous, version["chapters"])
        previous = version["chapters"]
        version["chapters"] = [
            {**c, "duration": audio.human_duration(c["seconds"]) if c["seconds"] else ""}
            for c in version["chapters"]
        ]
        out.append(version)
    return out[::-1]
