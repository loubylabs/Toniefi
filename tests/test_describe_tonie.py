from __future__ import annotations

from app import audio, config
from app.push import describe_tonie


def test_chapters_are_normalised_and_kept():
    result = describe_tonie({
        "id": "t1",
        "name": "Creative Tonie",
        "secondsPresent": 120.0,
        "chapters": [
            {"id": "a", "title": "One", "file": "f-a", "seconds": 252.0,
             "transcoding": False},
        ],
    })
    assert result["chapters"] == [
        {"id": "a", "title": "One", "seconds": 252.0, "duration": "4m 12s",
         "transcoding": False},
    ]
    assert result["chapter_count"] == 1


def test_chapter_still_transcoding_shows_no_duration():
    """A chapter mid-transcode reports no length. Show nothing, not '0m 00s'."""
    result = describe_tonie({
        "id": "t1",
        "chapters": [{"id": "a", "title": "One", "transcoding": True}],
    })
    assert result["chapters"][0]["seconds"] == 0.0
    assert result["chapters"][0]["duration"] == ""
    assert result["chapters"][0]["transcoding"] is True


def test_free_time_is_the_tonie_limit_minus_what_is_present():
    result = describe_tonie({"id": "t1", "secondsPresent": 60.0, "chapters": []})
    assert result["seconds_present"] == 60.0
    assert result["time_used"] == "1m 00s"
    # Read the limit rather than hard-coding 5400: TONIE_LIMIT_SECONDS is an
    # env var, so a machine that sets it would fail a literal here.
    assert result["seconds_free"] == config.TONIE_LIMIT_SECONDS - 60.0
    assert result["time_free"] == audio.human_duration(
        config.TONIE_LIMIT_SECONDS - 60.0)


def test_free_time_never_goes_negative():
    result = describe_tonie(
        {"id": "t1", "secondsPresent": config.TONIE_LIMIT_SECONDS + 1.0,
         "chapters": []})
    assert result["seconds_free"] == 0


def test_missing_chapters_key_is_an_empty_list():
    result = describe_tonie({"id": "t1"})
    assert result["chapters"] == []
    assert result["chapter_count"] == 0
