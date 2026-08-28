"""The Forge stage: turn raw extracted audio into something a kid will
actually enjoy listening to.

Four passes, each independently optional:
  - normalize   even out loudness so chapter 3 isn't twice as loud as chapter 2
  - clean       strip "FULL AUDIOBOOK [HD]" cruft out of track titles
  - trim        cut a fixed intro/outro off every track
  - split       break anything too long for one Tonie into even parts
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Callable

from . import audio, config, library

Progress = Callable[[str], None]


def _noop(_: str) -> None:
    return None


# ------------------------------------------------------------ title cleaning

# Junk that shows up in uploaded audiobook titles. Order matters a little:
# bracketed groups go first so the bare-word patterns don't strand brackets.
_BRACKETED = re.compile(
    r"[\(\[\{]\s*(?:"
    r"full\s+(?:length\s+)?audio\s?book|audio\s?book|unabridged|abridged|"
    r"complete|full\s+version|hd|hq|4k|1080p?|720p?|remastered|official|"
    r"free\s+audio\s?book|read\s+aloud|english"
    r")\s*[\)\]\}]",
    re.IGNORECASE,
)
_BARE = re.compile(
    r"(?:^|\s[|\-–—]\s*)(?:"
    r"full\s+(?:length\s+)?audio\s?book|audio\s?book|unabridged|complete\s+audio\s?book|"
    r"free\s+audio\s?book|full\s+audio|bedtime\s+story|read\s+aloud|"
    r"hd|hq|4k|1080p?|720p?|480p?|remastered|full\s+hd"
    r")\s*(?=$|[|\-–—])",
    re.IGNORECASE,
)
_TRAILING_JUNK = re.compile(r"\s*[|\-–—]\s*(?:youtube|topic)\s*$", re.IGNORECASE)
# The separator need not be followed by a space: yt-dlp names chapter files
# "001-Intro". The trailing \D guard is what keeps "24-7" from becoming "7".
_LEADING_INDEX = re.compile(r"^\s*\d{1,3}\s*[.\-–—:)]\s*(?=\D)")
_MULTISPACE = re.compile(r"\s{2,}")


def clean_title(raw: str, *, drop_leading_index: bool = False) -> str:
    """Strip the usual upload cruft, conservatively.

    Only removes patterns that are junk in every context. It will not try to
    guess that "Chapter 4" is redundant -- losing a real chapter name is worse
    than leaving a tidy-ish one. For the same reason a bare quality token is
    only dropped when a separator sets it apart: "Alice - HD" loses the HD,
    "The Hobbit in 4K" keeps it.

    The passes run in a loop because each substitution eats the separator that
    the next one needs: "Alice - Audiobook - HD" only collapses fully on the
    second lap.
    """
    title = raw or ""
    for _ in range(4):
        before = title
        title = _BRACKETED.sub(" ", title)
        title = _BARE.sub(" ", title)
        title = _TRAILING_JUNK.sub("", title)
        title = _MULTISPACE.sub(" ", title).strip()
        if title == before:
            break

    if drop_leading_index:
        title = _LEADING_INDEX.sub("", title)
    title = title.replace("_", " ")
    title = _MULTISPACE.sub(" ", title).strip(" -–—|:,.")
    return title or raw.strip() or "Untitled"


def strip_channel_prefix(title: str, uploader: str | None) -> str:
    """Drop a leading "Channel Name - " when it just repeats the uploader."""
    if not uploader:
        return title
    pattern = re.compile(rf"^\s*{re.escape(uploader)}\s*[|\-–—:]\s*", re.IGNORECASE)
    return pattern.sub("", title).strip() or title


# ------------------------------------------------------------- audio passes

# EBU R128 targets. -16 LUFS is the streaming-speech convention and sits in a
# comfortable place for a bedroom speaker; -1.5 dBTP leaves headroom so the
# Toniebox's own resampling can't clip.
LOUDNESS_TARGET = "I=-16:TP=-1.5:LRA=11"


def normalize_track(path: Path, progress: Progress = _noop) -> None:
    """Even out loudness in place, single-pass loudnorm."""
    tmp = path.with_name(path.stem + ".norm.mp3")
    audio._run([
        "ffmpeg", "-nostdin", "-y", "-i", str(path),
        "-af", f"loudnorm={LOUDNESS_TARGET}",
        "-c:a", "libmp3lame", "-b:a", config.AUDIO_BITRATE,
        "-ar", config.AUDIO_SAMPLE_RATE, "-ac", "2",
        "-map_metadata", "0",
        str(tmp),
    ])
    tmp.replace(path)


def trim_track(path: Path, head_seconds: float = 0, tail_seconds: float = 0) -> None:
    """Cut a fixed amount off the front and/or back of a track, in place."""
    if head_seconds <= 0 and tail_seconds <= 0:
        return
    total = audio.duration_seconds(path)
    keep = total - head_seconds - tail_seconds
    if keep <= 1:
        raise audio.AudioError(
            f"Trimming {path.name} by {head_seconds}s + {tail_seconds}s would "
            f"leave nothing of its {audio.human_duration(total)}."
        )
    tmp = path.with_name(path.stem + ".trim.mp3")
    cmd = ["ffmpeg", "-nostdin", "-y"]
    if head_seconds > 0:
        cmd += ["-ss", str(head_seconds)]
    cmd += ["-i", str(path), "-t", str(keep),
            "-c:a", "libmp3lame", "-b:a", config.AUDIO_BITRATE,
            "-ar", config.AUDIO_SAMPLE_RATE, "-ac", "2",
            "-map_metadata", "0", str(tmp)]
    audio._run(cmd)
    tmp.replace(path)


# ----------------------------------------------------------------- the forge

def run(
    slug: str,
    *,
    normalize: bool = True,
    clean_titles: bool = True,
    trim_head: float = 0,
    trim_tail: float = 0,
    split_oversized: bool = True,
    progress: Progress = _noop,
) -> dict[str, Any]:
    with library.collection_lease():
        library.recover_collection_publications()
        identity = f"manual-{slug}"
        stage = library.create_replacement_stage(slug, identity)
        try:
            _run_at_path(
                stage,
                normalize=normalize,
                clean_titles=clean_titles,
                trim_head=trim_head,
                trim_tail=trim_tail,
                split_oversized=split_oversized,
                progress=progress,
            )
            return library.publish_replacement(slug, stage, identity)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise


def run_collection_stage(
    stage_identity: str,
    *,
    normalize: bool = True,
    clean_titles: bool = True,
    trim_head: float = 0,
    trim_tail: float = 0,
    split_oversized: bool = True,
    progress: Progress = _noop,
) -> dict[str, Any]:
    """Forge immutable extracted staging and publish only complete output."""
    with library.collection_lease():
        published = library.find_published_stage(stage_identity)
        if published:
            return published
        forge_identity = f"prepare-{stage_identity}"
        stage = library.create_forge_stage_from_collection_stage(stage_identity, forge_identity)
        try:
            _run_at_path(
                stage,
                normalize=normalize,
                clean_titles=clean_titles,
                trim_head=trim_head,
                trim_tail=trim_tail,
                split_oversized=split_oversized,
                progress=progress,
            )
            return library.publish_forged_collection_stage(
                stage_identity,
                stage,
                forge_identity,
            )
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise


def _run_at_path(
    path: Path,
    *,
    normalize: bool,
    clean_titles: bool,
    trim_head: float,
    trim_tail: float,
    split_oversized: bool,
    progress: Progress,
) -> dict[str, Any]:
    manifest = library.get_at_path(path, refresh=True)
    if not manifest:
        raise RuntimeError("The Forge input collection is missing.")

    tracks = manifest["tracks"]
    total = len(tracks)

    if trim_head or trim_tail:
        for index, track in enumerate(tracks, start=1):
            progress(f"Trimming {index}/{total}: {track['title']}")
            trim_track(path / track["name"], trim_head, trim_tail)

    if normalize:
        for index, track in enumerate(tracks, start=1):
            progress(f"Levelling {index}/{total}: {track['title']}")
            normalize_track(path / track["name"])

    if clean_titles:
        progress("Cleaning titles")
        uploader = manifest.get("uploader")
        for track in tracks:
            cleaned = clean_title(track.get("title") or Path(track["name"]).stem)
            cleaned = strip_channel_prefix(cleaned, uploader)
            library.rename_track_at_path(path, track["name"], cleaned)

    manifest = library.get_at_path(path, refresh=True)

    if split_oversized:
        limit = config.usable_limit()
        over = [t for t in manifest["tracks"] if t.get("seconds", 0) > limit]
        for track in over:
            progress(f"Splitting {track['title']}")
            src = path / track["name"]
            parts = audio.split(src, path, limit, stem=Path(track["name"]).stem)
            if parts and parts[0] != src:
                src.unlink()
                library.replace_track_at_path(path, track["name"], [p.name for p in parts])

    progress("Re-probing")
    library.get_at_path(path, refresh=True)
    return library.set_forge_state_at_path(path, {
        "normalized": normalize,
        "titles_cleaned": clean_titles,
        "trim_head": trim_head,
        "trim_tail": trim_tail,
        "split": split_oversized,
    })
