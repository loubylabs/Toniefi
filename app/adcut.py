"""Find and cut podcast ads by comparing a library track with a fresh copy.

Podcast hosts stitch ads into an episode when it is downloaded, and change
them over hours or days. The story itself is the same in every copy. So a
stretch of the library track that the fresh copy does not contain is an ad,
and cutting only those stretches can never remove story the fresh copy has.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import audio, config

RATE = 8000
WINDOW = 800  # samples: each frame hears 100 ms
HOP = 160  # samples: a frame starts every 20 ms
BANDS = 16
STEP = HOP / RATE


def _frames(seconds: float) -> int:
    return round(seconds / STEP)


# Frames overlap so that two copies offset by any amount line up within half
# a hop; with back-to-back frames a half-frame offset ruins the match.
SMOOTH = _frames(3.0)
MATCH = 0.8  # smoothed correlation that marks a matching stretch
HOLD = 0.6  # per-frame correlation that still counts at a stretch's edge
# Frame loudness against the file's median. The library copy is levelled
# and the fresh one is not, so fixed thresholds would disagree on quiet bits.
QUIET = 0.03  # below this share of the median, a frame is silence
LOUD = 0.12  # above this share, a frame is surely sound
MIN_ANCHOR = _frames(10.0)
MAX_LAGS = 8
LAG_SPACING = _frames(2.0)
MARGIN = _frames(3.0)  # how far smoothing can blur a stretch's edge

MIN_MATCHED = 0.7
MIN_CUT = 5.0
MAX_CUT = 180.0
MAX_CUT_SHARE = 0.25
FADE = 0.02


@dataclass(frozen=True)
class Finding:
    matched_fraction: float
    cuts: list[tuple[float, float]] = field(default_factory=list)


@dataclass(frozen=True)
class _Anchor:
    start: int
    end: int
    lag: int  # library frame minus fresh frame


def decode(path: Path) -> np.ndarray:
    """Mono samples at RATE, the only input the comparison needs."""
    try:
        raw = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
             "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
            capture_output=True, check=True, timeout=1800,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise audio.AudioError(f"Could not decode {path.name}.") from exc
    return np.frombuffer(raw, np.int16).astype(np.float32)


def _features(samples: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per frame: 16 log band energies, centred and scaled to unit length so
    the dot product of two frames is their correlation; the raw log band
    energies, which the lag search uses; and a level per frame, -1 silent,
    1 surely sound, 0 in between."""
    count = 1 + (len(samples) - WINDOW) // HOP if len(samples) >= WINDOW else 0
    if count == 0:
        empty = np.zeros((0, BANDS), np.float32)
        return empty, empty, np.zeros(0, np.int8)
    frames = np.lib.stride_tricks.sliding_window_view(samples, WINDOW)[::HOP][:count]
    rms = np.sqrt((frames ** 2).mean(axis=1))
    median = max(float(np.median(rms)), 1.0)
    level = np.zeros(count, np.int8)
    level[rms < QUIET * median] = -1
    level[rms > LOUD * median] = 1
    frames = frames * np.hanning(WINDOW)
    spectrum = np.abs(np.fft.rfft(frames, axis=1))[:, 1:]
    edges = np.geomspace(1, spectrum.shape[1], BANDS + 1).astype(int)
    bands = np.stack(
        [spectrum[:, edges[i]:max(edges[i + 1], edges[i] + 1)].sum(1) for i in range(BANDS)],
        axis=1,
    )
    logs = np.log(bands + 1e-3)
    # Speech shares one overall spectral tilt, which makes any two frames
    # look alike. Removing each band's average over the file leaves what
    # changes from moment to moment, which is what identifies a moment.
    centred = logs - logs.mean(axis=0)
    centred = centred - centred.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(centred, axis=1, keepdims=True)
    return (centred / np.maximum(norms, 1e-9)).astype(np.float32), logs, level


def _candidate_lags(lib_logs: np.ndarray, fresh_logs: np.ndarray) -> list[int]:
    """The strongest distinct offsets between the copies.

    Each band's energy over time is cross-correlated and the results summed.
    Only local peaks count, so the shoulders of one strong offset cannot
    crowd out a second offset, which is exactly where an ad shifts the story.
    """
    def standardize(values: np.ndarray) -> np.ndarray:
        centred = values - values.mean(axis=0)
        return centred / (centred.std(axis=0) + 1e-9)

    a, b = standardize(lib_logs), standardize(fresh_logs)
    size = 1 << (len(a) + len(b) - 1).bit_length()
    spectra = np.fft.rfft(a, size, axis=0) * np.conj(np.fft.rfft(b, size, axis=0))
    score = np.fft.irfft(spectra, size, axis=0).sum(axis=1)
    lags = np.arange(size)
    lags[lags > size // 2] -= size
    order = np.argsort(lags)
    lags, score = lags[order], score[order]
    padded = np.concatenate([np.full(LAG_SPACING, -np.inf), score, np.full(LAG_SPACING, -np.inf)])
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * LAG_SPACING + 1)
    peaks = np.flatnonzero(score >= windows.max(axis=1))
    strongest = peaks[np.argsort(-score[peaks])][:MAX_LAGS]
    return [int(lags[index]) for index in strongest]


def _similarity(lib: np.ndarray, fresh: np.ndarray, lib_level: np.ndarray,
                fresh_level: np.ndarray, lag: int) -> np.ndarray:
    """Per library frame t, the correlation of lib[t] with fresh[t - lag].

    Silence has no spectrum to correlate, so silence against silence counts
    as a full match, or a pause in the story would look like an ad, and
    silence against sure sound as a full mismatch, or room noise would match
    an ad's music bed.
    """
    out = np.full(len(lib), -1.0, np.float32)
    start, stop = max(0, lag), min(len(lib), len(fresh) + lag)
    if stop > start:
        out[start:stop] = np.einsum("ij,ij->i", lib[start:stop], fresh[start - lag:stop - lag])
        product = lib_level[start:stop] * fresh_level[start - lag:stop - lag]
        both_silent = (lib_level[start:stop] == -1) & (product == 1)
        out[start:stop][both_silent] = 1.0
        out[start:stop][product == -1] = -1.0
    return out


def _smooth(values: np.ndarray) -> np.ndarray:
    return np.convolve(values, np.ones(SMOOTH) / SMOOTH, "same")


def _anchors(smoothed: dict[int, np.ndarray]) -> list[_Anchor]:
    """The longest chain of matching stretches that moves forward through
    both copies.

    Stretches are found per lag, not from whichever lag matches best frame by
    frame: a song's repeated chorus matches at two lags, and a frame-by-frame
    winner would chop the true stretch into pieces too short to keep.
    """
    runs: list[_Anchor] = []
    for lag, values in smoothed.items():
        above = np.concatenate([[False], values > MATCH, [False]])
        edges = np.flatnonzero(above[1:] != above[:-1])
        for start, end in zip(edges[::2], edges[1::2]):
            if end - start >= MIN_ANCHOR:
                runs.append(_Anchor(int(start), int(end), lag))
    runs.sort(key=lambda run: run.start)
    best: list[int] = []
    before: list[int | None] = []
    for index, run in enumerate(runs):
        best.append(run.end - run.start)
        before.append(None)
        for prior in range(index):
            earlier = runs[prior]
            # Smoothing stretches each end outward, so neighbours may overlap
            # by a blurred edge on each side.
            follows = (
                earlier.end - 2 * MARGIN <= run.start
                and earlier.end - earlier.lag - 2 * MARGIN <= run.start - run.lag
            )
            if follows and best[prior] + run.end - run.start > best[index]:
                best[index] = best[prior] + run.end - run.start
                before[index] = prior
    if not runs:
        return []
    chain: list[_Anchor] = []
    at: int | None = int(np.argmax(best))
    while at is not None:
        chain.append(runs[at])
        at = before[at]
    return chain[::-1]


def _edges(anchor: _Anchor, raw: np.ndarray, low: int, high: int) -> tuple[int, int]:
    """Sharpen a stretch's smoothed edges to single frames.

    Smoothing blurs an edge by up to MARGIN either way. Within that window the
    edge goes where frames before it (for the start, after it) match best on
    balance: each frame scores its correlation minus HOLD.
    """
    gain = raw - HOLD
    lo = max(low, anchor.start - MARGIN)
    mid = min(anchor.start + MARGIN, anchor.end)
    # Start s maximises the sum of gain over [s, mid).
    suffix = np.concatenate([np.cumsum(gain[lo:mid][::-1])[::-1], [0.0]])
    start = lo + int(np.argmax(suffix))
    mid = max(anchor.end - MARGIN, start)
    hi = min(high, anchor.end + MARGIN)
    # End e maximises the sum of gain over [mid, e).
    prefix = np.concatenate([[0.0], np.cumsum(gain[mid:hi])])
    end = mid + int(np.argmax(prefix))
    return start, end


def _place(first: _Anchor, second: _Anchor, low: int, high: int,
           raw_first: np.ndarray, raw_second: np.ndarray) -> tuple[int, int]:
    """Place audio only the library has, whose length the lag change gives.

    The cut goes where the frames before it best match at the first lag and
    the frames after it at the second. This beats sharpening each edge alone,
    which an ad over the show's own music can fool by seconds.
    """
    extra = second.lag - first.lag
    before = np.concatenate([[0.0], np.cumsum(raw_first[low:high])])
    after = np.concatenate([[0.0], np.cumsum(raw_second[low:high])])
    offsets = np.arange(0, high - low - extra + 1)
    score = before[offsets] + (after[-1] - after[offsets + extra])
    start = low + int(offsets[np.argmax(score)])
    return start, start + extra


def find_extra(library_track: Path, fresh: Path) -> Finding:
    """Where the library track has audio the fresh copy does not, in seconds."""
    return compare(decode(library_track), decode(fresh))


def compare(library_samples: np.ndarray, fresh_samples: np.ndarray) -> Finding:
    lib, lib_logs, lib_level = _features(library_samples)
    new, new_logs, new_level = _features(fresh_samples)
    if len(lib) < MIN_ANCHOR or len(new) < MIN_ANCHOR:
        return Finding(0.0)
    lags = _candidate_lags(lib_logs, new_logs)
    raw = {lag: _similarity(lib, new, lib_level, new_level, lag) for lag in lags}
    anchors = _anchors({lag: _smooth(values) for lag, values in raw.items()})
    matched = sum(a.end - a.start for a in anchors) / len(lib)
    if not anchors or matched < MIN_MATCHED:
        return Finding(round(matched, 3))

    covered: list[tuple[int, int]] = []
    for index, anchor in enumerate(anchors):
        low = covered[-1][1] if covered else 0
        high = anchors[index + 1].start if index + 1 < len(anchors) else len(lib)
        covered.append(_edges(anchor, raw[anchor.lag], low, high))
    # Whatever the stretches leave uncovered is audio the fresh copy lacks.
    frames: list[tuple[int, int]] = []
    if covered[0][0] > 0:
        frames.append((0, covered[0][0]))
    for index in range(len(anchors) - 1):
        first, second = anchors[index], anchors[index + 1]
        end, start = covered[index][1], covered[index + 1][0]
        fresh_gap = (start - second.lag) - (end - first.lag)
        extra = second.lag - first.lag
        if fresh_gap < _frames(MIN_CUT) and extra > 0:
            # Search around the gap, widened to fit the whole extra audio,
            # since each edge may sit up to a margin off on either side.
            middle = (end + start) // 2
            reach = (extra + 1) // 2 + 2 * MARGIN
            low = max(first.start, min(end - MARGIN, middle - reach))
            high = min(second.end, max(start + MARGIN, middle + reach))
            if high - low >= extra:
                frames.append(_place(first, second, low, high, raw[first.lag], raw[second.lag]))
                continue
        if start > end:
            frames.append((end, start))
    if covered[-1][1] < len(lib):
        frames.append((covered[-1][1], len(lib)))

    cuts = [(s * STEP, e * STEP) for s, e in frames if MIN_CUT <= (e - s) * STEP <= MAX_CUT]
    if sum(e - s for s, e in cuts) > MAX_CUT_SHARE * len(lib) * STEP:
        return Finding(round(matched, 3))
    return Finding(round(matched, 3), [(round(s, 2), round(e, 2)) for s, e in cuts])


def cut(path: Path, cuts: list[tuple[float, float]]) -> None:
    """Rewrite a track in place without the cut ranges, fading each join."""
    if not cuts:
        return
    total = audio.duration_seconds(path)
    keeps: list[tuple[float, float]] = []
    at = 0.0
    for start, end in sorted(cuts):
        if start > at:
            keeps.append((at, start))
        at = max(at, end)
    if at < total:
        keeps.append((at, total))
    parts = []
    for index, (start, end) in enumerate(keeps):
        chain = f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS"
        if index > 0:
            chain += f",afade=t=in:d={FADE}"
        if index < len(keeps) - 1:
            chain += f",afade=t=out:st={max(0.0, end - start - FADE):.3f}:d={FADE}"
        parts.append(f"{chain}[k{index}]")
    joined = "".join(f"[k{index}]" for index in range(len(keeps)))
    graph = ";".join(parts) + f";{joined}concat=n={len(keeps)}:v=0:a=1[out]"
    tmp = path.with_name(path.stem + ".adcut.mp3")
    audio._run([
        "ffmpeg", "-nostdin", "-y", "-i", str(path),
        "-filter_complex", graph, "-map", "[out]",
        "-c:a", "libmp3lame", "-b:a", config.AUDIO_BITRATE,
        "-ar", config.AUDIO_SAMPLE_RATE, "-ac", "2",
        "-map_metadata", "0", str(tmp),
    ])
    tmp.replace(path)
