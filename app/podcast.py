"""Podcasts: from a show link to the publisher's own audio files.

Audio only ever comes from a public feed's enclosures. A Spotify link is read
for nothing but its show name, which Apple's directory turns into a feed URL.
Spotify-only shows have no feed, so they are refused rather than worked around.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from . import audio, ingest, library

ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
ITUNES_SEARCH = "https://itunes.apple.com/search"
SPOTIFY_EMBED = "https://open.spotify.com/embed"

SPOTIFY_UNREADABLE = (
    "Spotify changed its page, so TonieFi could not read that link. "
    "Try the show's Apple Podcasts link instead."
)
NO_PUBLIC_FEED = (
    "This show has no public podcast feed. "
    "It may be a Spotify-only show, which TonieFi cannot import."
)
APPLE_NO_FEED = "Apple lists no public feed for this show."

NO_AUDIO = "That feed has no audio episodes."
MAX_FEED_BYTES = 20 * 1024 * 1024
_ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"

NONE_DOWNLOADED = "None of the picked episodes could be downloaded."
LICENSE = "Published by the podcast's maker in a public feed"

Progress = Callable[..., None]

_SPOTIFY_HOST = "open.spotify.com"
_APPLE_HOST = "podcasts.apple.com"
# Share links often carry a locale segment, as in /intl-de/show/<id>.
_SPOTIFY_PODCAST = re.compile(r"^/(?:intl-[a-z-]+/)?(?:embed/)?(show|episode)/([A-Za-z0-9]+)/?$")
_SPOTIFY_MUSIC = re.compile(r"^/(?:intl-[a-z-]+/)?(?:embed/)?(?:track|album|playlist|artist)/")
_APPLE_PODCAST = re.compile(r"^/[a-z]{2}/podcast/(?:[^/]+/)?id(\d+)/?$")
_NEXT_DATA = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)


@dataclass(frozen=True)
class Resolved:
    feed_url: str
    # A Spotify episode link names one episode, which the preview pre-ticks
    # and an unpicked import brings alone.
    episode_hint: str | None = None


@dataclass(frozen=True)
class Episode:
    # Position in the oldest-first list, from 1. The picker shows these;
    # picks travel as guids, which a new episode cannot shift.
    index: int
    guid: str
    title: str
    url: str
    published: float | None
    duration: int | None


@dataclass(frozen=True)
class Feed:
    title: str
    author: str
    cover: str | None
    episodes: list[Episode]


def _client(timeout: float = 45.0) -> httpx.Client:
    """Every outbound podcast request goes through here, so tests can swap the transport."""
    return httpx.Client(timeout=timeout, follow_redirects=True,
                        headers={"User-Agent": ingest.USER_AGENT})


def _noop(*_: Any, **__: Any) -> None:
    return None


def _host_and_path(url: str) -> tuple[str, str]:
    try:
        parsed = urlparse(url)
        return (parsed.hostname or "").lower(), parsed.path
    except ValueError:
        return "", ""


def _spotify_podcast(url: str) -> tuple[str, str] | None:
    host, path = _host_and_path(url)
    match = _SPOTIFY_PODCAST.match(path) if host == _SPOTIFY_HOST else None
    return (match.group(1), match.group(2)) if match else None


def _apple_id(url: str) -> str | None:
    host, path = _host_and_path(url)
    match = _APPLE_PODCAST.match(path) if host == _APPLE_HOST else None
    return match.group(1) if match else None


def is_podcast_url(url: str) -> bool:
    """A Spotify show or episode link, or an Apple Podcasts show link.

    A raw feed has no fixed shape, so it is never recognized here; it is found
    by reading it.
    """
    return _spotify_podcast(url) is not None or _apple_id(url) is not None


def is_spotify_music_url(url: str) -> bool:
    host, path = _host_and_path(url)
    return host == _SPOTIFY_HOST and bool(_SPOTIFY_MUSIC.match(path))


def normalize(text: str) -> str:
    """Casefold, read & as "and", and keep only letters and digits."""
    return "".join(ch for ch in (text or "").casefold().replace("&", "and") if ch.isalnum())


def _fetch(url: str, *, what: str, params: dict[str, str] | None = None) -> httpx.Response:
    try:
        with _client() as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach {what}: {exc}") from exc


def _results(resp: httpx.Response) -> list[dict]:
    try:
        results = resp.json().get("results") or []
    except (ValueError, AttributeError):
        return []
    return [result for result in results if isinstance(result, dict)]


def _apple_feed(apple_id: str) -> str:
    results = _results(_fetch(ITUNES_LOOKUP, what="Apple Podcasts", params={"id": apple_id}))
    feed_url = (results[0].get("feedUrl") if results else "") or ""
    if not feed_url:
        raise RuntimeError(APPLE_NO_FEED)
    return feed_url


def _spotify_entity(kind: str, spotify_id: str) -> dict:
    resp = _fetch(f"{SPOTIFY_EMBED}/{kind}/{spotify_id}", what="Spotify")
    match = _NEXT_DATA.search(resp.text)
    try:
        entity = json.loads(match.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (AttributeError, KeyError, TypeError, ValueError):
        raise RuntimeError(SPOTIFY_UNREADABLE) from None
    if not isinstance(entity, dict) or not entity.get("name") or not entity.get("subtitle"):
        raise RuntimeError(SPOTIFY_UNREADABLE)
    return entity


def _search_feed(show: str, publisher: str | None) -> str:
    """Find the show in Apple's directory by exact name, and publisher when known."""
    results = _results(_fetch(ITUNES_SEARCH, what="Apple Podcasts", params={
        "media": "podcast", "entity": "podcast", "limit": "10", "term": show,
    }))
    wanted_show, wanted_publisher = normalize(show), normalize(publisher or "")
    for result in results:
        if normalize(result.get("collectionName", "")) != wanted_show:
            continue
        if wanted_publisher and normalize(result.get("artistName", "")) != wanted_publisher:
            continue
        if result.get("feedUrl"):
            return result["feedUrl"]
    raise RuntimeError(NO_PUBLIC_FEED)


def resolve_feed(url: str) -> Resolved:
    """Turn a pasted link into the feed URL that holds its audio."""
    apple_id = _apple_id(url)
    if apple_id:
        return Resolved(_apple_feed(apple_id))
    spotify = _spotify_podcast(url)
    if spotify:
        kind, spotify_id = spotify
        entity = _spotify_entity(kind, spotify_id)
        if kind == "show":
            return Resolved(_search_feed(entity["name"], entity["subtitle"]))
        # An episode page names its show but not the publisher.
        return Resolved(_search_feed(entity["subtitle"], None), episode_hint=entity["name"])
    return Resolved(url)


# ------------------------------------------------------------------ feeds

def _feed_error(reason: object) -> RuntimeError:
    return RuntimeError(f"Could not read that podcast feed: {reason}")


def _download_feed(feed_url: str) -> bytes:
    body = bytearray()
    try:
        with _client() as client, client.stream("GET", feed_url) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes(chunk_size=1 << 16):
                body.extend(chunk)
                if len(body) > MAX_FEED_BYTES:
                    raise _feed_error("it is larger than 20 MB.")
    except httpx.HTTPError as exc:
        raise _feed_error(exc) from exc
    return bytes(body)


def _is_audio(kind: str, url: str) -> bool:
    if kind.lower().startswith("audio/"):
        return True
    return PurePosixPath(urlparse(url).path).suffix.lower() in audio.AUDIO_EXTENSIONS


def _published(text: str) -> float | None:
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _duration(text: str) -> int | None:
    """itunes:duration as SS, MM:SS or HH:MM:SS. Anything else is unknown."""
    parts = (text or "").strip().split(":")
    if len(parts) > 3:
        return None
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    seconds = 0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def read_feed(feed_url: str) -> Feed:
    """Fetch and parse a podcast feed, episodes oldest first.

    Stories are meant to be heard from the start and most feeds list newest
    first, so dated episodes are sorted by date and undated ones follow in
    their feed order. Python's bundled Expat limits entity expansion, so a
    hostile feed cannot blow up memory while it is parsed.
    """
    try:
        root = ET.fromstring(_download_feed(feed_url))
    except ET.ParseError as exc:
        raise _feed_error(exc) from exc
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise _feed_error("it is not an RSS feed.")

    dated: list[tuple[float, int, dict[str, Any]]] = []
    undated: list[dict[str, Any]] = []
    for position, element in enumerate(channel.findall("item")):
        enclosure = element.find("enclosure")
        if enclosure is None:
            continue
        url = (enclosure.get("url") or "").strip()
        if not url or not _is_audio(enclosure.get("type") or "", url):
            continue
        found = {
            "guid": (element.findtext("guid") or "").strip() or url,
            "title": (element.findtext("title") or "").strip(),
            "url": url,
            "published": _published((element.findtext("pubDate") or "").strip()),
            "duration": _duration(element.findtext(f"{_ITUNES}duration") or ""),
        }
        if found["published"] is None:
            undated.append(found)
        else:
            dated.append((found["published"], position, found))
    ordered = [found for _, _, found in sorted(dated, key=lambda entry: entry[:2])] + undated
    if not ordered:
        raise RuntimeError(NO_AUDIO)

    image = channel.find(f"{_ITUNES}image")
    cover = (image.get("href") if image is not None else None) or (channel.findtext("image/url") or "").strip()
    return Feed(
        title=(channel.findtext("title") or "").strip() or "Untitled podcast",
        author=(channel.findtext(f"{_ITUNES}author") or "").strip(),
        cover=cover or None,
        episodes=[
            Episode(index=index, guid=found["guid"], title=found["title"] or f"Episode {index}",
                    url=found["url"], published=found["published"], duration=found["duration"])
            for index, found in enumerate(ordered, start=1)
        ],
    )


def _unpicked(feed: Feed, hint: str | None) -> list[Episode]:
    """What a link means before anyone picks: every episode for a show, or the
    one a Spotify episode link named. A title that no longer matches falls back
    to every episode.
    """
    if hint:
        wanted = normalize(hint)
        matched = [episode for episode in feed.episodes if normalize(episode.title) == wanted]
        if matched:
            return matched[:1]
    return list(feed.episodes)


def preview(url: str) -> dict[str, Any]:
    """The picker's view of a podcast, in the playlist preview's shape.

    `preselect` is what the link means with no pick, so the picker starts
    from what an unpicked import would bring.
    """
    resolved = resolve_feed(url)
    feed = read_feed(resolved.feed_url)
    preselect = [episode.index for episode in _unpicked(feed, resolved.episode_hint)]
    return {
        "title": feed.title,
        "entries": [
            {"index": episode.index, "id": episode.guid, "title": episode.title, "available": True}
            for episode in feed.episodes
        ],
        "kind": "podcast",
        "preselect": preselect,
    }


# ------------------------------------------------------------------ import

def _extension(url: str) -> str:
    suffix = PurePosixPath(urlparse(url).path).suffix.lower()
    return suffix if suffix in audio.AUDIO_EXTENSIONS else ".mp3"


def import_feed(
    url: str,
    *,
    stage_id: str,
    episode_ids: list[str] | None = None,
    progress: Progress = _noop,
) -> dict[str, Any]:
    """Download the picked episodes of a podcast into one collection stage.

    Picks are episode guids, since a new episode shifts every position the
    preview showed. The feed is read again here, so picked episodes that have
    since left it are counted in `skipped`, and one whose download fails is
    skipped and named, rather than failing the rest. Picked episodes keep the
    feed's oldest-first order. No pick means what the link means on its own.
    """
    published = library.find_published_stage(stage_id)
    if published:
        return published
    resolved = resolve_feed(url)
    feed = read_feed(resolved.feed_url)
    skipped: list[str] = []
    if episode_ids is None:
        picked = _unpicked(feed, resolved.episode_hint)
    else:
        wanted = set(episode_ids)
        picked = [episode for episode in feed.episodes if episode.guid in wanted]
        missing = len(wanted - {episode.guid for episode in picked})
        if missing:
            skipped.append(f"{missing} picked episode is no longer in the feed." if missing == 1
                           else f"{missing} picked episodes are no longer in the feed.")
    if not picked:
        raise RuntimeError(NONE_DOWNLOADED)

    with library.collection_lease():
        stage = library.begin_collection_stage(stage_id, title=feed.title, source="podcast", extra={
            "url": url,
            "feed_url": resolved.feed_url,
            "author": feed.author,
            # The collection page's Uploader row reads this key.
            "uploader": feed.author,
            "license": LICENSE,
            "skipped": [],
        })
        dest = stage.path

        stored: list[tuple[str, str]] = []
        total = len(picked)
        with _client(timeout=300.0) as client:
            for position, episode in enumerate(picked, start=1):
                name = f"{position:03d}-{audio.slugify(episode.title)}{_extension(episode.url)}"
                if not (dest / name).is_file():
                    progress(
                        f"Downloading {position}/{total}: {episode.title}",
                        audio.step_percent(position - 1, total),
                    )
                    try:
                        ingest._stream_download(client, episode.url, dest / name)
                    except httpx.HTTPError as exc:
                        skipped.append(f"{episode.title}: {exc}")
                        continue
                stored.append((name, episode.title))
            if not stored:
                raise RuntimeError(NONE_DOWNLOADED)
            if feed.cover and not (dest / "cover.jpg").is_file():
                try:
                    ingest._stream_download(client, feed.cover, dest / "cover.jpg")
                except httpx.HTTPError:
                    pass

        library.rescan_collection_stage(stage_id)
        for name, title in stored:
            library.rename_track_at_path(dest, name, title)
        # A resumed stage keeps the extras it was created with, so this run's
        # skips are written once the downloads are done.
        library.mutate_at_path(dest, lambda manifest: manifest.update(skipped=skipped))

        progress("Probing durations")
        library.collection_stage(stage_id, refresh=True)
        return library.complete_collection_stage(stage_id)
