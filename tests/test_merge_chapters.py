"""merge_chapters is the only code here that can destroy a Tonie, so it is
pure and it is covered hard."""
from __future__ import annotations

import pytest

from app.push import StaleChapters, merge_chapters


def chapters() -> list[dict]:
    """Three chapters shaped the way the Tonie Cloud returns them.

    `futureRequired` is a sentinel this code has never heard of. It is here so
    that a merge which rebuilt chapters from a whitelist of known keys would
    fail a test instead of quietly stripping whatever the Tonie Cloud adds next.
    """
    return [
        {"id": "a", "title": "One", "file": "f-a", "seconds": 60.0,
         "transcoding": False, "futureRequired": "keep-me-a"},
        {"id": "b", "title": "Two", "file": "f-b", "seconds": 70.0,
         "transcoding": False, "futureRequired": "keep-me-b"},
        {"id": "c", "title": "Three", "file": "f-c", "seconds": 80.0,
         "transcoding": True, "futureRequired": "keep-me-c"},
    ]


def base() -> list[dict]:
    """What the browser had on screen: ids and titles, nothing else."""
    return [{"id": c["id"], "title": c["title"]} for c in chapters()]


def test_reorder_preserves_every_other_field():
    result = merge_chapters(
        chapters(),
        base(),
        [{"id": "c", "title": "Three"}, {"id": "a", "title": "One"}, {"id": "b", "title": "Two"}],
    )
    assert [c["id"] for c in result] == ["c", "a", "b"]
    assert [c["file"] for c in result] == ["f-c", "f-a", "f-b"]
    assert result[0]["seconds"] == 80.0
    assert result[0]["transcoding"] is True
    # The unrecognised field survives, and survives with its value intact.
    assert [c["futureRequired"] for c in result] == ["keep-me-c", "keep-me-a", "keep-me-b"]
    # Nothing else moved either: with the titles unchanged, each merged chapter
    # is the current chapter, key for key.
    original = {c["id"]: c for c in chapters()}
    assert result == [original["c"], original["a"], original["b"]]


def test_a_renamed_chapter_keeps_its_unknown_fields():
    """The one field this endpoint writes is `title`. Overriding it must not
    turn the chapter into a fresh dict built from the keys we happen to know."""
    result = merge_chapters(
        chapters(), base(),
        [{"id": "a", "title": "Renamed"}, {"id": "b", "title": "Two"},
         {"id": "c", "title": "Three"}],
    )
    assert result[0] == dict(chapters()[0], title="Renamed")


def test_title_override_applies():
    result = merge_chapters(
        chapters(), base(),
        [{"id": "a", "title": "  Renamed  "}, {"id": "b", "title": "Two"},
         {"id": "c", "title": "Three"}],
    )
    assert result[0]["title"] == "Renamed"
    assert result[0]["file"] == "f-a"


def test_title_truncated_at_128():
    result = merge_chapters(
        chapters(), base(),
        [{"id": "a", "title": "x" * 500}, {"id": "b", "title": "Two"},
         {"id": "c", "title": "Three"}],
    )
    assert len(result[0]["title"]) == 128


def test_empty_title_keeps_the_current_one():
    """A slipped keystroke must not leave a nameless chapter on a Tonie."""
    result = merge_chapters(
        chapters(), base(),
        [{"id": "a", "title": "   "}, {"id": "b", "title": "Two"},
         {"id": "c", "title": "Three"}],
    )
    assert result[0]["title"] == "One"


def test_omitted_id_is_dropped():
    result = merge_chapters(
        chapters(), base(),
        [{"id": "a", "title": "One"}, {"id": "c", "title": "Three"}],
    )
    assert [c["id"] for c in result] == ["a", "c"]


def test_empty_request_clears():
    assert merge_chapters(chapters(), base(), []) == []


def test_reordering_alone_is_not_stale():
    """Order is deliberately outside the precondition: reordering is what this
    endpoint is for, and the last writer wins on order alone."""
    shuffled = list(reversed(base()))
    result = merge_chapters(chapters(), shuffled, [{"id": "a", "title": "One"}])
    assert [c["id"] for c in result] == ["a"]


def test_stale_when_the_tonie_gained_a_chapter():
    """Another client added a chapter. The whole-list write would destroy it."""
    with pytest.raises(StaleChapters):
        merge_chapters(chapters(), base()[:2], [{"id": "a", "title": "One"}])


def test_stale_when_the_tonie_lost_a_chapter():
    with pytest.raises(StaleChapters):
        merge_chapters(
            chapters(), base() + [{"id": "d", "title": "Four"}],
            [{"id": "a", "title": "One"}],
        )


def test_stale_when_base_and_current_differ_at_equal_length():
    """Equal counts, different ids. A length check would let this through and
    the PATCH would delete the concurrently added chapter `c`."""
    with pytest.raises(StaleChapters):
        merge_chapters(
            chapters(),
            [{"id": "a", "title": "One"}, {"id": "b", "title": "Two"},
             {"id": "d", "title": "Four"}],
            [{"id": "a", "title": "One"}],
        )


def test_stale_when_a_chapter_was_renamed_elsewhere():
    """The concurrent-rename case. The ids match on both sides, so an id-only
    guard accepts the write and sends `a` back under the title the browser
    remembers, silently reverting the other client's rename."""
    current = chapters()
    current[0]["title"] = "Alpha"
    with pytest.raises(StaleChapters):
        merge_chapters(
            current, base(),
            [{"id": "a", "title": "One"}, {"id": "b", "title": "Beta"},
             {"id": "c", "title": "Three"}],
        )


def test_a_missing_title_matches_an_empty_one():
    """The Tonie Cloud can report `title: None`, while describe_tonie hands the
    browser "". Both sides normalise, or such a chapter would 409 every save."""
    current = [{"id": "a", "title": None, "file": "f-a", "seconds": 60.0}]
    result = merge_chapters(
        current, [{"id": "a", "title": ""}], [{"id": "a", "title": "Named"}],
    )
    assert result[0]["title"] == "Named"


def test_duplicate_requested_id_raises():
    with pytest.raises(ValueError):
        merge_chapters(
            chapters(), base(),
            [{"id": "a", "title": "One"}, {"id": "a", "title": "One again"}],
        )


def test_requested_id_outside_base_raises():
    with pytest.raises(ValueError):
        merge_chapters(chapters(), base(), [{"id": "zzz", "title": "Ghost"}])


def test_stale_is_checked_before_anything_else():
    """Order matters: a stale list must 409, not 400 on a bad id inside it."""
    with pytest.raises(StaleChapters):
        merge_chapters(chapters(), base()[:2], [{"id": "zzz", "title": "Ghost"}])


def test_an_untouched_title_is_never_rewritten():
    """A save must not strip or truncate a title the user did not edit.

    The myTonies app can create a title with leading whitespace or past 128
    characters. Reordering one chapter would otherwise silently rewrite all
    twelve, and there is no undo.
    """
    long_title = "x" * 200
    current = [
        {"id": "a", "title": "  Spaced  ", "file": "f-a", "seconds": 60.0},
        {"id": "b", "title": long_title, "file": "f-b", "seconds": 70.0},
    ]
    base = [{"id": "a", "title": "  Spaced  "}, {"id": "b", "title": long_title}]
    # A pure reorder: both titles come back exactly as they went out.
    result = merge_chapters(current, base, [
        {"id": "b", "title": long_title},
        {"id": "a", "title": "  Spaced  "},
    ])
    assert result[0]["title"] == long_title
    assert result[1]["title"] == "  Spaced  "


def test_a_changed_title_is_still_stripped_and_capped():
    current = [{"id": "a", "title": "One", "file": "f-a", "seconds": 60.0}]
    base = [{"id": "a", "title": "One"}]
    result = merge_chapters(current, base, [{"id": "a", "title": "  " + "y" * 300}])
    assert result[0]["title"] == "y" * 128
