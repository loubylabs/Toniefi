"""ffmpeg/ffprobe helpers: probing, normalizing, splitting, and packing
tracks into Creative-Tonie-sized groups."""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import config

AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".m4b", ".aac", ".flac", ".wav", ".ogg", ".oga",
    ".opus", ".wma", ".aiff", ".aif",
}


class AudioError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=True
        )
    except FileNotFoundError as exc:
        raise AudioError(f"{cmd[0]} is not installed in this container.") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioError(f"{cmd[0]} timed out after {timeout}s.") from exc
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or "").strip().splitlines()[-6:]
        raise AudioError(f"{cmd[0]} failed: {' / '.join(tail)}") from exc


def probe(path: Path) -> dict:
    proc = _run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams",
        "-print_format", "json", str(path),
    ], timeout=120)
    return json.loads(proc.stdout)


def duration_seconds(path: Path) -> float:
    info = probe(path)
    raw = info.get("format", {}).get("duration")
    if raw is None:
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "audio" and stream.get("duration"):
                raw = stream["duration"]
                break
    if raw is None:
        raise AudioError(f"Could not determine the duration of {path.name}.")
    return float(raw)


def normalize(src: Path, dst: Path, title: str | None = None) -> Path:
    """Transcode to a consistent MP3. Creative Tonies accept many formats,
    but normalizing avoids odd containers from the web being rejected."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-i", str(src),
        "-vn", "-map_metadata", "0",
        "-c:a", "libmp3lame", "-b:a", config.AUDIO_BITRATE,
        "-ar", config.AUDIO_SAMPLE_RATE, "-ac", "2",
    ]
    if title:
        cmd += ["-metadata", f"title={title[:120]}"]
    cmd.append(str(dst))
    _run(cmd)
    return dst


def split(src: Path, out_dir: Path, max_seconds: int, stem: str | None = None) -> list[Path]:
    """Split one file into parts no longer than max_seconds.

    Re-encodes rather than stream-copying: copying cuts on the nearest frame
    boundary, which drifts on VBR audiobook rips and can clip a word.
    """
    total = duration_seconds(src)
    if total <= max_seconds:
        return [src]

    parts_needed = math.ceil(total / max_seconds)
    # Even out the parts so you don't end up with a 90-minute chunk and a
    # 4-minute orphan; 3 parts of 62 minutes beats 2x90 + 1x4.
    part_seconds = math.ceil(total / parts_needed)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or src.stem
    produced: list[Path] = []
    for index in range(parts_needed):
        start = index * part_seconds
        dst = out_dir / f"{slugify(stem)}-part{index + 1:02d}.mp3"
        _run([
            "ffmpeg", "-nostdin", "-y",
            "-ss", str(start), "-i", str(src), "-t", str(part_seconds),
            "-vn", "-c:a", "libmp3lame", "-b:a", config.AUDIO_BITRATE,
            "-ar", config.AUDIO_SAMPLE_RATE, "-ac", "2",
            "-metadata", f"title={stem[:100]} (part {index + 1})",
            str(dst),
        ])
        produced.append(dst)
    return produced


# ------------------------------------------------------------------ packing

@dataclass
class Track:
    path: Path
    title: str
    seconds: float

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "name": self.path.name,
            "title": self.title,
            "seconds": round(self.seconds, 1),
            "duration": human_duration(self.seconds),
        }


@dataclass
class Group:
    index: int
    tracks: list[Track] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return sum(t.seconds for t in self.tracks)

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "seconds": round(self.seconds, 1),
            "duration": human_duration(self.seconds),
            "tracks": [t.as_dict() for t in self.tracks],
        }


def pack(tracks: list[Track], limit: int | None = None) -> list[Group]:
    """Pack tracks into Tonie-sized groups, preserving order.

    Order matters for an audiobook, so this is sequential first-fit, not a
    bin-packing optimum: fill a Tonie until the next chapter would overflow
    it, then start the next one. Chapter 7 never lands before chapter 6.
    """
    limit = limit or config.usable_limit()
    groups: list[Group] = []
    current = Group(index=1)
    for track in tracks:
        if current.tracks and current.seconds + track.seconds > limit:
            groups.append(current)
            current = Group(index=len(groups) + 1)
        current.tracks.append(track)
    if current.tracks:
        groups.append(current)
    return groups


def oversized(tracks: list[Track], limit: int | None = None) -> list[Track]:
    """Tracks that cannot fit on a Tonie at all and must be split first."""
    limit = limit or config.usable_limit()
    return [t for t in tracks if t.seconds > limit]


# ------------------------------------------------------------------ helpers

def human_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {secs:02d}s"


def slugify(value: str, max_length: int = 60) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    value = re.sub(r"[-\s]+", "-", value)
    return value[:max_length].strip("-") or "untitled"


def have_tools() -> dict[str, bool]:
    return {name: shutil.which(name) is not None for name in ("ffmpeg", "ffprobe")}
