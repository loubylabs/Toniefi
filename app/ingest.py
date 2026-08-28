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


def import_librivox(book_id: str, progress: Progress = _noop) -> dict[str, Any]:
    book, sections = librivox_sections(book_id)
    with library.collection_lease():
        title = book.get("title", f"LibriVox {book_id}")
        slug = library.create(title, source="librivox", extra={
            "author": _authors(book),
            "librivox_id": book_id,
            "url": book.get("url_librivox", ""),
            "license": "Public domain (LibriVox)",
        })
        dest = config.LIBRARY_DIR / slug

        total = len(sections)
        with httpx.Client(timeout=300.0, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as client:
            for position, section in enumerate(sections, start=1):
                label = section.get("title") or f"Section {position}"
                progress(f"Downloading {position}/{total}: {label}")
                filename = f"{position:03d}-{audio.slugify(label)}.mp3"
                _stream_download(client, section["listen_url"], dest / filename)

        # LibriVox section titles are already clean; keep them as the track titles.
        library.rescan(slug)
        for position, section in enumerate(sections, start=1):
            label = section.get("title") or f"Section {position}"
            library.rename_track(slug, f"{position:03d}-{audio.slugify(label)}.mp3", label)

        progress("Probing durations")
        return library.get(slug, refresh=True)


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


def probe_url(url: str) -> dict[str, Any]:
    """Look at a URL without downloading it, so Review can show what's coming."""
    if not shutil.which("yt-dlp"):
        raise RuntimeError("yt-dlp is not installed in this container.")
    proc = subprocess.run(
        ["yt-dlp", "--dump-single-json", "--flat-playlist", "--no-warnings",
         *_player_client_args(), url],
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError(f"Could not read that URL: {' / '.join(tail) or 'unknown error'}")
    info = json.loads(proc.stdout)

    entries = info.get("entries") or []
    chapters = info.get("chapters") or []
    duration = float(info.get("duration") or 0)
    if entries:
        duration = sum(float(e.get("duration") or 0) for e in entries)

    return {
        "title": forge.clean_title(info.get("title") or "Untitled"),
        "raw_title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "is_playlist": bool(entries),
        "item_count": len(entries) or 1,
        "chapter_count": len(chapters),
        "chapters": [
            {"title": c.get("title", ""), "start": c.get("start_time"), "end": c.get("end_time")}
            for c in chapters[:200]
        ],
        "duration_seconds": duration,
        "duration": audio.human_duration(duration),
        "tonies_needed": max(1, -(-int(duration) // config.usable_limit())) if duration else 0,
        "thumbnail": info.get("thumbnail") or "",
        "webpage_url": info.get("webpage_url") or url,
    }


def import_url(
    url: str,
    title: str | None = None,
    slug: str | None = None,
    use_chapters: bool = True,
    progress: Progress = _noop,
) -> dict[str, Any]:
    """Extract audio from a URL with yt-dlp, keeping metadata and cover art.

    Point this at material you have the right to use -- your own recordings,
    Creative Commons uploads, or public domain works. Most "full audiobook"
    uploads on video sites are unlicensed rips of commercial recordings, and
    the LibriVox path above is the clean route to the classics.
    """
    if not shutil.which("yt-dlp"):
        raise RuntimeError("yt-dlp is not installed in this container.")

    config.ensure_dirs()
    with tempfile.TemporaryDirectory(dir=config.WORK_DIR) as tmpdir:
        tmp = Path(tmpdir)
        chapters_dir = tmp / "chapters"
        progress("Fetching audio")

        cmd = [
            "yt-dlp",
            "--yes-playlist" if "list=" in url else "--no-playlist",
            "-x", "--audio-format", "mp3", "--audio-quality", "0",
            "--write-info-json", "--write-thumbnail", "--convert-thumbnails", "jpg",
            "--no-progress", "--newline", "--no-warnings",
            *_player_client_args(),
            "-o", str(tmp / "%(playlist_index|0)03d-%(title).70s.%(ext)s"),
        ]
        if use_chapters:
            # yt-dlp writes one file per chapter marker alongside the full file.
            cmd += ["--split-chapters",
                    "-o", f"chapter:{chapters_dir}/%(section_number)03d-%(section_title).60s.%(ext)s"]
        cmd.append(url)

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10800)
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()[-5:]
            raise RuntimeError(f"yt-dlp failed: {' / '.join(tail) or 'unknown error'}")

        info = _read_info_json(tmp)
        # Prefer chapter files when the upload had real chapter markers --
        # that is the difference between one 6-hour blob and 30 tracks.
        chaptered = sorted(chapters_dir.glob("*.mp3")) if chapters_dir.is_dir() else []
        produced = chaptered or sorted(
            p for p in tmp.iterdir()
            if p.is_file() and p.suffix.lower() in audio.AUDIO_EXTENSIONS
        )
        if not produced:
            raise RuntimeError("yt-dlp produced no audio files.")

        uploader = info.get("uploader") or info.get("channel") or ""
        book_title = title or forge.clean_title(info.get("title") or produced[0].stem)

        with library.collection_lease():
            if slug:
                target_slug = slug
                dest = config.LIBRARY_DIR / target_slug
                if not dest.is_dir():
                    raise RuntimeError(f"No collection named {slug}.")
                start = library.next_index(dest)
            else:
                target_slug = library.create(book_title, source="url", extra={
                    "url": info.get("webpage_url") or url,
                    "uploader": uploader,
                    "raw_title": info.get("title") or "",
                    "from_chapters": bool(chaptered),
                })
                dest = config.LIBRARY_DIR / target_slug
                start = 1

            stored: list[tuple[str, str]] = []
            for offset, src in enumerate(produced):
                index = start + offset
                # Name the file from the cleaned title, not the raw stem. A chapter
                # file arrives as "001-Intro", so slugifying the stem would stutter
                # the index back out as "001-001-intro.mp3".
                track_title = _track_title(src.stem, chaptered, offset, book_title)
                name = f"{index:03d}-{audio.slugify(track_title)}.mp3"
                progress(f"Storing {index}/{len(produced)}")
                shutil.move(str(src), dest / name)
                stored.append((name, track_title))

            cover = _pick_thumbnail(tmp)
            if cover:
                shutil.move(str(cover), dest / "cover.jpg")

            library.rescan(target_slug)
            for name, track_title in stored:
                library.rename_track(target_slug, name, track_title)

            progress("Probing durations")
            return library.get(target_slug, refresh=True)


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


# Both of our yt-dlp output templates below prefix every file with an index:
# "001-Intro" for a chapter, and "0-Title" for a single video, because the
# playlist_index fallback renders as a bare 0. Strip exactly that one prefix
# rather than letting clean_title guess at it, so a video genuinely called
# "7-Zip Explained" keeps its 7 once our own "0-" is off the front.
_OUR_INDEX_PREFIX = re.compile(r"^\d+-")


def _track_title(stem: str, chaptered: list[Path], offset: int, book_title: str) -> str:
    """Chapter files carry a real chapter name; single files fall back to the book."""
    cleaned = forge.clean_title(_OUR_INDEX_PREFIX.sub("", stem, count=1))
    if chaptered:
        return cleaned or f"Chapter {offset + 1}"
    return cleaned or book_title


# ------------------------------------------------------------ file uploads

def import_upload(
    source: Path,
    *,
    filename: str,
    slug: str,
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
        dest = config.LIBRARY_DIR / slug
        if not dest.is_dir():
            raise RuntimeError(f"No collection named {slug}.")
        target = dest / target_name
        if target.parent != dest or target.suffix.lower() != suffix:
            raise RuntimeError("The upload target is invalid.")

        if not target.is_file():
            partial = dest / f".{target.name}.part"
            with source.open("rb") as staged, partial.open("wb") as output:
                shutil.copyfileobj(staged, output, length=1024 * 1024)
            partial.replace(target)

        library.rescan(slug)
        library.rename_track(
            slug,
            target.name,
            forge.clean_title(Path(filename).stem, drop_leading_index=True),
        )
        return library.get(slug, refresh=True)
