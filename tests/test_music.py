"""Spotify music links: link shapes, the song list, the preview and the YouTube match.

Nothing here touches the network or runs yt-dlp. Spotify pages come from the
`web` fixture's in-memory transport; yt-dlp is a stubbed `subprocess.run`.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import audio, config, db, library, main, music, prepare

from .conftest import capture_jobs, reply, spotify_page

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


# ------------------------------------------------------------------ import

@pytest.fixture
def stage_ready(monkeypatch):
    """A real library and database under the test's tmp_path, with durations faked."""
    config.ensure_dirs()
    db.init()
    monkeypatch.setattr(audio, "duration_seconds", lambda path: 10)


@pytest.fixture
def youtube(monkeypatch):
    """Stand in for yt-dlp: a search answers from `found`, a download writes the mp3 it was asked for.

    `found` maps a search query to a video id. A video in `failing` exits with
    that stderr and writes nothing; one in `crashing` raises, the way a worker
    that dies mid-import would leave the stage half-built.
    """
    state = SimpleNamespace(found={}, failing={}, crashing=set(), searches=[], downloads=[], timeouts=[])

    def run(command, **kwargs):
        if "--flat-playlist" in command:
            query = command[-1].split(":", 1)[1]
            state.searches.append(query)
            video = state.found.get(query)
            entries = [{"id": video, "title": query, "duration": 180.0}] if video else []
            return SimpleNamespace(returncode=0, stdout=json.dumps({"entries": entries}), stderr="")
        state.downloads.append(list(command))
        state.timeouts.append(kwargs.get("timeout"))
        video = command[-1].rsplit("=", 1)[1]
        if video in state.crashing:
            raise RuntimeError("the worker died")
        if video in state.failing:
            return SimpleNamespace(returncode=1, stdout="", stderr=state.failing[video])
        target = Path(command[command.index("-o") + 1].replace("%(ext)s", "mp3"))
        target.write_bytes(f"audio of {video}".encode())
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(music.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    monkeypatch.setattr(music.subprocess, "run", run)
    return state


CAR_SONGS = [
    listed(1, "Baby Beluga", "Raffi"),
    listed(2, "Down by the Bay", "Raffi"),
    listed(3, "Wheels on the Bus", "Kids Choir", playable=False),
    listed(4, "Shake My Sillies Out", "Raffi"),
]


def serve_car_songs(web, youtube, tracks: list[dict] = CAR_SONGS) -> None:
    serve(web, "playlist", "37i9dQZF1DX", playlist_entity(tracks))
    for track in tracks:
        query = " ".join(part for part in (track["subtitle"], track["title"]) if part)
        youtube.found[query] = f"yt{track['uri'][-4:]}"


def names(result) -> list[str]:
    return [track["name"] for track in result["tracks"]]


def titles(result) -> list[str]:
    return [track["title"] for track in result["tracks"]]


def test_picked_songs_are_stored_in_list_order_under_their_labels(stage_ready, web, youtube):
    serve_car_songs(web, youtube)

    result = music.import_music(PLAYLIST_URL, stage_id="music-pick",
                                track_ids=["spotify:track:0004", "spotify:track:0001"])

    assert names(result) == ["001-baby-beluga-raffi.mp3", "002-shake-my-sillies-out-raffi.mp3"]
    assert titles(result) == ["Baby Beluga - Raffi", "Shake My Sillies Out - Raffi"]
    assert (Path(result["path"]) / "001-baby-beluga-raffi.mp3").read_bytes() == b"audio of yt0001"
    assert result["title"] == "Car Songs"
    assert result["source"] == "music"
    assert result["url"] == PLAYLIST_URL
    assert result["uploader"] == "Lantern Hill"
    assert result["skipped"] == []


def test_a_picked_song_that_is_unplayable_on_spotify_is_still_brought(stage_ready, web, youtube):
    serve_car_songs(web, youtube)

    result = music.import_music(PLAYLIST_URL, stage_id="music-unplayable", track_ids=["spotify:track:0003"])

    assert titles(result) == ["Wheels on the Bus - Kids Choir"]


def test_an_unpicked_import_takes_every_available_song(stage_ready, web, youtube):
    serve_car_songs(web, youtube)

    result = music.import_music(PLAYLIST_URL, stage_id="music-all")

    assert titles(result) == ["Baby Beluga - Raffi", "Down by the Bay - Raffi", "Shake My Sillies Out - Raffi"]
    assert names(result)[2] == "003-shake-my-sillies-out-raffi.mp3"


@pytest.mark.parametrize(("gone", "line"), [
    (["spotify:track:9998"], "1 picked song is no longer in the list."),
    (["spotify:track:9998", "spotify:track:9999"], "2 picked songs are no longer in the list."),
])
def test_picked_songs_that_left_the_list_are_counted_in_skipped(stage_ready, web, youtube, gone, line):
    serve_car_songs(web, youtube)

    result = music.import_music(PLAYLIST_URL, stage_id="music-gone", track_ids=["spotify:track:0002", *gone])

    assert titles(result) == ["Down by the Bay - Raffi"]
    assert result["skipped"] == [line]


def test_an_unmatched_and_a_failed_song_are_skipped_by_name_while_the_rest_land(stage_ready, web, youtube):
    serve_car_songs(web, youtube)
    del youtube.found["Raffi Down by the Bay"]
    youtube.failing["yt0004"] = "ERROR: [youtube] yt0004: Video unavailable"

    result = music.import_music(PLAYLIST_URL, stage_id="music-some-fail")

    assert names(result) == ["001-baby-beluga-raffi.mp3"]
    assert result["skipped"] == [
        "Down by the Bay - Raffi: no close match on YouTube.",
        "Shake My Sillies Out - Raffi: ERROR: [youtube] yt0004: Video unavailable",
    ]


def test_an_import_that_stores_no_song_fails_rather_than_publishing_nothing(stage_ready, web, youtube):
    serve_car_songs(web, youtube)
    youtube.found.clear()

    with pytest.raises(RuntimeError, match=music.NONE_FOUND):
        music.import_music(PLAYLIST_URL, stage_id="music-none-found")

    assert library.find_published_stage("music-none-found") is None


def test_a_pick_whose_every_song_left_the_list_fails_without_searching(stage_ready, web, youtube):
    serve_car_songs(web, youtube)

    with pytest.raises(RuntimeError, match=music.NONE_FOUND):
        music.import_music(PLAYLIST_URL, stage_id="music-all-gone", track_ids=["spotify:track:9999"])

    assert youtube.searches == []


def test_an_unpicked_import_of_a_list_where_every_song_is_unplayable_fails(stage_ready, web, youtube):
    serve_car_songs(web, youtube, [listed(1, "Gone", playable=False), listed(2, "Also Gone", playable=False)])

    with pytest.raises(RuntimeError, match=music.NONE_FOUND):
        music.import_music(PLAYLIST_URL, stage_id="music-unplayable-all")

    assert youtube.searches == []
    assert library.find_published_stage("music-unplayable-all") is None


def test_a_retried_import_keeps_stored_files_and_searches_a_skipped_song_again(stage_ready, web, youtube):
    serve_car_songs(web, youtube)
    del youtube.found["Raffi Down by the Bay"]
    youtube.crashing.add("yt0004")

    with pytest.raises(RuntimeError, match="the worker died"):
        music.import_music(PLAYLIST_URL, stage_id="music-retry")

    youtube.found["Raffi Down by the Bay"] = "yt0002"
    youtube.crashing.clear()
    youtube.downloads.clear()
    youtube.searches.clear()

    result = music.import_music(PLAYLIST_URL, stage_id="music-retry")

    assert names(result) == ["001-baby-beluga-raffi.mp3", "002-down-by-the-bay-raffi.mp3",
                             "003-shake-my-sillies-out-raffi.mp3"]
    assert [command[-1] for command in youtube.downloads] == [
        "https://www.youtube.com/watch?v=yt0002", "https://www.youtube.com/watch?v=yt0004"]
    assert "Raffi Baby Beluga" not in youtube.searches
    assert "Raffi Down by the Bay" in youtube.searches
    assert result["skipped"] == []


def test_titles_with_nothing_to_slug_still_get_valid_and_distinct_file_names(stage_ready, web, youtube):
    serve_car_songs(web, youtube, [listed(1, "♪♪♪", ""), listed(2, "★ ★", "")])

    result = music.import_music(PLAYLIST_URL, stage_id="music-symbols")

    assert names(result) == ["001-untitled.mp3", "002-untitled.mp3"]
    assert titles(result) == ["♪♪♪", "★ ★"]
    assert (Path(result["path"]) / "002-untitled.mp3").read_bytes() == b"audio of yt0002"


def test_finding_reports_each_song_with_a_measured_percent(stage_ready, web, youtube):
    serve_car_songs(web, youtube)
    reported = []

    music.import_music(PLAYLIST_URL, stage_id="music-progress",
                       track_ids=["spotify:track:0001", "spotify:track:0002"],
                       progress=lambda message, percent=None: reported.append((message, percent)))

    assert ("Finding 1/2: Baby Beluga", 0.0) in reported
    assert ("Finding 2/2: Down by the Bay", 50.0) in reported


def test_the_download_lands_in_scratch_under_the_work_dir_and_moves_in_under_its_final_name(
    stage_ready, web, youtube, monkeypatch,
):
    monkeypatch.setattr(music.config, "YTDLP_PLAYER_CLIENTS", "android,web")
    serve_car_songs(web, youtube)

    result = music.import_music(PLAYLIST_URL, stage_id="music-argv", track_ids=["spotify:track:0001"])

    command = youtube.downloads[0]
    scratch = Path(command[command.index("-o") + 1]).parent
    assert command == [
        "yt-dlp", "--no-playlist", "-x", "--audio-format", "mp3", "--audio-quality", "0",
        "--no-progress", "--no-warnings",
        "--extractor-args", "youtube:player_client=android,web",
        "-o", str(scratch / "song.%(ext)s"), "https://www.youtube.com/watch?v=yt0001",
    ]
    assert scratch.parent == config.WORK_DIR
    assert not scratch.exists()
    assert youtube.timeouts == [1800]
    assert names(result) == ["001-baby-beluga-raffi.mp3"]


def test_a_download_is_judged_by_the_mp3_it_left_not_by_its_exit_code(stage_ready, web, youtube, monkeypatch):
    serve_car_songs(web, youtube)
    real = music.subprocess.run

    def exits_badly(command, **kwargs):
        answer = real(command, **kwargs)
        if "--flat-playlist" in command:
            return answer
        return SimpleNamespace(returncode=1, stdout="", stderr="ERROR: postprocessing hiccup")

    monkeypatch.setattr(music.subprocess, "run", exits_badly)

    result = music.import_music(PLAYLIST_URL, stage_id="music-exit-code", track_ids=["spotify:track:0001"])

    assert names(result) == ["001-baby-beluga-raffi.mp3"]
    assert result["skipped"] == []


def test_a_clean_exit_that_left_no_mp3_skips_the_song(stage_ready, web, youtube, monkeypatch):
    serve_car_songs(web, youtube)
    real = music.subprocess.run

    def writes_nothing_for_the_bay(command, **kwargs):
        if command[-1].endswith("=yt0002"):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return real(command, **kwargs)

    monkeypatch.setattr(music.subprocess, "run", writes_nothing_for_the_bay)

    result = music.import_music(PLAYLIST_URL, stage_id="music-no-mp3",
                                track_ids=["spotify:track:0001", "spotify:track:0002"])

    assert names(result) == ["001-baby-beluga-raffi.mp3"]
    assert result["skipped"] == ["Down by the Bay - Raffi: yt-dlp produced no audio."]


def test_a_download_that_times_out_skips_the_song(stage_ready, web, youtube, monkeypatch):
    serve_car_songs(web, youtube)
    real = music.subprocess.run

    def stalls_on_the_bay(command, **kwargs):
        if command[-1].endswith("=yt0002"):
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))
        return real(command, **kwargs)

    monkeypatch.setattr(music.subprocess, "run", stalls_on_the_bay)

    result = music.import_music(PLAYLIST_URL, stage_id="music-stall",
                                track_ids=["spotify:track:0001", "spotify:track:0002"])

    assert names(result) == ["001-baby-beluga-raffi.mp3"]
    assert result["skipped"] == ["Down by the Bay - Raffi: the download took longer than 30 minutes."]


def test_the_cover_is_saved_beside_the_songs(stage_ready, web, youtube):
    serve_car_songs(web, youtube)
    web.routes["img.example.test/640.jpg"] = reply(b"cover bytes", content_type="image/jpeg")

    result = music.import_music(PLAYLIST_URL, stage_id="music-cover", track_ids=["spotify:track:0001"])

    assert (Path(result["path"]) / "cover.jpg").read_bytes() == b"cover bytes"


def test_a_cover_that_fails_to_download_is_ignored(stage_ready, web, youtube):
    serve_car_songs(web, youtube)

    result = music.import_music(PLAYLIST_URL, stage_id="music-no-cover", track_ids=["spotify:track:0001"])

    assert not (Path(result["path"]) / "cover.jpg").exists()
    assert names(result) == ["001-baby-beluga-raffi.mp3"]


def test_a_missing_yt_dlp_fails_the_import_before_reading_spotify(stage_ready, web, monkeypatch):
    monkeypatch.setattr(music.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match=music.YT_DLP_MISSING):
        music.import_music(PLAYLIST_URL, stage_id="music-no-tool")

    assert web.seen == []


# ------------------------------------------------------------------ routes

@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def refuse(*args, **kwargs):
    raise AssertionError("this path must not run")


def test_the_preview_route_refuses_an_artist_link_with_a_400(client, monkeypatch):
    monkeypatch.setattr(main.ingest, "playlist_preview", refuse)
    monkeypatch.setattr(main.podcast, "preview", refuse)

    response = client.post("/api/playlist/preview",
                           json={"url": "https://open.spotify.com/artist/0TnOYISbd1XYRBk9myaseg"})

    assert response.status_code == 400
    assert response.json()["detail"] == music.ARTIST_REFUSAL


def test_the_preview_route_reports_an_unreadable_music_page_as_a_502(client, web, monkeypatch):
    monkeypatch.setattr(main.ingest, "playlist_preview", refuse)
    web.routes["open.spotify.com/embed/playlist/37i9dQZF1DX"] = reply("<html><body>Nothing</body></html>")

    response = client.post("/api/playlist/preview", json={"url": PLAYLIST_URL})

    assert response.status_code == 502
    assert response.json()["detail"] == music.SPOTIFY_UNREADABLE


def test_the_preview_route_carries_the_limit_notice(client, web, monkeypatch):
    monkeypatch.setattr(main.ingest, "playlist_preview", refuse)
    serve(web, "playlist", "37i9dQZF1DX", playlist_entity([listed(n) for n in range(1, 101)]))

    response = client.post("/api/playlist/preview", json={"url": PLAYLIST_URL})

    assert response.status_code == 200
    assert response.json()["notice"] == music.LIMIT_NOTICE


def test_prepare_carries_the_picked_track_ids_into_the_job(client, monkeypatch):
    payloads = capture_jobs(monkeypatch)

    response = client.post("/api/prepare", json={
        "sources": [{"url": f"{PLAYLIST_URL}?si=abc", "track_ids": ["spotify:track:0002", "spotify:track:0001"]}],
    })

    assert response.status_code == 200
    assert payloads[0]["track_ids"] == ["spotify:track:0002", "spotify:track:0001"]
    assert payloads[0]["playlist_items"] is None
    assert payloads[0]["episode_ids"] is None


def test_prepare_carries_no_track_ids_for_other_sources(client, monkeypatch):
    payloads = capture_jobs(monkeypatch)

    response = client.post("/api/prepare", json={"sources": [{"url": "https://example.test/story"}]})

    assert response.status_code == 200
    assert payloads[0]["track_ids"] is None


@pytest.mark.parametrize(("source", "detail"), [
    ({"url": PLAYLIST_URL, "track_ids": []}, "Pick at least one song, or remove that source."),
    ({"url": "https://www.youtube.com/playlist?list=PL1", "track_ids": ["spotify:track:0001"]},
     "Only a Spotify music source can pick songs."),
    ({"url": "https://open.spotify.com/show/4aBcDeFg", "track_ids": ["spotify:track:0001"]},
     "Only a Spotify music source can pick songs."),
    ({"url": "https://feeds.example.test/moonbeam.xml", "kind": "podcast", "track_ids": ["spotify:track:0001"]},
     "Only a Spotify music source can pick songs."),
    ({"url": ALBUM_URL, "playlist_items": [1]},
     "A Spotify music source picks songs with track_ids, not playlist_items or episode_ids."),
    ({"url": TRACK_URL, "episode_ids": ["ep-1"]},
     "A Spotify music source picks songs with track_ids, not playlist_items or episode_ids."),
    ({"url": "https://open.spotify.com/artist/0TnOYISbd1XYRBk9myaseg"}, music.ARTIST_REFUSAL),
])
def test_prepare_refuses_a_pick_that_does_not_fit_the_source(client, monkeypatch, source, detail):
    payloads = capture_jobs(monkeypatch)

    response = client.post("/api/prepare", json={"sources": [source]})

    assert response.status_code == 400
    assert response.json()["detail"] == detail
    assert payloads == []


def run_prepare(monkeypatch, payload: dict) -> dict:
    seen: dict = {}
    monkeypatch.setattr(prepare.music, "import_music",
                        lambda url, **kw: seen.update(url=url, via="music", **kw) or {"slug": "car-songs"})
    monkeypatch.setattr(prepare.podcast, "import_feed", refuse)
    monkeypatch.setattr(prepare.ingest, "import_url", refuse)
    monkeypatch.setattr(prepare.forge, "run_collection_stage", lambda stage_id, **kw: {"slug": "car-songs"})
    monkeypatch.setattr(prepare.library, "find_published_stage", lambda stage_id: None)
    monkeypatch.setattr(prepare.library, "collection_stage", lambda stage_id: None)
    prepare.run({**payload, "stage_id": "url-music"},
                progress=lambda message, percent=None: None,
                checkpoint=lambda updated: None)
    return seen


def test_prepare_run_sends_a_music_link_to_the_music_import_with_its_picks(monkeypatch):
    seen = run_prepare(monkeypatch, {"url": PLAYLIST_URL, "track_ids": ["spotify:track:0001"]})

    assert seen["via"] == "music"
    assert seen["url"] == PLAYLIST_URL
    assert seen["stage_id"] == "url-music"
    assert seen["track_ids"] == ["spotify:track:0001"]


def test_prepare_run_sends_an_unpicked_music_link_to_the_music_import(monkeypatch):
    seen = run_prepare(monkeypatch, {"url": ALBUM_URL})

    assert seen["via"] == "music"
    assert seen["track_ids"] is None
