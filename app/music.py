"""Music: from a Spotify playlist, album or song link to audio found on YouTube.

Spotify supplies names and lengths only, read from its public embed page, so no
account, key or developer app is involved. Each song is then found on YouTube
by search and matched by length, because the first search result is often a
live set, a cover or a lyric video of a different length.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from . import audio, config, ingest, library, podcast

TOLERANCE_SECONDS = 30
SEARCH_RESULTS = 5
PLAYLIST_LIMIT = 100
SEARCH_TIMEOUT = 120
DOWNLOAD_TIMEOUT = 30 * 60

ARTIST_REFUSAL = "Paste a Spotify playlist, album or song link. Artist pages are not supported."
SPOTIFY_UNREADABLE = "Spotify changed its page, so TonieFi could not read that link."
NONE_FOUND = "None of the picked songs could be found on YouTube."
LIMIT_NOTICE = "Spotify lists only the first 100 songs of a playlist. Songs after that are not shown."
YT_DLP_MISSING = "yt-dlp is not installed in this container."

# Share links often carry a locale segment, as in /intl-de/playlist/<id>.
_PREFIX = r"^/(?:intl-[a-z-]+/)?(?:embed/)?"
_MUSIC = re.compile(_PREFIX + r"(playlist|album|track)/([A-Za-z0-9]+)/?$")
_ARTIST = re.compile(_PREFIX + r"artist/[A-Za-z0-9]+/?$")
# A stored song is `<position>-<key>.mp3`; see _file_keys.
_STORED_NAME = re.compile(r"^\d{3,}-(.+)\.mp3$")
PARTIAL_SUFFIX = ".part"


@dataclass(frozen=True)
class Song:
    index: int  # From 1, after repeats are dropped.
    id: str  # The spotify:track:<id> uri. Picks travel as these, which a moved position cannot break.
    title: str
    artists: str
    duration: float | None  # Seconds. Spotify reports 0 for a song whose length it does not know.
    available: bool
    explicit: bool

    @property
    def label(self) -> str:
        return f"{self.title} - {self.artists}" if self.artists else self.title


@dataclass(frozen=True)
class Listing:
    kind: str  # "playlist", "album" or "track"
    title: str
    owner: str
    cover: str | None
    songs: list[Song]
    # The embed page stops at 100 songs, so a playlist that fills it has
    # probably been cut short. An album is never flagged: it can hold 100 songs
    # without losing any.
    reached_limit: bool = False


def _music_shape(url: str) -> tuple[str, str] | None:
    host, path = podcast._host_and_path(url)
    match = _MUSIC.match(path) if host == podcast._SPOTIFY_HOST else None
    return (match.group(1), match.group(2)) if match else None


def is_music_url(url: str) -> bool:
    """A Spotify playlist, album or song link, with or without a share query string."""
    return _music_shape(url) is not None


def is_artist_url(url: str) -> bool:
    """An artist page is not a list the operator chose, so it is refused by name."""
    host, path = podcast._host_and_path(url)
    return host == podcast._SPOTIFY_HOST and bool(_ARTIST.match(path))


# ----------------------------------------------------------------- listing

def _artist_names(text: object) -> str:
    # Spotify joins artists with a comma and a no-break space. Anything but
    # text is a page Spotify changed, and the names are optional.
    if not isinstance(text, str):
        return ""
    return " ".join(text.replace(" ", " ").split())


def _seconds(milliseconds: object) -> float | None:
    if isinstance(milliseconds, bool) or not isinstance(milliseconds, (int, float)) or milliseconds <= 0:
        return None
    return milliseconds / 1000


def _cover(entity: dict) -> str | None:
    identity = entity.get("visualIdentity")
    images = identity.get("image") if isinstance(identity, dict) else None
    widest: tuple[float, str] | None = None
    for image in images if isinstance(images, list) else []:
        if not isinstance(image, dict) or not isinstance(image.get("url"), str) or not image["url"]:
            continue
        width = image.get("maxWidth")
        width = width if isinstance(width, (int, float)) and not isinstance(width, bool) else 0
        if widest is None or width > widest[0]:
            widest = (width, image["url"])
    return widest[1] if widest else None


def _track_artists(entity: dict) -> str:
    artists = entity.get("artists")
    names = [artist.get("name") for artist in artists if isinstance(artist, dict)] if isinstance(artists, list) else []
    return ", ".join(name for name in names if isinstance(name, str) and name)


def _raw_songs(kind: str, entity: dict) -> list[dict[str, Any]]:
    """The entity's songs as (uri, title, artists, ...) dicts, repeats not yet dropped."""
    if kind == "track":
        return [{"uri": entity.get("uri"), "title": entity.get("name"), "artists": _track_artists(entity),
                 "duration": entity.get("duration"), "playable": entity.get("isPlayable"),
                 "explicit": entity.get("isExplicit")}]
    items = entity.get("trackList")
    return [{"uri": item.get("uri"), "title": item.get("title"), "artists": item.get("subtitle"),
             "duration": item.get("duration"), "playable": item.get("isPlayable"),
             "explicit": item.get("isExplicit")}
            for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def read(url: str) -> Listing:
    """Read a playlist, album or song from Spotify's public embed page."""
    if is_artist_url(url):
        raise RuntimeError(ARTIST_REFUSAL)
    shape = _music_shape(url)
    if shape is None:
        raise ValueError("That is not a Spotify playlist, album or song link.")
    kind, spotify_id = shape
    entity = podcast.spotify_entity(kind, spotify_id, unreadable=SPOTIFY_UNREADABLE)

    raw = _raw_songs(kind, entity)
    songs: list[Song] = []
    seen: set[str] = set()
    for found in raw:
        uri, title = found["uri"], found["title"]
        if not isinstance(uri, str) or not uri or not isinstance(title, str) or not title.strip():
            continue
        # The same song twice in a list would download twice and sit twice on the Tonie.
        if uri in seen:
            continue
        seen.add(uri)
        songs.append(Song(
            index=len(songs) + 1, id=uri, title=title.strip(), artists=_artist_names(found["artists"]),
            duration=_seconds(found["duration"]), available=bool(found["playable"]),
            explicit=bool(found["explicit"]),
        ))
    if not songs:
        raise RuntimeError(SPOTIFY_UNREADABLE)

    owner = _track_artists(entity) if kind == "track" else _artist_names(entity.get("subtitle"))
    return Listing(kind=kind, title=entity["name"], owner=owner, cover=_cover(entity), songs=songs,
                   reached_limit=kind == "playlist" and len(raw) >= PLAYLIST_LIMIT)


def preview(url: str) -> dict[str, Any]:
    """The picker's view of a Spotify list, in the playlist preview's shape.

    There is no `preselect`: every available song starts ticked, which is the
    picker's default. An explicit song is marked here, and only here, because
    the audience is a child's speaker and the operator should see it.
    """
    listing = read(url)
    shown: dict[str, Any] = {
        "title": listing.title,
        "entries": [
            {"index": song.index, "id": song.id,
             "title": f"{song.label} (explicit)" if song.explicit else song.label,
             "available": song.available}
            for song in listing.songs
        ],
        "kind": "music",
    }
    if listing.reached_limit:
        shown["notice"] = LIMIT_NOTICE
    return shown


# ------------------------------------------------------------------- match

def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class SearchFailed(Exception):
    """The YouTube search itself failed, so nobody knows whether a close match exists.

    The message says why, in yt-dlp's words where it gave any, so the skipped
    line never blames the song for a refused or stalled search.
    """


def _videos(info: object) -> list[dict[str, Any]]:
    """The search results that name a video, in rank order."""
    entries = info.get("entries") if isinstance(info, dict) else None
    return [entry for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]
            ] if isinstance(entries, list) else []


def _closest(song: Song, found: list[dict[str, Any]]) -> str | None:
    """The video whose length is closest to the song's, or None when none is near enough."""
    if song.duration is None:
        # With no length to compare, search rank is all there is to go on.
        return found[0]["id"] if found else None
    best: tuple[float, int, str] | None = None
    for rank, entry in enumerate(found):
        length = _number(entry.get("duration"))
        if length is None:
            continue
        # Comparing rank second means an equal distance goes to the higher result.
        candidate = (abs(length - song.duration), rank, entry["id"])
        if best is None or candidate < best:
            best = candidate
    if best is None or best[0] > TOLERANCE_SECONDS:
        return None
    return best[2]


def find_video(song: Song) -> str | None:
    """Search YouTube for a song and return the id of the video that matches its length.

    The search is flat, so nothing downloads. A song with no close match is
    skipped rather than given the first result: a wrong recording that nobody
    notices is worse than a named gap. A search that timed out, or exited
    with an error and listed no video, raises SearchFailed instead, since it
    says nothing about whether a match exists. The query is one argv element
    that begins `ytsearch5:`, so a title that starts with `-` or holds quotes
    is never read as an option and never meets a shell.
    """
    if not shutil.which("yt-dlp"):
        raise RuntimeError(YT_DLP_MISSING)
    query = " ".join(part for part in (song.artists, song.title) if part)
    cmd = [
        "yt-dlp", "--flat-playlist", "--dump-single-json", "--no-warnings",
        *ingest.player_client_args(),
        f"ytsearch{SEARCH_RESULTS}:{query}",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=SEARCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise SearchFailed("the search timed out.") from None
    try:
        found = _videos(json.loads(proc.stdout or "{}"))
    except json.JSONDecodeError:
        found = []
    # One refused entry makes yt-dlp exit non-zero after listing the rest, so
    # the exit code alone does not mean the search failed.
    if not found and proc.returncode != 0:
        raise SearchFailed(ingest.error_summary(proc.stderr) or "the search failed.")
    return _closest(song, found)


# ------------------------------------------------------------------ import

Progress = Callable[..., None]


def _noop(*_: Any, **__: Any) -> None:
    return None


def _download(video_id: str, target: Path) -> str | None:
    """Download one video's audio to `target`. Returns None on success, or why it failed.

    yt-dlp writes into a scratch directory under the work dir. The work dir
    and the library are separate mounts, so moving the finished mp3 into the
    stage is a copy that a killed worker can cut short. The copy therefore
    lands under `<name>.part` and is renamed onto its final name inside the
    stage, which is atomic, so a resumed import never takes half a song for a
    stored one. yt-dlp can exit non-zero after it has written good audio, so
    the mp3 is the verdict, not the exit code.
    """
    config.ensure_dirs()
    with tempfile.TemporaryDirectory(dir=config.WORK_DIR) as tmpdir:
        scratch = Path(tmpdir)
        cmd = [
            "yt-dlp", "--no-playlist",
            "-x", "--audio-format", "mp3", "--audio-quality", "0",
            "--no-progress", "--no-warnings",
            *ingest.player_client_args(),
            "-o", str(scratch / "song.%(ext)s"),
            # The id sits inside a URL, so one that begins with `-` is never read as an option.
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                                  timeout=DOWNLOAD_TIMEOUT)
        except subprocess.TimeoutExpired:
            return f"the download took longer than {DOWNLOAD_TIMEOUT // 60} minutes."
        produced = scratch / "song.mp3"
        if produced.is_file():
            partial = target.with_name(target.name + PARTIAL_SUFFIX)
            shutil.move(produced, partial)
            os.replace(partial, target)
            return None
        return ingest.error_summary(proc.stderr) or "yt-dlp produced no audio."


def _file_keys(songs: list[Song]) -> list[str]:
    """Each song's file name without its position prefix: the slug of its label.

    The key, not the position, is what a retried import recognizes a stored
    song by, since a song added to the list shifts every position after it.
    Two different songs can share a label, such as two recordings of one
    nursery rhyme by the same choir. Every song in such a group also carries
    the slug of its Spotify id, so the two never collapse into one file and
    each keeps its own name on a retry.
    """
    slugs = [audio.slugify(song.label) for song in songs]
    counts = Counter(slugs)
    return [slug if counts[slug] == 1 else f"{slug}-{audio.slugify(song.id.rsplit(':', 1)[-1])}"
            for slug, song in zip(slugs, songs)]


def _stored_songs(dest: Path) -> dict[str, Path]:
    """The songs an earlier run stored in this stage, by key. A `.part` file is never one."""
    found: dict[str, Path] = {}
    for path in sorted(dest.glob("*.mp3")):
        match = _STORED_NAME.match(path.name)
        if match and path.is_file():
            found.setdefault(match.group(1), path)
    return found


def import_music(
    url: str,
    *,
    stage_id: str,
    track_ids: list[str] | None = None,
    progress: Progress = _noop,
) -> dict[str, Any]:
    """Find the picked songs of a Spotify list on YouTube and download them into one collection stage.

    Picks are track uris, since a song added to a playlist shifts every
    position the preview showed. The list is read again here, so picked songs
    that have since left it are counted in `skipped`, and a song with no close
    match or a failed download is skipped and named rather than failing the
    rest. No pick means every song Spotify marks playable.
    """
    published = library.find_published_stage(stage_id)
    if published:
        return published
    # Checked before Spotify is read, so a container without yt-dlp says so
    # instead of reading a page it can do nothing with.
    if not shutil.which("yt-dlp"):
        raise RuntimeError(YT_DLP_MISSING)
    listing = read(url)
    skipped: list[str] = []
    if track_ids is None:
        picked = [song for song in listing.songs if song.available]
    else:
        wanted = set(track_ids)
        picked = [song for song in listing.songs if song.id in wanted]
        missing = len(wanted - {song.id for song in picked})
        if missing:
            skipped.append(f"{missing} picked song is no longer in the list." if missing == 1
                           else f"{missing} picked songs are no longer in the list.")
    if not picked:
        raise RuntimeError(NONE_FOUND)

    with library.collection_lease():
        stage = library.begin_collection_stage(stage_id, title=listing.title, source="music", extra={
            "url": url,
            # The collection page's Uploader row reads this key.
            "uploader": listing.owner,
            "skipped": [],
        })
    dest = stage.path

    # The searches and downloads run without the lease. It is the one lock
    # behind the Library listing, the collection page and the other worker, and
    # a long list keeps the network busy for many minutes. The stage needs no
    # lock meanwhile: it is a hidden directory that only this job's identity
    # creates, resumes or discards, nothing reads its audio before the rescan
    # below, and every manifest write happens under the lease at the end.
    earlier = _stored_songs(dest)
    stored: dict[str, Song] = {}
    total = len(picked)
    for position, (song, key) in enumerate(zip(picked, _file_keys(picked)), start=1):
        name = f"{position:03d}-{key}.mp3"
        target = dest / name
        # A file an earlier run stored is kept, even when a song added to or
        # removed from the list since then has moved it to a new position. A
        # song that run skipped left no file, so it is searched again.
        if not target.is_file() and key in earlier:
            os.replace(earlier[key], target)
        if not target.is_file():
            progress(f"Finding {position}/{total}: {song.title}", audio.step_percent(position - 1, total))
            try:
                video_id = find_video(song)
            except SearchFailed as failed:
                skipped.append(f"{song.label}: {failed}")
                continue
            if video_id is None:
                skipped.append(f"{song.label}: no close match on YouTube.")
                continue
            failure = _download(video_id, target)
            if failure:
                skipped.append(f"{song.label}: {failure}")
                continue
        stored[name] = song
    if not stored:
        raise RuntimeError(NONE_FOUND)
    if listing.cover and not (dest / "cover.jpg").is_file():
        try:
            with podcast._client(timeout=60.0) as client:
                ingest._stream_download(client, listing.cover, dest / "cover.jpg")
        except httpx.HTTPError:
            pass

    with library.collection_lease():
        # The rescan publishes every audio file in the stage. A song an earlier
        # run stored that this run no longer picks, and the `.part` file of a
        # worker that died mid-copy, would otherwise reach the Tonie.
        for path in dest.iterdir():
            leftover = path.name.endswith(PARTIAL_SUFFIX) or (
                path.suffix.lower() in audio.AUDIO_EXTENSIONS and path.name not in stored)
            if leftover and path.is_file():
                path.unlink()
        library.rescan_collection_stage(stage_id)

        # A resumed stage keeps the extras it was created with, so this run's
        # skips are written once the downloads are done.
        def describe(manifest: dict[str, Any]) -> None:
            manifest["skipped"] = skipped
            for track in manifest.get("tracks", []):
                song = stored.get(track.get("name"))
                if song is not None:
                    track["title"] = song.label
        library.mutate_at_path(dest, describe)

        progress("Probing durations")
        library.collection_stage(stage_id, refresh=True)
        return library.complete_collection_stage(stage_id)
