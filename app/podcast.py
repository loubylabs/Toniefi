"""Podcasts: from a show link to the publisher's own audio files.

Audio only ever comes from a public feed's enclosures. A Spotify link is read
for nothing but its show name, which Apple's directory turns into a feed URL.
Spotify-only shows have no feed, so they are refused rather than worked around.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from . import ingest

ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
ITUNES_SEARCH = "https://itunes.apple.com/search"
SPOTIFY_EMBED = "https://open.spotify.com/embed"

MUSIC_REFUSAL = "TonieFi imports podcasts from Spotify, not music."
SPOTIFY_UNREADABLE = (
    "Spotify changed its page, so TonieFi could not read that link. "
    "Try the show's Apple Podcasts link instead."
)
NO_PUBLIC_FEED = (
    "This show has no public podcast feed. "
    "It may be a Spotify-only show, which TonieFi cannot import."
)
APPLE_NO_FEED = "Apple lists no public feed for this show."

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
    # A Spotify episode link names one episode, which the preview pre-ticks.
    episode_hint: str | None = None


def _client(timeout: float = 45.0) -> httpx.Client:
    """Every outbound podcast request goes through here, so tests can swap the transport."""
    return httpx.Client(timeout=timeout, follow_redirects=True,
                        headers={"User-Agent": ingest.USER_AGENT})


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
    if is_spotify_music_url(url):
        raise RuntimeError(MUSIC_REFUSAL)
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
