"""Music: from a Spotify playlist, album or song link to audio found on YouTube.

Spotify supplies names and lengths only, read from its public embed page, so no
account, key or developer app is involved. Each song is then found on YouTube
by search and matched by length, because the first search result is often a
live set, a cover or a lyric video of a different length.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from . import config, ingest, podcast

TOLERANCE_SECONDS = 30
SEARCH_RESULTS = 5
PLAYLIST_LIMIT = 100
SEARCH_TIMEOUT = 120

ARTIST_REFUSAL = "Paste a Spotify playlist, album or song link. Artist pages are not supported."
SPOTIFY_UNREADABLE = "Spotify changed its page, so TonieFi could not read that link."
NONE_FOUND = "None of the picked songs could be found on YouTube."
LIMIT_NOTICE = "Spotify lists only the first 100 songs of a playlist. Songs after that are not shown."
YT_DLP_MISSING = "yt-dlp is not installed in this container."

# Share links often carry a locale segment, as in /intl-de/playlist/<id>.
_PREFIX = r"^/(?:intl-[a-z-]+/)?(?:embed/)?"
_MUSIC = re.compile(_PREFIX + r"(playlist|album|track)/([A-Za-z0-9]+)/?$")
_ARTIST = re.compile(_PREFIX + r"artist/[A-Za-z0-9]+/?$")


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

def _artist_names(text: str) -> str:
    # Spotify joins artists with a comma and a no-break space.
    return " ".join((text or "").replace(" ", " ").split())


def _seconds(milliseconds: object) -> float | None:
    if isinstance(milliseconds, bool) or not isinstance(milliseconds, (int, float)) or milliseconds <= 0:
        return None
    return milliseconds / 1000


def _cover(entity: dict) -> str | None:
    images = (entity.get("visualIdentity") or {}).get("image")
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
    return [{"uri": item.get("uri"), "title": item.get("title"), "artists": item.get("subtitle") or "",
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

    owner = _track_artists(entity) if kind == "track" else _artist_names(entity.get("subtitle") or "")
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


def _closest(song: Song, entries: list[Any]) -> str | None:
    """The video whose length is closest to the song's, or None when none is near enough."""
    found = [entry for entry in entries
             if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]]
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
    notices is worse than a named gap. The query is one argv element that
    begins `ytsearch5:`, so a title that starts with `-` or holds quotes is
    never read as an option and never meets a shell.
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
        info = json.loads(proc.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    entries = info.get("entries") if isinstance(info, dict) else None
    return _closest(song, entries) if isinstance(entries, list) else None
