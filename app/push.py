"""Pushing a set of library tracks onto a Creative Tonie."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import audio, config, library, tonies

Progress = Callable[[str], None]


def _noop(_: str) -> None:
    return None


TITLE_LIMIT = 128


class StaleChapters(RuntimeError):
    """The Tonie's chapters changed since the browser last looked at them."""


def merge_chapters(
    current: list[dict], base_ids: list[str], requested: list[dict]
) -> list[dict]:
    """Build the chapter list to PATCH onto a Tonie.

    `current`   the Tonie's chapters as the Tonie Cloud reports them now
    `base_ids`  every chapter id the browser had on screen when it decided
    `requested` the chapters to keep, in order, as {"id", "title"}

    Chapters absent from `requested` are dropped: that is how remove and clear
    work. Only the title and the order are ever written, so a field this code
    has never heard of survives the round trip untouched.
    """
    current_ids = [c.get("id") for c in current]

    # Compare the whole set, not just the requested ids. Matching only what was
    # asked for would catch a chapter deleted elsewhere but silently destroy one
    # ADDED elsewhere, because a whole-list PATCH drops whatever it omits.
    if set(base_ids) != set(current_ids):
        raise StaleChapters("This Tonie changed somewhere else. Reloading.")

    known = set(base_ids)
    seen: set[str] = set()
    by_id = {c.get("id"): c for c in current}
    out: list[dict] = []

    for entry in requested:
        chapter_id = entry.get("id")
        if chapter_id not in known:
            raise ValueError(f"Chapter {chapter_id!r} is not on this Tonie.")
        if chapter_id in seen:
            raise ValueError(f"Chapter {chapter_id!r} was listed twice.")
        seen.add(chapter_id)

        merged = dict(by_id[chapter_id])
        title = (entry.get("title") or "").strip()[:TITLE_LIMIT]
        if title:
            merged["title"] = title
        out.append(merged)

    return out


def describe_tonie(tonie: dict[str, Any]) -> dict[str, Any]:
    """Decorate one Creative Tonie for the front end.

    Both GET /api/tonies and the chapter write return this shape, so the
    browser can swap a single Tonie in place after a save.
    """
    raw = tonie.get("chapters") or []
    tonie["chapters"] = [
        {
            "id": chapter.get("id"),
            "title": chapter.get("title") or "",
            "seconds": float(chapter.get("seconds") or 0),
            # A chapter mid-transcode reports no length. Blank beats "0m 00s".
            "duration": (
                audio.human_duration(float(chapter.get("seconds") or 0))
                if float(chapter.get("seconds") or 0) else ""
            ),
            "transcoding": bool(chapter.get("transcoding")),
        }
        for chapter in raw
    ]
    tonie["chapter_count"] = len(raw)

    seconds = float(tonie.get("secondsPresent") or 0)
    tonie["seconds_present"] = seconds
    tonie["time_used"] = audio.human_duration(seconds)
    tonie["seconds_free"] = max(0, config.TONIE_LIMIT_SECONDS - seconds)
    tonie["time_free"] = audio.human_duration(tonie["seconds_free"])
    return tonie


def client_from_settings() -> tonies.TonieCloud:
    """Env vars win; the UI-stored credentials are the fallback."""
    from . import db

    username = config.TONIES_USERNAME or db.get_setting("tonies_username")
    password = config.TONIES_PASSWORD or db.get_setting("tonies_password")
    if not username or not password:
        raise tonies.AuthError(
            "No myTonies credentials configured. Set TONIES_USERNAME and "
            "TONIES_PASSWORD, or save them on the Settings tab."
        )
    return tonies.TonieCloud(username, password)


def resolve_tracks(slug: str, names: list[str] | None, group_index: int | None) -> list[dict[str, Any]]:
    manifest = library.get(slug)
    if not manifest:
        raise RuntimeError(f"No collection named {slug}.")
    tracks = manifest["tracks"]

    if names:
        by_name = {t["name"]: t for t in tracks}
        missing = [n for n in names if n not in by_name]
        if missing:
            raise RuntimeError(f"Not in this collection: {', '.join(missing)}")
        return [by_name[n] for n in names]

    if group_index:
        groups = library.plan_groups(tracks)
        if group_index < 1 or group_index > len(groups):
            raise RuntimeError(f"This collection only has {len(groups)} group(s).")
        by_name = {t["name"]: t for t in tracks}
        # Match on filename only: two chapters can share a title.
        return [by_name[str(t.path)] for t in groups[group_index - 1].tracks
                if str(t.path) in by_name]

    return tracks


def push(
    slug: str,
    household_id: str,
    tonie_id: str,
    names: list[str] | None = None,
    group_index: int | None = None,
    replace: bool = True,
    progress: Progress = _noop,
) -> dict[str, Any]:
    tracks = resolve_tracks(slug, names, group_index)
    if not tracks:
        raise RuntimeError("Nothing selected to push.")

    total = sum(t.get("seconds", 0) for t in tracks)
    limit = config.TONIE_LIMIT_SECONDS
    if total > limit:
        raise RuntimeError(
            f"That selection is {audio.human_duration(total)}, over the "
            f"{audio.human_duration(limit)} a Creative Tonie holds. "
            f"Split it or push one group at a time."
        )

    client = client_from_settings()
    try:
        progress("Signing in to myTonies")
        client.check_login()

        if replace:
            progress("Clearing the Tonie")
            client.clear_tonie(household_id, tonie_id)

        uploaded = []
        for position, track in enumerate(tracks, start=1):
            path = library.track_path(slug, track["name"])
            label = track.get("title") or Path(track["name"]).stem
            progress(f"Uploading {position}/{len(tracks)}: {label}")
            file_id = client.upload_file(path)
            client.add_chapter(household_id, tonie_id, label, file_id)
            uploaded.append({"title": label, "file": file_id})

        progress("Confirming")
        state = client.get_tonie(household_id, tonie_id)
        return {
            "tonie": state.get("name", tonie_id),
            "chapters": len(state.get("chapters", [])),
            "uploaded": uploaded,
            "seconds": round(total, 1),
            "duration": audio.human_duration(total),
        }
    finally:
        client.close()
