"""Two Creative Tonie operations, sharing one Tonie Cloud client.

The chapter layer rewrites the chapter list a Tonie already has (rename,
reorder, remove, clear); it uploads nothing and never reads the library on
disk. Send takes a group of library tracks, uploads them and appends a
chapter per track.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, TypedDict

from . import audio, config, library, tonies


class SelectedCredentials(TypedDict):
    source: str
    configured: bool
    username: str
    password: str


def select_credentials() -> SelectedCredentials:
    """Select one complete credential source without mixing pairs."""
    from . import db

    if config.TONIES_USERNAME or config.TONIES_PASSWORD:
        source = "environment"
        username = config.TONIES_USERNAME
        password = config.TONIES_PASSWORD
    else:
        username, password = db.get_credentials()
        source = "saved" if username or password else "none"
    return {
        "source": source,
        "configured": bool(username and password),
        "username": username,
        "password": password,
    }


def credential_status() -> dict[str, Any]:
    """Report the selected pair without exposing its password."""
    selected = select_credentials()
    return {
        "configured": selected["configured"],
        "source": selected["source"],
        "username": selected["username"],
    }


def client_from_settings() -> tonies.TonieCloud:
    """Create a client from the one atomically selected credential pair."""
    selected = select_credentials()
    if not selected["configured"]:
        if selected["source"] == "environment":
            raise tonies.AuthError(
                "Environment credentials are incomplete. Set both "
                "TONIES_USERNAME and TONIES_PASSWORD."
            )
        if selected["source"] == "saved":
            raise tonies.AuthError(
                "Saved myTonies credentials are incomplete. Save both the "
                "username and password."
            )
        raise tonies.AuthError(
            "No myTonies credentials configured. Set TONIES_USERNAME and "
            "TONIES_PASSWORD, or save them on the Settings tab."
        )
    return tonies.TonieCloud(selected["username"], selected["password"])


# ------------------------------------------------------ chapter management

TITLE_LIMIT = 128
# The Tonie Cloud documents maxLength 100 for a Creative Tonie's name. That is a
# different limit from TITLE_LIMIT, which caps a chapter title at 128. They are
# different fields on different resources; do not unify them.
NAME_LIMIT = 100
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


class StaleTonieName(RuntimeError):
    """This Tonie was renamed somewhere else since the browser last read it."""


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
    tonie["seconds_free"] = max(0, config.usable_limit() - seconds)
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


def set_tonie_name(
    household_id: str,
    tonie_id: str,
    base_name: str,
    name: str,
) -> dict[str, Any]:
    """Rename one Creative Tonie, without touching its chapters.

    `base_name` is the name the browser had on screen when the operator
    decided. The upstream offers no conditional write, so comparing it here
    narrows the lost-update window to a single round trip rather than the life
    of an open tab. It is the same guard merge_chapters applies, for the same
    reason.
    """
    wanted = (name or "").strip()[:NAME_LIMIT]
    if not wanted:
        # A chapter title keeps its old value when blanked, because a slipped
        # keystroke there must not leave a nameless chapter on a Tonie. A
        # rename is the opposite case: it is a deliberate act on one field, so
        # silently keeping the old name would report success for a write that
        # was never made.
        raise ValueError("A Creative Tonie needs a name.")

    with target_lease(household_id, tonie_id):
        client = client_from_settings()
        try:
            tonie = client.get_tonie(household_id, tonie_id)
            if (tonie.get("name") or "") != (base_name or ""):
                raise StaleTonieName("This Tonie was renamed somewhere else. Reloading.")

            # get_tonie does not carry the household, but a GET /api/tonies
            # entry does, and this response has to match it for every caller.
            household_name = ""
            for house in client.households():
                if house.get("id") == household_id:
                    household_name = house.get("name", "")
                    break

            tonie["name"] = wanted
            tonie["householdId"] = household_id
            tonie["householdName"] = household_name

            # Built BEFORE the write. Nothing that can raise may run after a
            # landed PATCH, or a rename the Tonie Cloud accepted is reported as
            # a failure and the operator retries a change already made.
            answer = describe_tonie(tonie)
            client.set_name(household_id, tonie_id, wanted)
            return answer
        finally:
            client.close()


# -------------------------------------------------- sending library tracks

Progress = Callable[..., None]


def _noop(*_: Any, **__: Any) -> None:
    return None


class ProgressThrottle:
    """Rate-limit progress writes so a byte counter is not one write per chunk.

    A 20 MB upload reads in thousands of chunks. Writing a row for each would
    hammer SQLite for a bar the browser only reads every 2.5 seconds. A report
    gets through when the figure has actually moved, or when enough time has
    passed, and flush always writes so the end of a phase is never lost.
    """

    def __init__(
        self,
        progress: Progress,
        min_interval: float = 0.5,
        min_delta: float = 1.0,
    ) -> None:
        self._progress = progress
        self._min_interval = min_interval
        self._min_delta = min_delta
        self._last_at = 0.0
        self._last_percent: float | None = None
        self._started = False

    def report(self, message: str, percent: float | None) -> None:
        now = time.monotonic()
        moved = (
            not self._started
            or self._last_percent is None
            or percent is None
            or abs(percent - self._last_percent) >= self._min_delta
        )
        if not moved and (now - self._last_at) < self._min_interval:
            return
        self.flush(message, percent)

    def flush(self, message: str, percent: float | None) -> None:
        self._started = True
        self._last_at = time.monotonic()
        self._last_percent = percent
        self._progress(message, percent)


def upload_percent(
    tracks: list[dict[str, Any]], done_index: int, position: int
) -> float | None:
    """How much of a send's audio is on the wire, by bytes.

    `done_index` counts fully uploaded tracks; `position` is how far into the
    current one the transport has read. Returns None when any track is missing
    a size, because a partial total would understate the work and so inflate
    the bar. An unknown percentage is the honest answer, and the front end
    already renders it as an indeterminate meter.
    """
    sizes = [track.get("size") for track in tracks]
    if any(not size for size in sizes):
        return None
    total = float(sum(sizes))
    if total <= 0:
        return None
    done = float(sum(sizes[:done_index]))
    return max(0.0, min(100.0, 100.0 * (done + float(position)) / total))


def _flatten(assignment: dict[str, Any]) -> list[tuple[str, str]]:
    """One assignment's confirmed files as ordered (slug, name) pairs.

    An assignment has no `files` field of its own: its file list is its sources
    flattened in source order, then file order. Every comparison below uses
    this one definition.
    """
    return [
        (source["slug"], name)
        for source in assignment["sources"]
        for name in source["files"]
    ]


def validate_confirmed_batch(
    assignments: list[dict[str, Any]],
) -> list[list[tuple[str, dict[str, Any]]]]:
    """Check a whole confirmed batch, and resolve it to tracks per assignment.

    The expected track sequence is built from the manifests on disk, never from
    the submitted file order. Planning from what the browser sent would let a
    payload naming half a collection, or naming it backwards, define its own
    correctness and pass.
    """
    submitted = [_flatten(assignment) for assignment in assignments]

    # Load each manifest once, remembering the order its slug first appears.
    # That order is what the expected sequence is built in.
    manifests: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for flat in submitted:
        for slug, _ in flat:
            if slug in manifests:
                continue
            manifest = library.get(slug)
            if not manifest:
                raise StalePush(f"The collection changed because {slug} no longer exists.")
            if manifest.get("stage") != "forged":
                raise StalePush(
                    "Forge is incomplete for a selected collection. "
                    "Finish preparation before sending it."
                )
            manifests[slug] = manifest
            order.append(slug)

    for assignment in assignments:
        for source in assignment["sources"]:
            if library.manifest_fingerprint(manifests[source["slug"]]) != source["manifest_fingerprint"]:
                raise StalePush(
                    "A selected collection changed after confirmation. "
                    "Select the collections in the Library again and send."
                )

    expected = [
        (slug, track)
        for slug in order
        for track in manifests[slug]["tracks"]
    ]
    # One comparison covers omission, duplication, a foreign name, a reordered
    # collection and interleaving, because `expected` is whole collections in
    # first-appearance order and nothing else can equal it.
    if [pair for flat in submitted for pair in flat] != [
        (slug, track["name"]) for slug, track in expected
    ]:
        raise StalePush(
            "The confirmed audio files no longer match the selected collections. "
            "Select them in the Library again and send."
        )

    groups = library.plan_groups([track for _, track in expected])
    if [len(group.tracks) for group in groups] != [len(flat) for flat in submitted]:
        raise StalePush(
            "The confirmed files no longer fill the capacity groups this selection plans. "
            "Select the collections in the Library again and send."
        )

    resolved: list[list[tuple[str, dict[str, Any]]]] = []
    cursor = 0
    for flat in submitted:
        resolved.append(expected[cursor:cursor + len(flat)])
        cursor += len(flat)
    return resolved


def confirmed_tracks(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Resolve one assignment's confirmed files, revalidating local truth.

    The worker runs long after the route validated the batch, and it holds only
    its own assignment, so it re-checks what it can see: every source is still
    forged, still fingerprints the same, and still has the files on disk.
    """
    resolved: list[tuple[str, dict[str, Any]]] = []
    for source in payload["sources"]:
        slug = source["slug"]
        manifest = library.get(slug)
        if not manifest:
            raise StalePush(f"The collection changed because {slug} no longer exists.")
        if manifest.get("stage") != "forged":
            raise StalePush(
                "Forge is incomplete for this collection. "
                "Finish preparation before sending it."
            )
        if library.manifest_fingerprint(manifest) != source["manifest_fingerprint"]:
            raise StalePush(
                "The local collection changed after confirmation. "
                "Select it in the Library again and send."
            )
        names = source["files"]
        by_name = {track["name"]: track for track in manifest["tracks"]}
        if len(names) != len(set(names)) or any(name not in by_name for name in names):
            raise StalePush("The confirmed audio files no longer match this collection.")
        for name in names:
            try:
                library.track_path(slug, name)
            except ValueError as exc:
                raise StalePush("A confirmed audio file is no longer available.") from exc
            resolved.append((slug, by_name[name]))
    return resolved


def _remote_identity(chapters: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(chapter.get("id"), chapter.get("title") or "") for chapter in chapters]


def push_confirmed(payload: dict[str, Any], progress: Progress = _noop) -> dict[str, Any]:
    with library.collection_lease():
        resolved = confirmed_tracks(payload)
        return _push_confirmed_tracks(payload, resolved, progress)


def _push_confirmed_tracks(
    payload: dict[str, Any],
    resolved: list[tuple[str, dict[str, Any]]],
    progress: Progress,
) -> dict[str, Any]:
    if not resolved:
        raise RuntimeError("Nothing selected to push.")

    total = sum(track.get("seconds", 0) for _, track in resolved)
    limit = config.usable_limit()
    if total > limit:
        raise RuntimeError(
            f"That selection is {audio.human_duration(total)}, over the "
            f"{audio.human_duration(limit)} usable audio limit. "
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
                raise StalePush(
                    "The Creative Tonie changed after confirmation. "
                    "Refresh targets in the Library and send again."
                )
            replace = payload["replace"]
            if not replace:
                present = float(state.get("secondsPresent") or 0)
                if total > max(0, limit - present):
                    raise StalePush("The Creative Tonie no longer has enough free space for this append.")

            if replace:
                progress("Clearing the Tonie")
                client.clear_tonie(payload["household_id"], payload["tonie_id"])

            uploaded = []
            tracks = [track for _, track in resolved]
            throttle = ProgressThrottle(progress)
            for position, (slug, track) in enumerate(resolved, start=1):
                path = library.track_path(slug, track["name"])
                label = track.get("title") or Path(track["name"]).stem
                message = f"Uploading {position}/{len(resolved)}: {label}"
                done_index = position - 1
                throttle.flush(message, upload_percent(tracks, done_index, 0))
                # The default arguments bind this iteration's values. A bare
                # closure over `message` would report every chunk of every file
                # under the last file's name once the loop moved on.
                file_id = client.upload_file(
                    path,
                    on_bytes=lambda at, m=message, d=done_index: throttle.report(
                        m, upload_percent(tracks, d, at)
                    ),
                )
                client.add_chapter(payload["household_id"], payload["tonie_id"], label, file_id)
                uploaded.append({"title": label, "file": file_id})

            # The bar measures audio uploaded, and by here it really is all up.
            # Sign-in and Clear report no percentage at all, because neither has
            # a size to measure and an invented figure is what this whole column
            # exists to avoid.
            progress("Confirming", upload_percent(tracks, len(tracks), 0))
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
