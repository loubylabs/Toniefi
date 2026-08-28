"""Two Creative Tonie operations, sharing one Tonie Cloud client.

The chapter layer rewrites the chapter list a Tonie already has (rename,
reorder, remove, clear); it uploads nothing and never reads the library on
disk. Send takes a group of library tracks, uploads them and appends a
chapter per track.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
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
_target_locks_guard = threading.Lock()
_target_locks: dict[tuple[str, str], threading.RLock] = {}


@contextmanager
def target_lease(household_id: str, tonie_id: str):
    """Serialize every write to one Creative Tonie in this process."""
    key = (str(household_id), str(tonie_id))
    with _target_locks_guard:
        lock = _target_locks.setdefault(key, threading.RLock())
    with lock:
        yield


class StaleChapters(RuntimeError):
    """The Tonie's chapters changed since the browser last looked at them."""


class StalePush(RuntimeError):
    """A confirmed local or remote send precondition no longer holds."""


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
    with target_lease(household_id, tonie_id):
        return _set_tonie_chapters_locked(household_id, tonie_id, base, requested)


def _set_tonie_chapters_locked(
    household_id: str,
    tonie_id: str,
    base: list[dict],
    requested: list[dict],
) -> dict[str, Any]:
    client = client_from_settings()
    try:
        tonie = client.get_tonie(household_id, tonie_id)
        current = tonie.get("chapters") or []
        merged = merge_chapters(current, base, requested)

        # get_tonie does not carry the household, but a GET /api/tonies entry
        # does, and this response has to match it for every caller, not just
        # for the browser.
        household_name = ""
        for house in client.households():
            if house.get("id") == household_id:
                household_name = house.get("name", "")
                break

        kept = {c.get("id") for c in merged}
        present = float(tonie.get("secondsPresent") or 0)
        surviving = sum(
            float(c.get("seconds") or 0) for c in current if c.get("id") in kept
        )
        dropped = [c for c in current if c.get("id") not in kept]

        # Four cases, attributed by what a save actually drops. The Cloud's
        # secondsPresent and the sum of the chapters' own reported seconds
        # can legitimately disagree in either direction (a chapter still
        # transcoding reports less than it will finish with), and that
        # disagreement is not this endpoint's business unless a chapter is
        # actually removed.
        if not merged:
            # Clearing is exact: nothing survives, so there is nothing to
            # carry.
            seconds_present = 0.0
        elif not dropped:
            # Rename and reorder drop nothing, so no audio changed and the
            # Cloud's own figure passes through untouched. Recomputing it
            # from the chapters' reported seconds, even to "correct" it
            # toward that sum, is what let a rename move the number: up when
            # the Cloud was still counting more than the chapters had
            # finished reporting, down when the chapters happened to report
            # more than the Cloud's own total.
            seconds_present = present
        elif all(float(c.get("seconds") or 0) for c in dropped):
            # Every dropped chapter reported a real duration, so the Cloud's
            # total minus exactly what left is what remains, floored at what
            # the survivors themselves report. The floor matters because
            # present and the chapters' reported seconds can disagree in
            # either direction: when present sits below the reported total,
            # subtracting the dropped chapter's full duration can land under
            # what the survivors demonstrably occupy, understating present
            # and so overstating free space. This floor subsumes the plain
            # max(0.0, ...) it replaces, since a sum of durations is never
            # negative.
            dropped_seconds = sum(float(c.get("seconds") or 0) for c in dropped)
            seconds_present = max(surviving, present - dropped_seconds)
        else:
            # A dropped chapter reported 0, most likely because it was still
            # transcoding. It may own part of whatever the Cloud counts
            # beyond the chapters' own reported total, and that remainder
            # cannot be split, so it leaves with the dropped chapter rather
            # than inflating the survivors. Erring low is the safer
            # direction: this figure feeds the free-space readout, and
            # overstating what a Tonie holds is what sends a user's next
            # push into a refusal.
            seconds_present = surviving

        tonie["chapters"] = merged
        tonie["secondsPresent"] = seconds_present
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


def confirmed_tracks(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    slug = payload["slug"]
    manifest = library.get(slug)
    if not manifest:
        raise StalePush(f"The collection changed because {slug} no longer exists.")
    if library.manifest_fingerprint(manifest) != payload["manifest_fingerprint"]:
        raise StalePush("The local collection changed after confirmation. Review it again.")
    names = payload.get("files") or []
    by_name = {track["name"]: track for track in manifest["tracks"]}
    if len(names) != len(set(names)) or any(name not in by_name for name in names):
        raise StalePush("The confirmed audio files no longer match this collection.")
    tracks = [by_name[name] for name in names]
    for track in tracks:
        try:
            library.track_path(slug, track["name"])
        except ValueError as exc:
            raise StalePush("A confirmed audio file is no longer available.") from exc
    return manifest, tracks


def validate_confirmed_groups(slug: str, fingerprint: str, assignments: list[dict[str, Any]]) -> None:
    manifest = library.get(slug)
    if not manifest or library.manifest_fingerprint(manifest) != fingerprint:
        raise StalePush("The local collection changed after review. Review it again.")
    planned = [[track["name"] for track in group.as_dict()["tracks"]]
               for group in library.plan_groups(manifest["tracks"])]
    received = [assignment.get("files") or [] for assignment in assignments]
    if received != planned:
        raise StalePush("The confirmed files do not match the reviewed capacity plan.")


def _remote_identity(chapters: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(chapter.get("id"), chapter.get("title") or "") for chapter in chapters]


def push_confirmed(payload: dict[str, Any], progress: Progress = _noop) -> dict[str, Any]:
    with library.collection_lease():
        _, tracks = confirmed_tracks(payload)
        return _push_confirmed_tracks(payload, tracks, progress)


def _push_confirmed_tracks(
    payload: dict[str, Any],
    tracks: list[dict[str, Any]],
    progress: Progress,
) -> dict[str, Any]:
    slug = payload["slug"]
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

    with target_lease(payload["household_id"], payload["tonie_id"]):
        client = client_from_settings()
        try:
            progress("Signing in to myTonies")
            client.check_login()

            state = client.get_tonie(payload["household_id"], payload["tonie_id"])
            current_chapters = state.get("chapters") or []
            if _remote_identity(current_chapters) != _remote_identity(payload.get("remote_chapters") or []):
                raise StalePush("The Creative Tonie changed after confirmation. Refresh targets and review again.")
            replace = payload["replace"]
            if not replace:
                present = float(state.get("secondsPresent") or 0)
                if total > max(0, limit - present):
                    raise StalePush("The Creative Tonie no longer has enough free space for this append.")

            if replace:
                progress("Clearing the Tonie")
                client.clear_tonie(payload["household_id"], payload["tonie_id"])

            uploaded = []
            for position, track in enumerate(tracks, start=1):
                path = library.track_path(slug, track["name"])
                label = track.get("title") or Path(track["name"]).stem
                progress(f"Uploading {position}/{len(tracks)}: {label}")
                file_id = client.upload_file(path)
                client.add_chapter(payload["household_id"], payload["tonie_id"], label, file_id)
                uploaded.append({"title": label, "file": file_id})

            progress("Confirming")
            state = client.get_tonie(payload["household_id"], payload["tonie_id"])
            return {
                "tonie": state.get("name", payload["tonie_id"]),
                "chapters": len(state.get("chapters", [])),
                "uploaded": uploaded,
                "seconds": round(total, 1),
                "duration": audio.human_duration(total),
            }
        finally:
            client.close()
