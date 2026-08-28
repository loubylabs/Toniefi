"""The on-disk library.

Layout, deliberately boring so nothing is trapped in this app:

    /library/<slug>/collection.json     metadata, track order, cached durations
    /library/<slug>/cover.jpg           artwork, when the source had any
    /library/<slug>/001-chapter-one.mp3
    /library/<slug>/002-chapter-two.mp3

Delete Toniefi tomorrow and you still have plain folders of MP3s.

Track ORDER lives in collection.json, not in the filenames -- the Review step
lets you drag chapters around, and renaming files to match would churn the
whole folder every time. Files found on disk that the manifest doesn't know
about get appended at the end, in filename order.
"""
from __future__ import annotations

import json
import hashlib
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from . import audio, config

MANIFEST = "collection.json"
COVER_NAMES = ("cover.jpg", "cover.png", "cover.webp")
_manifest_lock = threading.RLock()


def _dir_for(slug: str) -> Path:
    """Resolve a slug to a directory, refusing anything outside the library."""
    candidate = (config.LIBRARY_DIR / slug).resolve()
    root = config.LIBRARY_DIR.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Refusing to touch {slug!r}: outside the library.")
    return candidate


def unique_slug(title: str) -> str:
    base = audio.slugify(title)
    slug, counter = base, 2
    while (config.LIBRARY_DIR / slug).exists():
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def create(title: str, source: str = "", extra: dict[str, Any] | None = None) -> str:
    with _manifest_lock:
        config.ensure_dirs()
        slug = unique_slug(title)
        path = config.LIBRARY_DIR / slug
        path.mkdir(parents=True)
        manifest: dict[str, Any] = {
            "slug": slug,
            "title": title,
            "source": source,
            "stage": "extracted",
            "created_at": time.time(),
            "tracks": [],
        }
        if extra:
            manifest.update(extra)
        _write_manifest(path, manifest)
        return slug


# ------------------------------------------------------------- manifest i/o

def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    (path / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        return json.loads((path / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def audio_files(path: Path) -> list[Path]:
    return sorted(
        p for p in path.iterdir()
        if p.is_file() and p.suffix.lower() in audio.AUDIO_EXTENSIONS
    )


def find_cover(path: Path) -> str | None:
    for name in COVER_NAMES:
        if (path / name).is_file():
            return name
    return None


# ------------------------------------------------------------------- rescan

def rescan(slug: str) -> dict[str, Any]:
    """Re-probe the folder and rewrite the manifest, preserving track order.

    Anything dropped in by hand (scp, an Unraid share, a download) is picked
    up here -- Toniefi never assumes it is the only thing writing to the
    library.
    """
    with _manifest_lock:
        path = _dir_for(slug)
        manifest = _read_manifest(path)
        manifest.setdefault("slug", slug)
        manifest.setdefault("title", slug.replace("-", " ").title())
        manifest.setdefault("created_at", time.time())
        manifest.setdefault("stage", "extracted")

        on_disk = {p.name: p for p in audio_files(path)}
        cached = {t.get("name"): t for t in manifest.get("tracks", [])}

    # Manifest order first (dropping files that vanished), then anything new.
        ordered_names = [n for n in cached if n in on_disk]
        ordered_names += sorted(n for n in on_disk if n not in cached)

        tracks: list[dict[str, Any]] = []
        for name in ordered_names:
            file = on_disk[name]
            stat = file.stat()
            prior = cached.get(name)
        # Trust the cached duration only while size and mtime are unchanged;
        # probing every file on every page load makes a big library crawl.
            if prior and prior.get("size") == stat.st_size and prior.get("mtime") == int(stat.st_mtime):
                tracks.append(prior)
                continue
            try:
                seconds = audio.duration_seconds(file)
            except audio.AudioError:
                seconds = 0.0
            tracks.append({
                "name": name,
                "title": (prior or {}).get("title") or file.stem,
                "seconds": round(seconds, 1),
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
            })

        manifest["tracks"] = tracks
        cover = find_cover(path)
        if cover:
            manifest["cover"] = cover
        _write_manifest(path, manifest)
        return manifest


# -------------------------------------------------------------------- reads

def get(slug: str, refresh: bool = False) -> dict[str, Any] | None:
    with _manifest_lock:
        path = _dir_for(slug)
        if not path.is_dir():
            return None
        manifest = rescan(slug) if refresh or not (path / MANIFEST).exists() else _read_manifest(path)
        return _decorate(slug, path, manifest)


def manifest_fingerprint(manifest: dict[str, Any]) -> str:
    """Identify the exact reviewed order and metadata used for a send."""
    relevant = {
        "slug": manifest.get("slug"),
        "title": manifest.get("title"),
        "stage": manifest.get("stage"),
        "source": manifest.get("source"),
        "uploader": manifest.get("uploader"),
        "forge": manifest.get("forge"),
        "tracks": [
            {key: track.get(key) for key in ("name", "title", "seconds", "size", "mtime")}
            for track in manifest.get("tracks", [])
        ],
    }
    encoded = json.dumps(relevant, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _decorate(slug: str, path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    tracks = manifest.get("tracks", [])
    total = sum(t.get("seconds", 0) for t in tracks)
    limit = config.usable_limit()
    manifest = dict(manifest)
    manifest.update({
        "slug": slug,
        "path": str(path),
        "track_count": len(tracks),
        "total_seconds": round(total, 1),
        "total_duration": audio.human_duration(total),
        "tonies_needed": len(plan_groups(tracks)) if tracks else 0,
        "fits_one_tonie": bool(tracks) and total <= limit
        and all(t.get("seconds", 0) <= limit for t in tracks),
        "has_oversized": any(t.get("seconds", 0) > limit for t in tracks),
    })
    manifest["tracks"] = [dict(t) for t in tracks]
    for track in manifest["tracks"]:
        track["duration"] = audio.human_duration(track.get("seconds", 0))
        track["oversized"] = track.get("seconds", 0) > limit
    return manifest


def list_all() -> list[dict[str, Any]]:
    with _manifest_lock:
        config.ensure_dirs()
        out = []
        for path in sorted(config.LIBRARY_DIR.iterdir()):
            if not path.is_dir():
                continue
            manifest = _read_manifest(path) or rescan(path.name)
            out.append(_decorate(path.name, path, manifest))
        out.sort(key=lambda m: m.get("created_at", 0), reverse=True)
        return out


# ------------------------------------------------------------------ packing

def plan_groups(tracks: list[dict[str, Any]], limit: int | None = None) -> list[audio.Group]:
    items = [
        audio.Track(
            path=Path(t["name"]),
            title=t.get("title") or t["name"],
            seconds=t.get("seconds", 0),
        )
        for t in tracks
    ]
    return audio.pack(items, limit)


def plan(slug: str, limit: int | None = None) -> list[dict[str, Any]]:
    """How this collection would be spread across Creative Tonies."""
    manifest = get(slug)
    if not manifest:
        return []
    return [g.as_dict() for g in plan_groups(manifest["tracks"], limit)]


# ------------------------------------------------------------------ mutation

def _mutate(slug: str, fn) -> dict[str, Any]:
    with _manifest_lock:
        path = _dir_for(slug)
        manifest = _read_manifest(path)
        fn(manifest)
        _write_manifest(path, manifest)
        return get(slug)


def set_title(slug: str, title: str) -> dict[str, Any]:
    return _mutate(slug, lambda m: m.__setitem__("title", title))


def set_stage(slug: str, stage: str) -> dict[str, Any]:
    return _mutate(slug, lambda m: m.__setitem__("stage", stage))


def set_forge_state(slug: str, state: dict[str, Any]) -> dict[str, Any]:
    def apply(m: dict[str, Any]) -> None:
        m["forge"] = state
        m["stage"] = "forged"
    return _mutate(slug, apply)


def rename_track(slug: str, name: str, title: str) -> dict[str, Any]:
    def apply(m: dict[str, Any]) -> None:
        for track in m.get("tracks", []):
            if track.get("name") == name:
                track["title"] = title
    return _mutate(slug, apply)


def reorder(slug: str, names: list[str]) -> dict[str, Any]:
    """Apply a drag-and-drop reorder. Names not listed keep their relative
    position at the end, so a partial list can never silently drop a track."""
    def apply(m: dict[str, Any]) -> None:
        by_name = {t["name"]: t for t in m.get("tracks", [])}
        ordered = [by_name[n] for n in names if n in by_name]
        ordered += [t for t in m.get("tracks", []) if t["name"] not in set(names)]
        m["tracks"] = ordered
    return _mutate(slug, apply)


def replace_track(slug: str, name: str, new_names: list[str]) -> dict[str, Any]:
    """Swap one track for the parts it was split into, keeping its position."""
    def apply(m: dict[str, Any]) -> None:
        tracks = m.get("tracks", [])
        index = next((i for i, t in enumerate(tracks) if t["name"] == name), None)
        if index is None:
            return
        base = tracks[index]
        stand_ins = [
            {"name": new, "title": f"{base.get('title', name)} (part {i})"}
            for i, new in enumerate(new_names, start=1)
        ]
        m["tracks"] = tracks[:index] + stand_ins + tracks[index + 1:]
    return _mutate(slug, apply)


def delete_track(slug: str, name: str) -> dict[str, Any]:
    with _manifest_lock:
        path = _dir_for(slug)
        target = path / name
        if target.parent != path:
            raise ValueError("Refusing to delete outside the collection.")
        if target.is_file():
            target.unlink()

        def apply(m: dict[str, Any]) -> None:
            m["tracks"] = [t for t in m.get("tracks", []) if t.get("name") != name]
        return _mutate(slug, apply)


def delete(slug: str) -> None:
    with _manifest_lock:
        shutil.rmtree(_dir_for(slug))


# ------------------------------------------------------------------- paths

def track_path(slug: str, name: str) -> Path:
    base = _dir_for(slug)
    path = base / name
    if path.parent != base or not path.is_file():
        raise ValueError(f"No such track: {name}")
    return path


def cover_path(slug: str) -> Path | None:
    base = _dir_for(slug)
    cover = find_cover(base)
    return base / cover if cover else None


def next_index(path: Path) -> int:
    return len(audio_files(path)) + 1
