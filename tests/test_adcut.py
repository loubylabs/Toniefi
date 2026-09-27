from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app import adcut

R = adcut.RATE


def tones(seconds: float, seed: int) -> np.ndarray:
    """Structured audio: a run of short random two-tone notes."""
    rng = np.random.default_rng(seed)
    out, total = [], 0
    while total < seconds * R:
        length = int(rng.uniform(0.08, 0.4) * R)
        t = np.arange(length) / R
        note = (np.sin(2 * np.pi * rng.uniform(150, 3000) * t) * rng.uniform(0.2, 1.0)
                + 0.5 * np.sin(2 * np.pi * rng.uniform(150, 3000) * t))
        out.append(note)
        total += length
    return (np.concatenate(out)[: int(seconds * R)] * 8000).astype(np.float32)


def copy(samples: np.ndarray, seed: int) -> np.ndarray:
    """Stand-in for a lossy re-encode: a gain change and a little noise."""
    rng = np.random.default_rng(seed)
    return samples * 0.8 + rng.normal(0, 40, len(samples)).astype(np.float32)


def at(seconds: float) -> int:
    return int(seconds * R)


STORY = tones(300, 1)
AD = tones(20, 2)
OTHER_AD = tones(15, 3)


def with_ad(story: np.ndarray, where: float, ad: np.ndarray) -> np.ndarray:
    return np.concatenate([story[: at(where)], ad, story[at(where):]])


def compare(library: np.ndarray, fresh: np.ndarray) -> adcut.Finding:
    return adcut.compare(copy(library, 5), copy(fresh, 6))


def assert_cuts(found: adcut.Finding, expected: list[tuple[float, float]]) -> None:
    assert len(found.cuts) == len(expected), found
    for (start, end), (want_start, want_end) in zip(found.cuts, expected):
        assert start == pytest.approx(want_start, abs=0.5)
        assert end == pytest.approx(want_end, abs=0.5)


def test_an_ad_only_the_library_has_is_found_where_it_sits():
    assert_cuts(compare(with_ad(STORY, 120, AD), STORY), [(120, 140)])


def test_two_ads_are_both_found():
    library = np.concatenate([STORY[: at(60)], AD, STORY[at(60):at(200)], OTHER_AD, STORY[at(200):]])
    assert_cuts(compare(library, STORY), [(60, 80), (220, 235)])


def test_different_ads_in_the_same_spot_cut_the_library_one_whole():
    assert_cuts(compare(with_ad(STORY, 120, AD), with_ad(STORY, 120, OTHER_AD)), [(120, 140)])


def test_a_preroll_and_a_postroll_are_found():
    assert_cuts(compare(np.concatenate([AD, STORY]), STORY), [(0, 20)])
    assert_cuts(compare(np.concatenate([STORY, AD]), STORY), [(300, 320)])


def test_an_ad_only_the_fresh_copy_has_cuts_nothing():
    found = compare(STORY, with_ad(STORY, 120, AD))
    assert found.cuts == []
    assert found.matched_fraction > 0.9


def test_no_cut_when_copies_match():
    assert compare(STORY, STORY).cuts == []


def test_a_library_track_trimmed_at_the_start_cuts_nothing():
    assert compare(STORY[at(10):], STORY).cuts == []


def test_a_split_part_is_aligned_against_the_whole_episode():
    part = with_ad(STORY, 120, AD)[at(100):at(250)]
    assert_cuts(compare(part, STORY), [(20, 40)])


def test_unrelated_audio_cuts_nothing():
    found = compare(STORY, tones(300, 9))
    assert found.cuts == []
    assert found.matched_fraction < adcut.MIN_MATCHED


def test_a_pause_in_the_story_is_never_cut():
    story = np.concatenate([STORY[: at(150)], np.zeros(at(8), np.float32), STORY[at(150):]])
    assert compare(story, story).cuts == []


def test_cuts_over_a_quarter_of_the_track_cut_nothing(monkeypatch):
    # Two 55 s ads in 410 s: enough match to pass MIN_MATCHED, too much cut.
    library = np.concatenate([STORY[: at(100)], tones(55, 4), STORY[at(100):at(200)], tones(55, 7), STORY[at(200):]])
    found = compare(library, STORY)
    assert found.matched_fraction >= adcut.MIN_MATCHED
    assert found.cuts == []
    monkeypatch.setattr(adcut, "MAX_CUT_SHARE", 0.5)
    assert len(compare(library, STORY).cuts) == 2


def test_audio_too_short_to_compare_cuts_nothing():
    assert compare(STORY[: at(5)], STORY).cuts == []


def test_cut_keeps_everything_outside_the_ranges_and_fades_each_join(monkeypatch, tmp_path):
    track = tmp_path / "one.mp3"
    track.write_bytes(b"audio")
    commands = []

    def run(cmd, timeout=3600):
        commands.append(cmd)
        Path(cmd[-1]).write_bytes(b"cut")

    monkeypatch.setattr(adcut.audio, "_run", run)
    monkeypatch.setattr(adcut.audio, "duration_seconds", lambda path: 100.0)

    adcut.cut(track, [(40.0, 50.0), (10.0, 20.0)])

    graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "atrim=start=0.000:end=10.000" in graph
    assert "atrim=start=20.000:end=40.000" in graph
    assert "atrim=start=50.000:end=100.000" in graph
    assert "concat=n=3:v=0:a=1[out]" in graph
    assert graph.count("afade=t=in") == 2
    assert graph.count("afade=t=out") == 2
    assert track.read_bytes() == b"cut"


def test_cut_with_nothing_to_cut_leaves_the_file(monkeypatch, tmp_path):
    track = tmp_path / "one.mp3"
    track.write_bytes(b"audio")
    monkeypatch.setattr(adcut.audio, "_run", lambda *a, **k: pytest.fail("ran ffmpeg"))

    adcut.cut(track, [])

    assert track.read_bytes() == b"audio"
