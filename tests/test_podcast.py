"""Podcast import: link shapes, feed discovery, feed reading and the download.

Nothing here touches the network. Every request goes through podcast._client,
which the `web` fixture points at an in-memory transport serving invented shows.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Callable

import httpx
import pytest

from app import ingest, podcast

FEED_URL = "https://feeds.example.test/moonbeam.xml"
APPLE_LINK = "https://podcasts.apple.com/us/podcast/moonbeam-bedtime-tales/id1234567890"

Answer = Callable[[httpx.Request], httpx.Response]


def reply(body: str | bytes = b"", status: int = 200, content_type: str = "text/html") -> Answer:
    content = body.encode() if isinstance(body, str) else body
    return lambda request: httpx.Response(status, content=content, headers={"content-type": content_type})


def reply_json(payload) -> Answer:
    return reply(json.dumps(payload), content_type="application/json")


@pytest.fixture
def web(monkeypatch):
    """Serve canned answers keyed by host and path; anything else is a 404."""
    routes: dict[str, Answer] = {}
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = routes.get(f"{request.url.host}{request.url.path}")
        return answer(request) if answer else httpx.Response(404, text="not here")

    def client(timeout: float = 45.0) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True,
                            headers={"User-Agent": ingest.USER_AGENT})

    monkeypatch.setattr(podcast, "_client", client)
    return SimpleNamespace(routes=routes, seen=seen)


def spotify_page(entity) -> str:
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return ('<html><body><script id="__NEXT_DATA__" type="application/json">'
            f"{json.dumps(data)}</script></body></html>")


# ------------------------------------------------------------- link shapes

@pytest.mark.parametrize("url", [
    "https://open.spotify.com/show/4aBcDeFg",
    "https://open.spotify.com/embed/show/4aBcDeFg",
    "https://open.spotify.com/show/4aBcDeFg?si=abc123",
    "https://open.spotify.com/intl-de/show/4aBcDeFg",
    "https://open.spotify.com/episode/7xYzWv",
    "https://open.spotify.com/embed/episode/7xYzWv",
    APPLE_LINK,
    "https://podcasts.apple.com/gb/podcast/moonbeam-bedtime-tales/id1234567890?i=1000123",
])
def test_podcast_link_shapes_are_recognized(url):
    assert podcast.is_podcast_url(url)


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/playlist?list=PL1",
    "https://open.spotify.com/track/1a2b3c",
    FEED_URL,
    "https://podcasts.apple.com/us/browse",
    "not a url",
])
def test_other_links_are_not_podcast_links(url):
    assert not podcast.is_podcast_url(url)


@pytest.mark.parametrize("kind", ["track", "album", "playlist", "artist"])
def test_spotify_music_links_are_music(kind):
    assert podcast.is_spotify_music_url(f"https://open.spotify.com/{kind}/1a2b3c")
    assert podcast.is_spotify_music_url(f"https://open.spotify.com/embed/{kind}/1a2b3c")
    assert not podcast.is_podcast_url(f"https://open.spotify.com/{kind}/1a2b3c")


def test_a_spotify_show_link_is_not_music():
    assert not podcast.is_spotify_music_url("https://open.spotify.com/show/4aBcDeFg")
    assert not podcast.is_spotify_music_url(FEED_URL)


def test_normalize_ignores_case_punctuation_and_ampersands():
    assert podcast.normalize("Moonbeam & Friends!") == podcast.normalize("moonbeam and friends")
    assert podcast.normalize("  Story-Time 2 ") == "storytime2"


# ------------------------------------------------------------ feed discovery

def test_resolving_a_music_link_is_refused_without_a_request(web):
    with pytest.raises(RuntimeError, match="not music"):
        podcast.resolve_feed("https://open.spotify.com/album/1a2b3c")
    assert web.seen == []


def test_a_raw_feed_url_is_its_own_feed(web):
    assert podcast.resolve_feed(FEED_URL) == podcast.Resolved(feed_url=FEED_URL)
    assert web.seen == []


def test_an_apple_link_resolves_through_the_lookup(web):
    web.routes["itunes.apple.com/lookup"] = reply_json({"results": [{"feedUrl": FEED_URL}]})

    resolved = podcast.resolve_feed(f"{APPLE_LINK}?i=1000123")

    assert resolved == podcast.Resolved(feed_url=FEED_URL, episode_hint=None)
    assert web.seen[0].url.params["id"] == "1234567890"
    assert web.seen[0].headers["user-agent"] == ingest.USER_AGENT


def test_an_apple_show_without_a_feed_says_so(web):
    web.routes["itunes.apple.com/lookup"] = reply_json(
        {"results": [{"collectionName": "Moonbeam Bedtime Tales"}]})

    with pytest.raises(RuntimeError, match="Apple lists no public feed"):
        podcast.resolve_feed(APPLE_LINK)


def test_a_spotify_show_matches_its_name_and_publisher(web):
    web.routes["open.spotify.com/embed/show/4aBcDeFg"] = reply(spotify_page(
        {"name": "Moonbeam Bedtime Tales", "subtitle": "Lantern Hill Audio"}))
    web.routes["itunes.apple.com/search"] = reply_json({"results": [
        {"collectionName": "Moonbeam Bedtime Tales", "artistName": "Copperpot Media",
         "feedUrl": "https://feeds.example.test/impostor.xml"},
        {"collectionName": "Moonbeam Bedtime Tales", "artistName": "Lantern Hill Audio",
         "feedUrl": FEED_URL},
    ]})

    resolved = podcast.resolve_feed("https://open.spotify.com/show/4aBcDeFg?si=abc123")

    assert resolved == podcast.Resolved(feed_url=FEED_URL, episode_hint=None)
    search = next(request for request in web.seen if request.url.path == "/search")
    assert search.url.params["term"] == "Moonbeam Bedtime Tales"
    assert search.url.params["media"] == "podcast"
    assert search.url.params["entity"] == "podcast"


def test_a_spotify_show_with_no_matching_feed_is_spotify_only(web):
    web.routes["open.spotify.com/embed/show/4aBcDeFg"] = reply(spotify_page(
        {"name": "Moonbeam Bedtime Tales", "subtitle": "Lantern Hill Audio"}))
    web.routes["itunes.apple.com/search"] = reply_json({"results": [
        {"collectionName": "Moonbeam Bedtime Tales", "artistName": "Copperpot Media",
         "feedUrl": "https://feeds.example.test/impostor.xml"},
    ]})

    with pytest.raises(RuntimeError, match="no public podcast feed"):
        podcast.resolve_feed("https://open.spotify.com/show/4aBcDeFg")


def test_a_spotify_episode_resolves_by_show_name_and_hints_its_title(web):
    web.routes["open.spotify.com/embed/episode/7xYzWv"] = reply(spotify_page(
        {"name": "The Owl Who Lost Her Hat", "subtitle": "Moonbeam Bedtime Tales"}))
    web.routes["itunes.apple.com/search"] = reply_json({"results": [
        {"collectionName": "Moonbeam Bedtime Tales!", "artistName": "Anyone At All",
         "feedUrl": FEED_URL},
    ]})

    resolved = podcast.resolve_feed("https://open.spotify.com/episode/7xYzWv")

    assert resolved == podcast.Resolved(feed_url=FEED_URL, episode_hint="The Owl Who Lost Her Hat")


def test_an_unreadable_spotify_page_points_at_the_apple_link(web):
    web.routes["open.spotify.com/embed/show/4aBcDeFg"] = reply("<html><body>Nothing here</body></html>")

    with pytest.raises(RuntimeError, match="Spotify changed its page"):
        podcast.resolve_feed("https://open.spotify.com/show/4aBcDeFg")
