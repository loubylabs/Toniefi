"""Two Creative Tonie operations, sharing one Tonie Cloud client.

Send takes a group of library tracks, uploads them and appends a chapter
per track. The chapter layer rewrites the chapter list a Tonie already has
(rename, reorder, remove, clear); it uploads nothing and never reads the
library on disk.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import audio, config, library, tonies


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


# ------------------------------------------------------ chapter management

TITLE_LIMIT = 128


class StaleChapters(RuntimeError):
    """The Tonie's chapters changed since the browser last looked at them."""


def _identity(chapter: dict) -> tuple[str, str]:
    """The (id, title) pair the precondition compares.

    A raw Tonie Cloud chapter can carry `title: None` while describe_tonie
    hands the browser "", so both sides are normalised here. Without that,
    every save on such a chapter would 409 forever.
    """
    return (chapter.get("id"), chapter.get("title") or "")


def merge_chapters(
    current: list[dict], base: list[dict], requested: list[dict]
) -> list[dict]:
    """Build the chapter list to PATCH onto a Tonie.

    `current`   the Tonie's chapters as the Tonie Cloud reports them now
    `base`      every chapter the browser had on screen when it decided, as
                {"id", "title"}. Both fields are part of the precondition.
    `requested` the chapters to keep, in order, as {"id", "title"}

    Chapters absent from `requested` are dropped: that is how remove and clear
    work. Only the title and the order are ever written, so a field this code
    has never heard of survives the round trip untouched.
    """
    # Compare the whole set of (id, title) pairs, not just the requested ids.
    # Matching only what was asked for would catch a chapter deleted elsewhere
    # but silently destroy one ADDED elsewhere, because a whole-list PATCH
    # drops whatever it omits. Comparing ids alone would then still miss a
    # chapter RENAMED elsewhere: the id sets match, and the write sends the
    # other client's chapter back under its old title. The rename is the one
    # field this endpoint writes, so it is the one an id-only guard is blind to.
    #
    # Order is deliberately out of the precondition. Reordering is exactly what
    # this endpoint is for, and the last writer should win on order alone,
    # which is what comparing sets rather than lists buys.
    if {_identity(c) for c in current} != {_identity(c) for c in base}:
        raise StaleChapters("This Tonie changed somewhere else. Reloading.")

    known = {c.get("id") for c in base}
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
        current_title = merged.get("title") or ""
        wanted = entry.get("title") or ""
        # Compare the raw title before normalising. A title the myTonies app
        # created with leading whitespace, or past the cap, must survive a save
        # that never touched it: otherwise reordering one chapter quietly
        # rewrites all twelve, and there is no undo.
        if wanted != current_title:
            wanted = wanted.strip()[:TITLE_LIMIT]
            # An empty title keeps the current one, so a slipped keystroke
            # cannot leave a nameless chapter on a Tonie.
            if wanted:
                merged["title"] = wanted
        out.append(merged)

    return out


def describe_tonie(tonie: dict[str, Any]) -> dict[str, Any]:
    """Decorate one Creative Tonie for the front end.

    Both GET /api/tonies and the chapter write return this shape, so the
    browser can swap a single Tonie in place after a save.
    """
    raw = tonie.get("chapters") or []
    chapters = []
    for chapter in raw:
        seconds = float(chapter.get("seconds") or 0)
        chapters.append(
            {
                "id": chapter.get("id"),
                "title": chapter.get("title") or "",
                "seconds": seconds,
                # A chapter mid-transcode reports no length. Blank beats "0m 00s".
                "duration": audio.human_duration(seconds) if seconds else "",
                "transcoding": bool(chapter.get("transcoding")),
            }
        )
    tonie["chapters"] = chapters
    tonie["chapter_count"] = len(raw)

    seconds = float(tonie.get("secondsPresent") or 0)
    tonie["seconds_present"] = seconds
    tonie["time_used"] = audio.human_duration(seconds)
    tonie["seconds_free"] = max(0, config.TONIE_LIMIT_SECONDS - seconds)
    tonie["time_free"] = audio.human_duration(tonie["seconds_free"])
    return tonie


def set_tonie_chapters(
    household_id: str,
    tonie_id: str,
    base: list[dict],
    requested: list[dict],
) -> dict[str, Any]:
    """Rewrite a Tonie's chapter list. Rename, reorder and remove are all
    this one call, because the Tonie Cloud only offers a whole-list PATCH.
    """
    client = client_from_settings()
    try:
        tonie = client.get_tonie(household_id, tonie_id)
        merged = merge_chapters(tonie.get("chapters") or [], base, requested)

        # get_tonie does not carry the household, but a GET /api/tonies entry
        # does, and this response has to match it for every caller, not just
        # for the browser.
        household_name = ""
        for house in client.households():
            if house.get("id") == household_id:
                household_name = house.get("name", "")
                break

        kept = {c.get("id") for c in merged}
        dropped = sum(
            float(c.get("seconds") or 0)
            for c in (tonie.get("chapters") or [])
            if c.get("id") not in kept
        )
        present = float(tonie.get("secondsPresent") or 0)

        tonie["chapters"] = merged
        # Clearing is exact. Otherwise keep the Cloud's own figure and subtract
        # only what was removed: recomputing from the remaining chapters would
        # drop whatever the Cloud counts for a chapter that is still
        # transcoding and so reports no length of its own.
        tonie["secondsPresent"] = 0 if not merged else max(0, present - dropped)
        tonie["householdId"] = household_id
        tonie["householdName"] = household_name

        # Build the answer BEFORE the write. Nothing that can raise may run
        # after set_chapters, or a landed PATCH gets reported as a failure and
        # the user retries into a 409.
        answer = describe_tonie(tonie)
        client.set_chapters(household_id, tonie_id, merged)
        return answer
    finally:
        client.close()


# -------------------------------------------------- sending library tracks

Progress = Callable[[str], None]


def _noop(_: str) -> None:
    return None


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
