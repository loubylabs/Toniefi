"""Spotify music links: link shapes, the song list, the preview and the YouTube match.

Nothing here touches the network or runs yt-dlp. Spotify pages come from the
`web` fixture's in-memory transport; yt-dlp is a stubbed `subprocess.run`.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from app import music

from .conftest import reply, spotify_page

PLAYLIST_URL = "https://open.spotify.com/playlist/37i9dQZF1DX"
ALBUM_URL = "https://open.spotify.com/album/2noRn2Aes5aoNVsU6iWThc"
TRACK_URL = "https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl"

NBSP = " "


def cover_images() -> dict:
    return {"image": [
        {"url": "https://img.example.test/300.jpg", "maxHeight": 300, "maxWidth": 300},
        {"url": "https://img.example.test/64.jpg", "maxHeight": 64, "maxWidth": 64},
        {"url": "https://img.example.test/640.jpg", "maxHeight": 640, "maxWidth": 640},
    ]}


def listed(number: int, title: str = "Song", subtitle: str = "Artist A", *,
           duration: int = 180_000, playable: bool = True, explicit: bool = False) -> dict:
    return {
        "uri": f"spotify:track:{number:04d}", "uid": f"uid{number}", "title": title,
        "subtitle": subtitle, "isExplicit": explicit, "duration": duration,
        "isPlayable": playable, "entityType": "track",
    }


def playlist_entity(tracks: list[dict], name: str = "Car Songs") -> dict:
    return {"type": "playlist", "name": name, "title": name, "subtitle": "Lantern Hill",
            "trackList": tracks, "visualIdentity": cover_images(), "coverArt": None}


def serve(web, kind: str, spotify_id: str, entity: dict) -> None:
    web.routes[f"open.spotify.com/embed/{kind}/{spotify_id}"] = reply(spotify_page(entity))


# ------------------------------------------------------------- link shapes

@pytest.mark.parametrize("url", [
    PLAYLIST_URL,
    ALBUM_URL,
    TRACK_URL,
    "https://open.spotify.com/embed/playlist/37i9dQZF1DX",
    "https://open.spotify.com/intl-de/album/2noRn2Aes5aoNVsU6iWThc",
    "https://open.spotify.com/intl-pt-br/embed/track/11dFghVXANMlKmJXsNCbNl",
    f"{PLAYLIST_URL}?si=abc123def456",
    f"{TRACK_URL}?si=abc123&utm_source=copy-link",
])
def test_playlist_album_and_song_links_are_music_links(url):
    assert music.is_music_url(url)
    assert not music.is_artist_url(url)


@pytest.mark.parametrize("url", [
    "https://open.spotify.com/artist/0TnOYISbd1XYRBk9myaseg",
    "https://open.spotify.com/intl-de/artist/0TnOYISbd1XYRBk9myaseg?si=abc",
    "https://open.spotify.com/embed/artist/0TnOYISbd1XYRBk9myaseg",
])
def test_an_artist_link_is_an_artist_link_and_not_a_music_link(url):
    assert music.is_artist_url(url)
    assert not music.is_music_url(url)


@pytest.mark.parametrize("url", [
    "https://open.spotify.com/show/4aBcDeFg",
    "https://open.spotify.com/episode/7xYzWv",
    "https://www.youtube.com/playlist?list=PL1",
    "https://example.test/playlist/37i9dQZF1DX",
    "https://open.spotify.com/playlist/",
    "https://open.spotify.com/",
    "not a url",
])
def test_show_episode_and_other_links_are_not_music_links(url):
    assert not music.is_music_url(url)
    assert not music.is_artist_url(url)


def test_reading_an_artist_link_is_refused_without_a_request(web):
    with pytest.raises(RuntimeError, match="Artist pages are not supported"):
        music.read("https://open.spotify.com/artist/0TnOYISbd1XYRBk9myaseg")

    assert web.seen == []


# ----------------------------------------------------------------- listing

def test_a_playlist_lists_its_songs_from_one_in_order(web):
    serve(web, "playlist", "37i9dQZF1DX", playlist_entity([
        listed(1, "Baby Beluga", "Raffi", duration=164_000, explicit=True),
        listed(2, "Hello (feat. X)", f"Artist A,{NBSP}Artist B", duration=0, playable=False),
    ]))

    listing = music.read(f"{PLAYLIST_URL}?si=abc123")

    assert (listing.kind, listing.title, listing.owner) == ("playlist", "Car Songs", "Lantern Hill")
    assert listing.cover == "https://img.example.test/640.jpg"
    assert listing.songs == [
        music.Song(index=1, id="spotify:track:0001", title="Baby Beluga", artists="Raffi",
                   duration=164.0, available=True, explicit=True),
        music.Song(index=2, id="spotify:track:0002", title="Hello (feat. X)", artists="Artist A, Artist B",
                   duration=None, available=False, explicit=False),
    ]
    assert web.seen[0].url.path == "/embed/playlist/37i9dQZF1DX"


def test_an_album_is_owned_by_its_artist(web):
    entity = playlist_entity([listed(1, "Opening", "Raffi")], name="Singable Songs")
    entity["type"] = "album"
    entity["subtitle"] = "Raffi"
    serve(web, "album", "2noRn2Aes5aoNVsU6iWThc", entity)

    listing = music.read(ALBUM_URL)

    assert (listing.kind, listing.title, listing.owner) == ("album", "Singable Songs", "Raffi")
    assert [song.title for song in listing.songs] == ["Opening"]


def test_a_single_song_is_a_list_of_one_owned_by_its_artists(web):
    serve(web, "track", "11dFghVXANMlKmJXsNCbNl", {
        "type": "track", "name": "Cut To The Feeling", "title": "Cut To The Feeling",
        "uri": "spotify:track:11dFghVXANMlKmJXsNCbNl",
        "artists": [{"name": "Carly Rae Jepsen", "uri": "spotify:artist:1"},
                    {"name": "Friend", "uri": "spotify:artist:2"}],
        "duration": 207_959, "isPlayable": True, "isExplicit": False,
        "visualIdentity": cover_images(),
    })

    listing = music.read(TRACK_URL)

    assert (listing.kind, listing.title, listing.owner) == (
        "track", "Cut To The Feeling", "Carly Rae Jepsen, Friend")
    assert listing.songs == [music.Song(
        index=1, id="spotify:track:11dFghVXANMlKmJXsNCbNl", title="Cut To The Feeling",
        artists="Carly Rae Jepsen, Friend", duration=pytest.approx(207.959), available=True, explicit=False)]


def test_a_song_listed_twice_is_listed_once_and_numbering_closes_the_gap(web):
    serve(web, "playlist", "37i9dQZF1DX", playlist_entity([
        listed(1, "One"), listed(2, "Two"), listed(1, "One"), listed(3, "Three"),
    ]))

    songs = music.read(PLAYLIST_URL).songs

    assert [(song.index, song.title) for song in songs] == [(1, "One"), (2, "Two"), (3, "Three")]


def test_the_cover_is_the_widest_image_and_a_missing_cover_is_fine(web):
    entity = playlist_entity([listed(1)])
    del entity["visualIdentity"]
    serve(web, "playlist", "37i9dQZF1DX", entity)

    assert music.read(PLAYLIST_URL).cover is None


@pytest.mark.parametrize("entity", [
    {"type": "playlist", "name": "Empty", "subtitle": "Someone", "trackList": []},
    {"type": "playlist", "name": "No list", "subtitle": "Someone"},
    {"type": "playlist", "subtitle": "Someone", "trackList": [listed(1)]},
    None,
])
def test_a_page_without_a_name_or_songs_is_unreadable(web, entity):
    serve(web, "playlist", "37i9dQZF1DX", entity)

    with pytest.raises(RuntimeError) as raised:
        music.read(PLAYLIST_URL)

    assert str(raised.value) == music.SPOTIFY_UNREADABLE
    assert "Apple Podcasts" not in str(raised.value)


def test_a_page_with_no_data_script_is_unreadable(web):
    web.routes["open.spotify.com/embed/playlist/37i9dQZF1DX"] = reply("<html>Nothing here</html>")

    with pytest.raises(RuntimeError, match="Spotify changed its page"):
        music.read(PLAYLIST_URL)


# ----------------------------------------------------------------- preview

def test_the_preview_answers_in_the_pickers_shape(web):
    serve(web, "playlist", "37i9dQZF1DX", playlist_entity([
        listed(1, "Baby Beluga", "Raffi"),
        listed(2, "Rude Song", "Someone", explicit=True),
        listed(3, "Gone", "Nobody", playable=False),
    ]))

    preview = music.preview(PLAYLIST_URL)

    assert preview == {
        "title": "Car Songs",
        "entries": [
            {"index": 1, "id": "spotify:track:0001", "title": "Baby Beluga - Raffi", "available": True},
            {"index": 2, "id": "spotify:track:0002", "title": "Rude Song - Someone (explicit)",
             "available": True},
            {"index": 3, "id": "spotify:track:0003", "title": "Gone - Nobody", "available": False},
        ],
        "kind": "music",
    }
    assert "preselect" not in preview
    assert "notice" not in preview


def test_a_song_with_no_artists_is_labelled_by_its_title_alone(web):
    serve(web, "playlist", "37i9dQZF1DX", playlist_entity([listed(1, "Lullaby", "")]))

    assert music.preview(PLAYLIST_URL)["entries"][0]["title"] == "Lullaby"


def test_a_playlist_of_exactly_one_hundred_songs_carries_the_limit_notice(web):
    serve(web, "playlist", "37i9dQZF1DX", playlist_entity([listed(n) for n in range(1, 101)]))

    assert music.preview(PLAYLIST_URL)["notice"] == music.LIMIT_NOTICE


def test_a_playlist_of_ninety_nine_songs_has_no_notice(web):
    serve(web, "playlist", "37i9dQZF1DX", playlist_entity([listed(n) for n in range(1, 100)]))

    assert "notice" not in music.preview(PLAYLIST_URL)


def test_an_album_of_one_hundred_songs_has_no_notice(web):
    entity = playlist_entity([listed(n) for n in range(1, 101)])
    entity["type"] = "album"
    serve(web, "album", "2noRn2Aes5aoNVsU6iWThc", entity)

    assert "notice" not in music.preview(ALBUM_URL)


def test_a_list_where_every_song_is_unplayable_previews_with_nothing_available(web):
    serve(web, "playlist", "37i9dQZF1DX", playlist_entity([
        listed(1, playable=False), listed(2, playable=False),
    ]))

    entries = music.preview(PLAYLIST_URL)["entries"]

    assert [entry["available"] for entry in entries] == [False, False]


def test_the_preview_of_an_artist_link_is_refused(web):
    with pytest.raises(RuntimeError, match="Artist pages are not supported"):
        music.preview("https://open.spotify.com/artist/0TnOYISbd1XYRBk9myaseg")


# ------------------------------------------------------------------- match

def song(title: str = "Baby Beluga", artists: str = "Raffi", duration: float | None = 164.0) -> music.Song:
    return music.Song(index=1, id="spotify:track:0001", title=title, artists=artists,
                      duration=duration, available=True, explicit=False)


@pytest.fixture
def search(monkeypatch):
    """Answer yt-dlp's flat search with a canned payload and record each command."""
    recorded: list[list[str]] = []
    timeouts: list[float] = []

    def install(payload=None, *, stdout: str | None = None, returncode: int = 0,
                raises: Exception | None = None) -> list[list[str]]:
        def run(command, **kwargs):
            recorded.append(list(command))
            timeouts.append(kwargs.get("timeout"))
            if raises:
                raise raises
            body = stdout if stdout is not None else json.dumps(payload)
            return SimpleNamespace(returncode=returncode, stdout=body, stderr="")

        monkeypatch.setattr(music.shutil, "which", lambda name: "/usr/bin/yt-dlp")
        monkeypatch.setattr(music.subprocess, "run", run)
        return recorded

    install.timeouts = timeouts
    return install


def entry(video_id: str | None, duration: float | None = ..., title: str = "A video") -> dict:
    found: dict = {"title": title}
    if video_id is not None:
        found["id"] = video_id
    if duration is not ...:
        found["duration"] = duration
    return found


def test_the_video_closest_in_length_wins_over_a_higher_rank(search):
    search({"entries": [entry("live", 310.0), entry("studio", 160.0), entry("video", 170.5)]})

    assert music.find_video(song(duration=164.0)) == "studio"


def test_a_tie_in_length_goes_to_the_higher_rank(search):
    search({"entries": [entry("first", 154.0), entry("second", 174.0)]})

    assert music.find_video(song(duration=164.0)) == "first"


def test_nothing_within_thirty_seconds_is_no_match(search):
    search({"entries": [entry("hour", 3600.0), entry("short", 130.0)]})

    assert music.find_video(song(duration=164.0)) is None


def test_exactly_thirty_seconds_away_still_matches(search):
    search({"entries": [entry("edge", 194.0)]})

    assert music.find_video(song(duration=164.0)) == "edge"


def test_an_unknown_spotify_length_takes_the_first_entry_with_an_id(search):
    search({"entries": [entry(None, 100.0), entry("second", None), entry("third", 999.0)]})

    assert music.find_video(song(duration=None)) == "second"


def test_entries_without_a_duration_or_an_id_are_never_matched_by_length(search):
    search({"entries": [entry("nolength"), entry("nulllength", None), entry(None, 164.0), entry("good", 170.0)]})

    assert music.find_video(song(duration=164.0)) == "good"


def test_entries_that_report_no_duration_at_all_are_no_match_when_the_length_is_known(search):
    search({"entries": [entry("nolength"), entry("nulllength", None)]})

    assert music.find_video(song(duration=164.0)) is None


@pytest.mark.parametrize("payload", [{"entries": []}, {}, {"entries": None}, []])
def test_a_search_with_no_entries_is_no_match(search, payload):
    search(payload)

    assert music.find_video(song()) is None


def test_unreadable_output_is_no_match(search):
    search(stdout="ERROR: not json", returncode=1)

    assert music.find_video(song()) is None


def test_a_timed_out_search_is_no_match(search):
    search(raises=subprocess.TimeoutExpired("yt-dlp", 120))

    assert music.find_video(song()) is None


def test_a_missing_yt_dlp_is_an_error_not_a_missed_song(monkeypatch):
    monkeypatch.setattr(music.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match="yt-dlp is not installed"):
        music.find_video(song())


def test_the_search_is_one_flat_call_with_the_query_as_a_single_argument(search):
    commands = search({"entries": [entry("abc", 164.0)]})

    music.find_video(song("Baby Beluga", "Raffi"))

    command = commands[0]
    assert command[0] == "yt-dlp"
    assert ["--flat-playlist", "--dump-single-json", "--no-warnings"] == command[1:4]
    assert command[-1] == "ytsearch5:Raffi Baby Beluga"
    assert "--download" not in command and "-x" not in command
    assert search.timeouts == [120]


def test_a_title_that_looks_like_a_flag_reaches_yt_dlp_as_one_search_argument(search):
    commands = search({"entries": [entry("abc", 164.0)]})
    awkward = song("--exec \"rm -rf /\": it's a 'trap'", "-Odd; $(Artist)")

    music.find_video(awkward)

    assert commands[0][-1] == "ytsearch5:-Odd; $(Artist) --exec \"rm -rf /\": it's a 'trap'"
    assert commands[0].count(commands[0][-1]) == 1


def test_the_player_client_arguments_are_passed_through(search, monkeypatch):
    monkeypatch.setattr(music.config, "YTDLP_PLAYER_CLIENTS", "android,web")
    commands = search({"entries": [entry("abc", 164.0)]})

    music.find_video(song())

    assert "--extractor-args" in commands[0]
    assert "youtube:player_client=android,web" in commands[0]
