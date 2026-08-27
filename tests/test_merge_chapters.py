"""merge_chapters is the only code here that can destroy a Tonie, so it is
pure and it is covered hard."""
from __future__ import annotations

import pytest

from app.push import StaleChapters, merge_chapters


def chapters() -> list[dict]:
    """Three chapters shaped the way the Tonie Cloud returns them."""
    return [
        {"id": "a", "title": "One", "file": "f-a", "seconds": 60.0, "transcoding": False},
        {"id": "b", "title": "Two", "file": "f-b", "seconds": 70.0, "transcoding": False},
        {"id": "c", "title": "Three", "file": "f-c", "seconds": 80.0, "transcoding": True},
    ]


def test_reorder_preserves_every_other_field():
    result = merge_chapters(
        chapters(),
        ["a", "b", "c"],
        [{"id": "c", "title": "Three"}, {"id": "a", "title": "One"}, {"id": "b", "title": "Two"}],
    )
    assert [c["id"] for c in result] == ["c", "a", "b"]
    assert [c["file"] for c in result] == ["f-c", "f-a", "f-b"]
    assert result[0]["seconds"] == 80.0
    assert result[0]["transcoding"] is True


def test_title_override_applies():
    result = merge_chapters(
        chapters(), ["a", "b", "c"],
        [{"id": "a", "title": "  Renamed  "}, {"id": "b", "title": "Two"},
         {"id": "c", "title": "Three"}],
    )
    assert result[0]["title"] == "Renamed"
    assert result[0]["file"] == "f-a"


def test_title_truncated_at_128():
    result = merge_chapters(
        chapters(), ["a", "b", "c"],
        [{"id": "a", "title": "x" * 500}, {"id": "b", "title": "Two"},
         {"id": "c", "title": "Three"}],
    )
    assert len(result[0]["title"]) == 128


def test_empty_title_keeps_the_current_one():
    """A slipped keystroke must not leave a nameless chapter on a Tonie."""
    result = merge_chapters(
        chapters(), ["a", "b", "c"],
        [{"id": "a", "title": "   "}, {"id": "b", "title": "Two"},
         {"id": "c", "title": "Three"}],
    )
    assert result[0]["title"] == "One"


def test_omitted_id_is_dropped():
    result = merge_chapters(
        chapters(), ["a", "b", "c"],
        [{"id": "a", "title": "One"}, {"id": "c", "title": "Three"}],
    )
    assert [c["id"] for c in result] == ["a", "c"]


def test_empty_request_clears():
    assert merge_chapters(chapters(), ["a", "b", "c"], []) == []


def test_stale_when_the_tonie_gained_a_chapter():
    """Another client added a chapter. The whole-list write would destroy it."""
    with pytest.raises(StaleChapters):
        merge_chapters(chapters(), ["a", "b"], [{"id": "a", "title": "One"}])


def test_stale_when_the_tonie_lost_a_chapter():
    with pytest.raises(StaleChapters):
        merge_chapters(chapters(), ["a", "b", "c", "d"], [{"id": "a", "title": "One"}])


def test_duplicate_requested_id_raises():
    with pytest.raises(ValueError):
        merge_chapters(
            chapters(), ["a", "b", "c"],
            [{"id": "a", "title": "One"}, {"id": "a", "title": "One again"}],
        )


def test_requested_id_outside_base_ids_raises():
    with pytest.raises(ValueError):
        merge_chapters(chapters(), ["a", "b", "c"], [{"id": "zzz", "title": "Ghost"}])


def test_stale_is_checked_before_anything_else():
    """Order matters: a stale list must 409, not 400 on a bad id inside it."""
    with pytest.raises(StaleChapters):
        merge_chapters(chapters(), ["a", "b"], [{"id": "zzz", "title": "Ghost"}])
