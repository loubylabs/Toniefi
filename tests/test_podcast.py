"""Podcast import: link shapes, feed discovery, feed reading and the download.

Nothing here touches the network. Every request goes through podcast._client,
which the `web` fixture points at an in-memory transport serving invented shows.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from app import audio, config, db, ingest, library, main, podcast, prepare

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


# -------------------------------------------------------------- feed reading

ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
SHOW_DETAILS = ("<itunes:author>Lantern Hill Audio</itunes:author>"
                '<itunes:image href="https://cdn.example.test/moonbeam.jpg"/>')


def feed_xml(items: str, *, channel: str = SHOW_DETAILS) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<rss version="2.0" xmlns:itunes="{ITUNES}"><channel>'
            f"<title>Moonbeam Bedtime Tales</title>{channel}{items}</channel></rss>")


def item(title: str | None, *, date: str | None = None, url: str | None = None,
         kind: str = "audio/mpeg", duration: str | None = None, guid: str | None = None) -> str:
    parts = []
    if title is not None:
        parts.append(f"<title>{title}</title>")
    if guid:
        parts.append(f"<guid>{guid}</guid>")
    if date:
        parts.append(f"<pubDate>{date}</pubDate>")
    if duration is not None:
        parts.append(f"<itunes:duration>{duration}</itunes:duration>")
    if url:
        parts.append(f'<enclosure url="{url}" type="{kind}" length="1"/>')
    return f"<item>{''.join(parts)}</item>"


# Newest first, the way most feeds list them, with one undated bonus.
STORIES = "".join([
    item("The Sleepy Lighthouse", date="Wed, 03 Jan 2024 06:00:00 +0000",
         url="https://cdn.example.test/lighthouse.mp3", guid="ep-3"),
    item("The Owl Who Lost Her Hat", date="Mon, 01 Jan 2024 06:00:00 +0000",
         url="https://cdn.example.test/owl.mp3", guid="ep-1"),
    item("A Bonus Lullaby", url="https://cdn.example.test/lullaby.mp3", guid="ep-bonus"),
    item("Two Snails Race", date="Tue, 02 Jan 2024 06:00:00 +0000",
         url="https://cdn.example.test/snails.mp3", guid="ep-2"),
])


def serve_feed(web, body: str, path: str = "/moonbeam.xml") -> None:
    web.routes[f"feeds.example.test{path}"] = reply(body, content_type="application/rss+xml")


def test_episodes_are_listed_oldest_first_with_undated_ones_last(web):
    serve_feed(web, feed_xml(STORIES))

    feed = podcast.read_feed(FEED_URL)

    assert [episode.title for episode in feed.episodes] == [
        "The Owl Who Lost Her Hat", "Two Snails Race", "The Sleepy Lighthouse", "A Bonus Lullaby",
    ]
    assert [episode.index for episode in feed.episodes] == [1, 2, 3, 4]
    assert [episode.guid for episode in feed.episodes] == ["ep-1", "ep-2", "ep-3", "ep-bonus"]
    assert feed.episodes[0].url == "https://cdn.example.test/owl.mp3"
    assert feed.episodes[3].published is None


def test_undated_episodes_keep_their_feed_order(web):
    serve_feed(web, feed_xml(
        item("Second Bonus", url="https://cdn.example.test/b.mp3")
        + item("Dated", date="Mon, 01 Jan 2024 06:00:00 +0000", url="https://cdn.example.test/d.mp3")
        + item("Third Bonus", url="https://cdn.example.test/c.mp3")))

    titles = [episode.title for episode in podcast.read_feed(FEED_URL).episodes]

    assert titles == ["Dated", "Second Bonus", "Third Bonus"]


def test_the_channel_names_the_show_its_author_and_its_cover(web):
    serve_feed(web, feed_xml(STORIES))

    feed = podcast.read_feed(FEED_URL)

    assert feed.title == "Moonbeam Bedtime Tales"
    assert feed.author == "Lantern Hill Audio"
    assert feed.cover == "https://cdn.example.test/moonbeam.jpg"


def test_the_plain_rss_image_is_the_fallback_cover(web):
    serve_feed(web, feed_xml(STORIES, channel="<image><url>https://cdn.example.test/plain.jpg</url></image>"))

    feed = podcast.read_feed(FEED_URL)

    assert feed.cover == "https://cdn.example.test/plain.jpg"
    assert feed.author == ""


def test_a_feed_without_artwork_has_no_cover(web):
    serve_feed(web, feed_xml(STORIES, channel=""))

    assert podcast.read_feed(FEED_URL).cover is None


def test_items_without_usable_audio_are_dropped(web):
    serve_feed(web, feed_xml(
        item("Video Special", url="https://cdn.example.test/special.mp4", kind="video/mp4")
        + item("Show Notes Only")
        + item("Porch Songs", url="https://cdn.example.test/porch.m4a?source=rss", kind="")
        + item("Web Page", url="https://cdn.example.test/page.html", kind="text/html")))

    episodes = podcast.read_feed(FEED_URL).episodes

    assert [episode.title for episode in episodes] == ["Porch Songs"]
    assert episodes[0].url == "https://cdn.example.test/porch.m4a?source=rss"


@pytest.mark.parametrize(("text", "seconds"), [
    ("95", 95), ("12:05", 725), ("1:02:03", 3723), ("", None), ("soon", None), (None, None),
])
def test_itunes_duration_forms(web, text, seconds):
    serve_feed(web, feed_xml(item("Timed", url="https://cdn.example.test/t.mp3", duration=text)))

    assert podcast.read_feed(FEED_URL).episodes[0].duration == seconds


def test_an_item_without_guid_or_title_gets_its_url_and_number(web):
    serve_feed(web, feed_xml(item(None, url="https://cdn.example.test/mystery.mp3")))

    episode = podcast.read_feed(FEED_URL).episodes[0]

    assert episode.title == "Episode 1"
    assert episode.guid == "https://cdn.example.test/mystery.mp3"


def test_a_feed_with_no_audio_says_so(web):
    serve_feed(web, feed_xml(item("Show Notes Only")))

    with pytest.raises(RuntimeError, match="That feed has no audio episodes."):
        podcast.read_feed(FEED_URL)


def test_an_oversized_feed_is_refused(web, monkeypatch):
    monkeypatch.setattr(podcast, "MAX_FEED_BYTES", 100)
    serve_feed(web, feed_xml(STORIES))

    with pytest.raises(RuntimeError, match="larger than 20 MB"):
        podcast.read_feed(FEED_URL)


@pytest.mark.parametrize("body", ["<html><body>A web page</body></html>", "<rss><channel>"])
def test_a_page_that_is_not_rss_is_not_a_feed(web, body):
    serve_feed(web, body)

    with pytest.raises(RuntimeError, match="Could not read that podcast feed"):
        podcast.read_feed(FEED_URL)


def test_an_unreachable_feed_is_not_a_feed(web):
    with pytest.raises(RuntimeError, match="Could not read that podcast feed"):
        podcast.read_feed(FEED_URL)


def test_a_moved_feed_is_followed(web):
    serve_feed(web, feed_xml(STORIES))
    web.routes["feeds.example.test/old.xml"] = lambda request: httpx.Response(
        301, headers={"location": FEED_URL})

    assert len(podcast.read_feed("https://feeds.example.test/old.xml").episodes) == 4


# ------------------------------------------------------------------ preview

def test_a_show_preview_ticks_every_episode(web):
    serve_feed(web, feed_xml(STORIES))

    assert podcast.preview(FEED_URL) == {
        "title": "Moonbeam Bedtime Tales",
        "entries": [
            {"index": 1, "id": "ep-1", "title": "The Owl Who Lost Her Hat", "available": True},
            {"index": 2, "id": "ep-2", "title": "Two Snails Race", "available": True},
            {"index": 3, "id": "ep-3", "title": "The Sleepy Lighthouse", "available": True},
            {"index": 4, "id": "ep-bonus", "title": "A Bonus Lullaby", "available": True},
        ],
        "kind": "podcast",
        "preselect": [1, 2, 3, 4],
    }


def test_an_episode_link_ticks_only_its_own_episode(web, monkeypatch):
    serve_feed(web, feed_xml(STORIES))
    monkeypatch.setattr(podcast, "resolve_feed",
                        lambda url: podcast.Resolved(FEED_URL, episode_hint="the owl who lost her HAT!"))

    assert podcast.preview("https://open.spotify.com/episode/7xYzWv")["preselect"] == [1]


def test_an_episode_link_whose_title_is_not_found_ticks_everything(web, monkeypatch):
    serve_feed(web, feed_xml(STORIES))
    monkeypatch.setattr(podcast, "resolve_feed",
                        lambda url: podcast.Resolved(FEED_URL, episode_hint="A Story Not In The Feed"))

    assert podcast.preview("https://open.spotify.com/episode/7xYzWv")["preselect"] == [1, 2, 3, 4]


# ------------------------------------------------------------------- import

@pytest.fixture
def isolated_library(monkeypatch, tmp_path):
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "portal.db")
    config.ensure_dirs()
    db.init()
    monkeypatch.setattr(audio, "duration_seconds", lambda path: 10)
    yield
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


def serve_stories(web) -> None:
    serve_feed(web, feed_xml(STORIES))
    for name in ("owl", "snails", "lighthouse", "lullaby"):
        web.routes[f"cdn.example.test/{name}.mp3"] = reply(f"{name} audio", content_type="audio/mpeg")
    web.routes["cdn.example.test/moonbeam.jpg"] = reply(b"cover bytes", content_type="image/jpeg")


def names(result) -> list[str]:
    return [track["name"] for track in result["tracks"]]


def titles(result) -> list[str]:
    return [track["title"] for track in result["tracks"]]


def test_picked_episodes_are_stored_oldest_first_with_their_titles(isolated_library, web):
    serve_stories(web)

    result = podcast.import_feed(FEED_URL, stage_id="podcast-pick", playlist_items=[3, 1])

    assert names(result) == ["001-the-owl-who-lost-her-hat.mp3", "002-the-sleepy-lighthouse.mp3"]
    assert titles(result) == ["The Owl Who Lost Her Hat", "The Sleepy Lighthouse"]
    assert (Path(result["path"]) / "001-the-owl-who-lost-her-hat.mp3").read_bytes() == b"owl audio"
    assert result["title"] == "Moonbeam Bedtime Tales"
    assert result["source"] == "podcast"
    assert result["url"] == FEED_URL
    assert result["feed_url"] == FEED_URL
    assert result["author"] == "Lantern Hill Audio"
    assert result["uploader"] == "Lantern Hill Audio"
    assert result["license"] == "Published by the podcast's maker in a public feed"
    assert result["skipped"] == []


def test_the_channel_cover_is_saved(isolated_library, web):
    serve_stories(web)

    result = podcast.import_feed(FEED_URL, stage_id="podcast-cover", playlist_items=[1])

    assert result["cover"] == "cover.jpg"
    assert (Path(result["path"]) / "cover.jpg").read_bytes() == b"cover bytes"


def test_a_cover_that_will_not_download_is_ignored(isolated_library, web):
    serve_stories(web)
    del web.routes["cdn.example.test/moonbeam.jpg"]

    result = podcast.import_feed(FEED_URL, stage_id="podcast-no-cover", playlist_items=[1])

    assert names(result) == ["001-the-owl-who-lost-her-hat.mp3"]
    assert "cover" not in result


def test_no_pick_imports_every_episode(isolated_library, web):
    serve_stories(web)

    result = podcast.import_feed(FEED_URL, stage_id="podcast-all")

    assert titles(result) == [
        "The Owl Who Lost Her Hat", "Two Snails Race", "The Sleepy Lighthouse", "A Bonus Lullaby",
    ]


def test_an_empty_pick_is_refused(isolated_library, web):
    serve_stories(web)

    with pytest.raises(ValueError, match="at least one"):
        podcast.import_feed(FEED_URL, stage_id="podcast-empty", playlist_items=[])


def test_an_episode_that_fails_to_download_is_skipped_and_named(isolated_library, web):
    serve_stories(web)
    web.routes["cdn.example.test/snails.mp3"] = reply("gone", status=500)

    result = podcast.import_feed(FEED_URL, stage_id="podcast-one-fails", playlist_items=[1, 2])

    assert names(result) == ["001-the-owl-who-lost-her-hat.mp3"]
    assert len(result["skipped"]) == 1
    assert result["skipped"][0].startswith("Two Snails Race: ")


def test_the_job_fails_when_no_picked_episode_downloads(isolated_library, web):
    serve_feed(web, feed_xml(STORIES))

    with pytest.raises(RuntimeError, match="None of the picked episodes could be downloaded."):
        podcast.import_feed(FEED_URL, stage_id="podcast-all-fail", playlist_items=[1, 2])


def test_a_pick_past_the_end_of_the_feed_is_skipped(isolated_library, web):
    serve_stories(web)

    result = podcast.import_feed(FEED_URL, stage_id="podcast-short", playlist_items=[1, 9])

    assert names(result) == ["001-the-owl-who-lost-her-hat.mp3"]
    assert result["skipped"] == ["Episode 9 is no longer in the feed."]


def test_a_resumed_import_does_not_download_a_finished_episode_again(isolated_library, web):
    serve_stories(web)
    stage = library.begin_collection_stage(
        "podcast-resume", title="Moonbeam Bedtime Tales", source="podcast", extra={})
    (stage.path / "001-the-owl-who-lost-her-hat.mp3").write_bytes(b"from last time")

    result = podcast.import_feed(FEED_URL, stage_id="podcast-resume", playlist_items=[1, 2])

    assert names(result) == ["001-the-owl-who-lost-her-hat.mp3", "002-two-snails-race.mp3"]
    assert (Path(result["path"]) / "001-the-owl-who-lost-her-hat.mp3").read_bytes() == b"from last time"
    assert not any(request.url.path == "/owl.mp3" for request in web.seen)


def test_download_progress_counts_the_picked_episodes(isolated_library, web):
    serve_stories(web)
    reported = []

    podcast.import_feed(FEED_URL, stage_id="podcast-progress", playlist_items=[1, 2],
                        progress=lambda message, percent=None: reported.append((message, percent)))

    assert ("Downloading 1/2: The Owl Who Lost Her Hat", 0.0) in reported
    assert ("Downloading 2/2: Two Snails Race", 50.0) in reported


def test_episodes_with_the_same_title_keep_separate_files(isolated_library, web):
    serve_feed(web, feed_xml(
        item("Goodnight Moonbeam", date="Tue, 02 Jan 2024 06:00:00 +0000", url="https://cdn.example.test/g2.mp3")
        + item("Goodnight Moonbeam", date="Mon, 01 Jan 2024 06:00:00 +0000", url="https://cdn.example.test/g1.mp3")))
    web.routes["cdn.example.test/g1.mp3"] = reply("first night", content_type="audio/mpeg")
    web.routes["cdn.example.test/g2.mp3"] = reply("second night", content_type="audio/mpeg")

    result = podcast.import_feed(FEED_URL, stage_id="podcast-twins")

    assert names(result) == ["001-goodnight-moonbeam.mp3", "002-goodnight-moonbeam.mp3"]
    assert (Path(result["path"]) / "002-goodnight-moonbeam.mp3").read_bytes() == b"second night"


def test_the_file_extension_comes_from_the_enclosure_path(isolated_library, web):
    serve_feed(web, feed_xml(item("Porch Songs", url="https://cdn.example.test/porch.m4a?source=rss", kind="")))
    web.routes["cdn.example.test/porch.m4a"] = reply("porch audio", content_type="audio/mp4")

    result = podcast.import_feed(FEED_URL, stage_id="podcast-m4a")

    assert names(result) == ["001-porch-songs.m4a"]


# ------------------------------------------------------------------ routes

@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def refuse(*args, **kwargs):
    raise AssertionError("this path must not run")


YOUTUBE_SHAPE = {"title": "How Search Works",
                 "entries": [{"index": 1, "id": "aaa", "title": "First", "available": True}]}


def test_preview_route_answers_a_podcast_link_with_its_episodes(client, web, monkeypatch):
    monkeypatch.setattr(main.ingest, "playlist_preview", refuse)
    web.routes["itunes.apple.com/lookup"] = reply_json({"results": [{"feedUrl": FEED_URL}]})
    serve_feed(web, feed_xml(STORIES))

    response = client.post("/api/playlist/preview", json={"url": APPLE_LINK})

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "podcast"
    assert body["preselect"] == [1, 2, 3, 4]
    assert body["entries"][0] == {"index": 1, "id": "ep-1", "title": "The Owl Who Lost Her Hat", "available": True}


def test_preview_route_reads_a_raw_feed_as_a_podcast_before_yt_dlp(client, web, monkeypatch):
    monkeypatch.setattr(main.ingest, "playlist_preview", refuse)
    serve_feed(web, feed_xml(STORIES))

    response = client.post("/api/playlist/preview", json={"url": FEED_URL})

    assert response.status_code == 200
    assert response.json()["kind"] == "podcast"


def test_preview_route_keeps_yt_dlp_error_when_the_link_is_not_a_feed_either(client, web, monkeypatch):
    def fail_like_yt_dlp(url):
        raise RuntimeError("yt-dlp could not read that playlist: ERROR: Unsupported URL")
    monkeypatch.setattr(main.ingest, "playlist_preview", fail_like_yt_dlp)

    response = client.post("/api/playlist/preview", json={"url": "https://example.test/story"})

    assert response.status_code == 502
    assert response.json()["detail"] == "yt-dlp could not read that playlist: ERROR: Unsupported URL"


def test_preview_route_answers_in_yt_dlp_shape_when_the_link_is_not_a_feed(client, web, monkeypatch):
    monkeypatch.setattr(main.ingest, "playlist_preview", lambda url: YOUTUBE_SHAPE)

    response = client.post("/api/playlist/preview", json={"url": "https://example.test/story"})

    assert response.status_code == 200
    assert response.json() == YOUTUBE_SHAPE


def test_preview_route_never_reads_a_youtube_playlist_as_a_feed(client, monkeypatch):
    monkeypatch.setattr(main.podcast, "preview", refuse)
    monkeypatch.setattr(main.ingest, "playlist_preview", lambda url: YOUTUBE_SHAPE)

    response = client.post("/api/playlist/preview", json={"url": "https://www.youtube.com/playlist?list=PL1"})

    assert response.status_code == 200
    assert "kind" not in response.json()


def test_preview_route_refuses_spotify_music(client, monkeypatch):
    monkeypatch.setattr(main.ingest, "playlist_preview", refuse)

    response = client.post("/api/playlist/preview", json={"url": "https://open.spotify.com/track/1a2b3c"})

    assert response.status_code == 400
    assert response.json()["detail"] == podcast.MUSIC_REFUSAL


def test_preview_route_reports_an_unreadable_spotify_page(client, web, monkeypatch):
    monkeypatch.setattr(main.ingest, "playlist_preview", refuse)
    web.routes["open.spotify.com/embed/show/4aBcDeFg"] = reply("<html><body>Nothing here</body></html>")

    response = client.post("/api/playlist/preview", json={"url": "https://open.spotify.com/show/4aBcDeFg"})

    assert response.status_code == 502
    assert response.json()["detail"] == podcast.SPOTIFY_UNREADABLE


def capture_jobs(monkeypatch) -> list[dict]:
    payloads: list[dict] = []
    monkeypatch.setattr(main.jobs, "enqueue_many",
                        lambda entries: payloads.extend(entry[2] for entry in entries) or ["job-1"])
    return payloads


def test_prepare_refuses_spotify_music(client, monkeypatch):
    payloads = capture_jobs(monkeypatch)

    response = client.post("/api/prepare", json={"sources": [{"url": "https://open.spotify.com/album/1a2b3c"}]})

    assert response.status_code == 400
    assert response.json()["detail"] == podcast.MUSIC_REFUSAL
    assert payloads == []


def test_prepare_carries_the_podcast_kind_into_the_job(client, monkeypatch):
    payloads = capture_jobs(monkeypatch)

    response = client.post("/api/prepare", json={
        "sources": [{"url": FEED_URL, "playlist_items": [1, 3], "kind": "podcast"}],
    })

    assert response.status_code == 200
    assert payloads[0]["kind"] == "podcast"
    assert payloads[0]["playlist_items"] == [1, 3]


def test_prepare_without_a_kind_carries_none(client, monkeypatch):
    payloads = capture_jobs(monkeypatch)

    response = client.post("/api/prepare", json={"sources": [{"url": "https://example.test/story"}]})

    assert response.status_code == 200
    assert payloads[0]["kind"] is None


def test_prepare_refuses_an_unknown_kind(client, monkeypatch):
    payloads = capture_jobs(monkeypatch)

    response = client.post("/api/prepare", json={
        "sources": [{"url": "https://example.test/story", "kind": "video"}],
    })

    assert response.status_code == 422
    assert payloads == []


def run_prepare(monkeypatch, payload: dict) -> dict:
    seen: dict = {}
    monkeypatch.setattr(prepare.podcast, "import_feed",
                        lambda url, **kw: seen.update(url=url, **kw) or {"slug": "moonbeam"})
    monkeypatch.setattr(prepare.ingest, "import_url",
                        lambda url, **kw: seen.update(url=url, via="yt-dlp") or {"slug": "other"})
    monkeypatch.setattr(prepare.forge, "run_collection_stage", lambda stage_id, **kw: {"slug": "moonbeam"})
    monkeypatch.setattr(prepare.library, "find_published_stage", lambda stage_id: None)
    monkeypatch.setattr(prepare.library, "collection_stage", lambda stage_id: None)
    prepare.run({**payload, "stage_id": "url-podcast"},
                progress=lambda message, percent=None: None,
                checkpoint=lambda updated: None)
    return seen


def test_prepare_run_sends_a_podcast_kind_to_the_feed_import(monkeypatch):
    seen = run_prepare(monkeypatch, {"url": FEED_URL, "kind": "podcast", "playlist_items": [2, 4]})

    assert "via" not in seen
    assert seen["url"] == FEED_URL
    assert seen["stage_id"] == "url-podcast"
    assert seen["playlist_items"] == [2, 4]


def test_prepare_run_sends_a_spotify_show_to_the_feed_import(monkeypatch):
    seen = run_prepare(monkeypatch, {"url": "https://open.spotify.com/show/4aBcDeFg", "playlist_items": None})

    assert "via" not in seen
    assert seen["playlist_items"] is None


def test_prepare_run_leaves_other_links_to_yt_dlp(monkeypatch):
    seen = run_prepare(monkeypatch, {"url": "https://example.test/story", "kind": None})

    assert seen["via"] == "yt-dlp"
