"""The Extract stage: getting audio and metadata into the library."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import httpx

from . import audio, config, forge, library

Progress = Callable[[str], None]

LIBRIVOX_API = "https://librivox.org/api/feed/audiobooks/"
USER_AGENT = "toniefi/1.0 (self-hosted personal library tool)"


def _noop(_: str) -> None:
    return None


# ---------------------------------------------------------------- LibriVox

def librivox_search(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search LibriVox by title. Everything there is public domain, which is
    why it sits alongside URL ingest rather than behind it."""
    params = {
        "format": "json",
        "extended": "1",
        "limit": str(limit),
        "title": f"^{query}" if query else "",
    }
    try:
        resp = httpx.get(LIBRIVOX_API, params=params, timeout=45.0,
                         headers={"User-Agent": USER_AGENT})
        # LibriVox answers "nothing matched" with 404 and an {"error": ...}
        # body, not an empty list. Raising there turns an ordinary empty
        # search into a stack trace in the UI.
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        books = resp.json().get("books", [])
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LibriVox search failed: {exc}") from exc
    except ValueError:
        return []

    results = []
    for book in books:
        sections = book.get("sections") or []
        total = float(book.get("totaltimesecs") or 0)
        results.append({
            "id": book.get("id"),
            "title": book.get("title", "Untitled"),
            "authors": _authors(book),
            "language": book.get("language", ""),
            "num_sections": int(book.get("num_sections") or len(sections)),
            "total_seconds": total,
            "total_duration": audio.human_duration(total),
            "tonies_needed": max(1, -(-int(total) // config.usable_limit())) if total else 0,
            "url_librivox": book.get("url_librivox", ""),
            "description": _strip_html(book.get("description", ""))[:400],
        })
    return results


def _authors(book: dict[str, Any]) -> str:
    return ", ".join(
        f"{a.get('first_name', '')} {a.get('last_name', '')}".strip()
        for a in (book.get("authors") or [])
    ).strip(", ")


def librivox_sections(book_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    params = {"format": "json", "extended": "1", "id": str(book_id)}
    resp = httpx.get(LIBRIVOX_API, params=params, timeout=45.0,
                     headers={"User-Agent": USER_AGENT})
    if resp.status_code != 404:
        resp.raise_for_status()
    books = [] if resp.status_code == 404 else resp.json().get("books", [])
    if not books:
        raise RuntimeError(f"LibriVox has no book with id {book_id}.")
    book = books[0]
    sections = [s for s in (book.get("sections") or []) if s.get("listen_url")]
    if not sections:
        raise RuntimeError(f"{book.get('title', 'That book')} has no downloadable sections.")
    return book, sections


def import_librivox(
    book_id: str,
    *,
    stage_id: str,
    progress: Progress = _noop,
) -> dict[str, Any]:
    published = library.find_published_stage(stage_id)
    if published:
        return published
    book, sections = librivox_sections(book_id)
    with library.collection_lease():
        title = book.get("title", f"LibriVox {book_id}")
        stage = library.begin_collection_stage(stage_id, title=title, source="librivox", extra={
            "author": _authors(book),
            "librivox_id": book_id,
            "url": book.get("url_librivox", ""),
            "license": "Public domain (LibriVox)",
        })
        dest = stage.path

        total = len(sections)
        with httpx.Client(timeout=300.0, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as client:
            for position, section in enumerate(sections, start=1):
                label = section.get("title") or f"Section {position}"
                filename = f"{position:03d}-{audio.slugify(label)}.mp3"
                target = dest / filename
                if not target.is_file():
                    progress(f"Downloading {position}/{total}: {label}")
                    _stream_download(client, section["listen_url"], target)

        # LibriVox section titles are already clean; keep them as the track titles.
        library.rescan_collection_stage(stage_id)
        for position, section in enumerate(sections, start=1):
            label = section.get("title") or f"Section {position}"
            library.rename_track_at_path(
                dest,
                f"{position:03d}-{audio.slugify(label)}.mp3",
                label,
            )

        progress("Probing durations")
        library.collection_stage(stage_id, refresh=True)
        return library.complete_collection_stage(stage_id)


def _stream_download(client: httpx.Client, url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with client.stream("GET", url) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(chunk_size=1 << 16):
                fh.write(chunk)
    tmp.rename(dest)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


# --------------------------------------------------------------- URL ingest

def _player_client_args() -> list[str]:
    """Ask yt-dlp for a working YouTube client. See config.YTDLP_PLAYER_CLIENTS."""
    if not config.YTDLP_PLAYER_CLIENTS:
        return []
    return ["--extractor-args",
            f"youtube:player_client={config.YTDLP_PLAYER_CLIENTS}"]


def _playlist_items_spec(items: list[int]) -> str:
    """Compact picked entry numbers into yt-dlp's --playlist-items grammar.

    [1, 3, 4, 5] becomes "1,3:5", which keeps the argument short on a long
    playlist where the user unticked only a handful of entries. START:STOP is
    the current range syntax and includes both ends; START-STOP means the same
    but survives only as a backward-compatible spelling.
    """
    ordered = sorted({int(item) for item in items if int(item) > 0})
    spans: list[str] = []
    start = previous = None
    for number in ordered:
        if start is None:
            start = previous = number
            continue
        if number == previous + 1:
            previous = number
            continue
        spans.append(_span(start, previous))
        start = previous = number
    if start is not None:
        spans.append(_span(start, previous))
    return ",".join(spans)


def _span(start: int, end: int) -> str:
    return str(start) if start == end else f"{start}:{end}"


def playlist_preview(url: str) -> dict[str, Any]:
    """List a playlist's entries without downloading any audio.

    `--flat-playlist` asks the site for the index page only, so a hundred-video
    playlist answers in a second and costs nothing. Entry numbers count every
    position, including videos the site refuses to serve, because that is what
    `--playlist-items` counts when the download runs.
    """
    if not shutil.which("yt-dlp"):
        raise RuntimeError("yt-dlp is not installed in this container.")
    cmd = [
        "yt-dlp",
        "--flat-playlist", "--dump-single-json",
        "--no-warnings", "--ignore-no-formats-error",
        *_player_client_args(),
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError(f"yt-dlp could not read that playlist: {' / '.join(tail) or 'unknown error'}")
    try:
        info = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as error:
        raise RuntimeError("yt-dlp returned something that was not a playlist.") from error
    return {
        "title": info.get("title") or "",
        "entries": [_preview_entry(index, entry)
                    for index, entry in enumerate(info.get("entries") or [], start=1)],
    }


def _preview_entry(index: int, entry: dict[str, Any] | None) -> dict[str, Any]:
    """A private or deleted video still holds its place in the numbering."""
    entry = entry or {}
    return {
        "index": index,
        "id": entry.get("id") or "",
        "title": entry.get("title") or f"Video {index}",
        "available": bool(entry.get("id")) and entry.get("title") not in ("[Private video]", "[Deleted video]"),
    }


def import_url(
    url: str,
    *,
    stage_id: str,
    use_chapters: bool = True,
    playlist_items: list[int] | None = None,
    progress: Progress = _noop,
) -> dict[str, Any]:
    """Extract audio from a URL with yt-dlp, keeping metadata and cover art.

    Point this at material you have the right to use -- your own recordings,
    Creative Commons uploads, or public domain works. Most "full audiobook"
    uploads on video sites are unlicensed rips of commercial recordings, and
    the LibriVox path above is the clean route to the classics.
    """
    published = library.find_published_stage(stage_id)
    if published:
        return published
    if not shutil.which("yt-dlp"):
        raise RuntimeError("yt-dlp is not installed in this container.")

    config.ensure_dirs()
    with tempfile.TemporaryDirectory(dir=config.WORK_DIR) as tmpdir:
        tmp = Path(tmpdir)
        chapters_dir = tmp / "chapters"
        progress("Fetching audio")

        # No pick defaults to the one video the link points at. A bare playlist
        # link is unaffected by --no-playlist, so it still brings every entry;
        # a watch?v=...&list=... link no longer drags its whole playlist in
        # behind it. Picking entries is what opts you into the playlist.
        #
        # A pick that names nothing is refused instead of falling back on that
        # default, because the fallback is what made unticking every entry
        # download all of them.
        if playlist_items is None:
            selection = ["--no-playlist"]
        else:
            picked = _playlist_items_spec(playlist_items)
            if not picked:
                raise ValueError("A playlist pick has to name at least one entry number.")
            selection = ["--yes-playlist", "--playlist-items", picked]
        cmd = [
            "yt-dlp",
            *selection,
            "-x", "--audio-format", "mp3", "--audio-quality", "0",
            "--write-info-json", "--write-thumbnail", "--convert-thumbnails", "jpg",
            "--no-progress", "--newline", "--no-warnings",
            *_player_client_args(),
            "-o", str(tmp / "%(playlist_index|0)03d-%(title).70s.%(ext)s"),
        ]
        if use_chapters:
            # yt-dlp writes one file per chapter marker alongside the full file.
            # The video number leads the name so that chapters from one video in
            # a playlist never interleave with another video's.
            cmd += ["--split-chapters",
                    "-o", f"chapter:{chapters_dir}/%(playlist_index|0)03d-"
                          "%(section_number)03d-%(section_title).60s.%(ext)s"]
        cmd.append(url)

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10800)
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()[-5:]
            raise RuntimeError(f"yt-dlp failed: {' / '.join(tail) or 'unknown error'}")

        info = _read_info_json(tmp)
        produced = _order_produced_audio(tmp, chapters_dir)
        if not produced:
            raise RuntimeError("yt-dlp produced no audio files.")

        uploader = info.get("uploader") or info.get("channel") or ""
        # A playlist names itself; every video in it carries the playlist title
        # alongside its own, so the collection is not named after video one.
        book_title = forge.clean_title(
            info.get("playlist_title") or info.get("title") or produced[0][0].stem)

        with library.collection_lease():
            stage = library.begin_collection_stage(
                stage_id,
                title=book_title,
                source="url",
                extra={
                    "url": info.get("webpage_url") or url,
                    "uploader": uploader,
                    "raw_title": info.get("title") or "",
                    "from_chapters": any(from_chapter for _, from_chapter in produced),
                },
                restart=True,
            )
            dest = stage.path
            start = 1

            stored: list[tuple[str, str]] = []
            for offset, (src, from_chapter) in enumerate(produced):
                index = start + offset
                # Name the file from the cleaned title, not the raw stem. A chapter
                # file arrives as "001-002-Intro", so slugifying the stem would
                # stutter the numbers back out as "001-001-002-intro.mp3".
                track_title = _track_title(src.stem, from_chapter, offset, book_title)
                name = f"{index:03d}-{audio.slugify(track_title)}.mp3"
                progress(f"Storing {index}/{len(produced)}")
                shutil.move(str(src), dest / name)
                stored.append((name, track_title))

            cover = _pick_thumbnail(tmp)
            if cover:
                shutil.move(str(cover), dest / "cover.jpg")

            library.rescan_collection_stage(stage_id)
            for name, track_title in stored:
                library.rename_track_at_path(dest, name, track_title)

            progress("Probing durations")
            library.collection_stage(stage_id, refresh=True)
            return library.complete_collection_stage(stage_id)


def _read_info_json(tmp: Path) -> dict[str, Any]:
    for candidate in sorted(tmp.glob("*.info.json")):
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def _pick_thumbnail(tmp: Path) -> Path | None:
    for pattern in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
        found = sorted(tmp.glob(pattern))
        if found:
            return found[0]
    return None


# Both of our yt-dlp output templates prefix every file with its playlist
# number, which renders as a bare 0 for a lone video. A chapter file carries a
# second number for the chapter itself: "001-002-Intro". Strip exactly those
# prefixes rather than letting clean_title guess at them, so a video genuinely
# called "7-Zip Explained" keeps its 7 once our own numbers are off the front.
_OUR_INDEX_PREFIX = re.compile(r"^\d+-")


def _order_produced_audio(tmp: Path, chapters_dir: Path) -> list[tuple[Path, bool]]:
    """Pair each downloaded video with its chapters, in playlist order.

    Chapter files win for a video that has chapter markers -- that is the
    difference between one 6-hour blob and 30 tracks. A playlist can mix
    chaptered and unchaptered videos, so the choice is made per video; an
    unchaptered video keeps its whole file instead of vanishing because some
    other video in the playlist happened to be chaptered.
    """
    chapters: dict[int, list[Path]] = {}
    if chapters_dir.is_dir():
        for path in sorted(chapters_dir.glob("*.mp3")):
            chapters.setdefault(_leading_index(path.stem), []).append(path)
    wholes: dict[int, list[Path]] = {}
    for path in sorted(tmp.iterdir()):
        if path.is_file() and path.suffix.lower() in audio.AUDIO_EXTENSIONS:
            wholes.setdefault(_leading_index(path.stem), []).append(path)
    ordered: list[tuple[Path, bool]] = []
    for number in sorted(set(chapters) | set(wholes)):
        if number in chapters:
            ordered.extend((path, True) for path in chapters[number])
        else:
            ordered.extend((path, False) for path in wholes[number])
    return ordered


def _leading_index(stem: str) -> int:
    """Both templates lead with the playlist number, 0 for a lone video."""
    match = _OUR_INDEX_PREFIX.match(stem)
    return int(match.group(0)[:-1]) if match else 0


def _track_title(stem: str, from_chapter: bool, offset: int, book_title: str) -> str:
    """Chapter files carry a real chapter name; whole videos fall back to the book."""
    bare = _OUR_INDEX_PREFIX.sub("", stem, count=1)
    if from_chapter:
        bare = _OUR_INDEX_PREFIX.sub("", bare, count=1)
    cleaned = forge.clean_title(bare)
    if from_chapter:
        return cleaned or f"Chapter {offset + 1}"
    return cleaned or book_title


# ------------------------------------------------------------ file uploads

def import_upload(
    source: Path,
    *,
    filename: str,
    stage_id: str,
    target_name: str,
) -> dict[str, Any]:
    """Stream one staged file into its deterministic collection target."""
    suffix = Path(filename).suffix.lower()
    if suffix not in audio.AUDIO_EXTENSIONS:
        raise RuntimeError(f"{suffix or 'That file'} is not a supported audio format.")
    source = Path(source)
    if not source.is_file():
        raise RuntimeError("The staged upload file is missing. Submit the collection again.")
    with library.collection_lease():
        staged = library.collection_stage(stage_id)
        if not staged:
            raise RuntimeError("The upload collection stage is missing.")
        dest = Path(staged["path"])
        target = dest / target_name
        if target.parent != dest or target.suffix.lower() != suffix:
            raise RuntimeError("The upload target is invalid.")

        if not target.is_file():
            partial = dest / f".{target.name}.part"
            with source.open("rb") as staged, partial.open("wb") as output:
                shutil.copyfileobj(staged, output, length=1024 * 1024)
            partial.replace(target)

        library.rescan_collection_stage(stage_id)
        library.rename_track_at_path(
            dest,
            target.name,
            forge.clean_title(Path(filename).stem, drop_leading_index=True),
        )
        return library.collection_stage(stage_id, refresh=True)
