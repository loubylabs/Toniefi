"""The on-disk library.

Layout, deliberately boring so nothing is trapped in this app:

    /library/<slug>/collection.json     metadata, track order, cached durations
    /library/<slug>/cover.jpg           artwork, when the source had any
    /library/<slug>/001-chapter-one.mp3
    /library/<slug>/002-chapter-two.mp3

Delete Toniefi tomorrow and you still have plain folders of MP3s.

Track ORDER lives in collection.json, not in the filenames -- the collection
page lets you drag chapters around, and renaming files to match would churn
the whole folder every time. Files found on disk that the manifest doesn't
know about get appended at the end, in filename order.
"""
from __future__ import annotations

import json
import hashlib
import re
import shutil
import threading
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import audio, config

MANIFEST = "collection.json"
COVER_NAMES = ("cover.jpg", "cover.png", "cover.webp")
COLLECTION_STAGE_MARKER = ".toniefi-collection-stage.json"
COLLECTION_STAGE_PREFIX = ".toniefi-stage-"
FORGE_STAGE_PREFIX = ".toniefi-forge-"
BACKUP_STAGE_PREFIX = ".toniefi-backup-"
SLUG_RESERVATION_PREFIX = ".toniefi-slug-"
SLUG_RESERVATION_MARKER = ".toniefi-slug-reservation.json"
INVALID_PUBLIC_COLLECTION_SLUG_DETAIL = "Invalid collection slug."
PORTABLE_PUBLIC_SLUG_BYTES = 255
PRIVATE_LIBRARY_PREFIXES = (
    COLLECTION_STAGE_PREFIX,
    FORGE_STAGE_PREFIX,
    BACKUP_STAGE_PREFIX,
    SLUG_RESERVATION_PREFIX,
)
_manifest_lock = threading.RLock()


@dataclass(frozen=True)
class CollectionStage:
    identity: str
    slug: str
    path: Path


class InvalidPublicCollectionSlug(Exception):
    """A public slug cannot identify a collection directory safely."""

    def __init__(self) -> None:
        super().__init__(INVALID_PUBLIC_COLLECTION_SLUG_DETAIL)


@contextmanager
def collection_lease():
    """Hold the canonical manifest lock across a multi-system operation."""
    with _manifest_lock:
        yield


def _unsafe_public_slug_character(character: str) -> bool:
    codepoint = ord(character)
    return (
        unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        or 0xFDD0 <= codepoint <= 0xFDEF
        or (codepoint & 0xFFFE) == 0xFFFE
    )


def validate_public_collection_slug(slug: object) -> str:
    """Return one public collection name or raise the typed boundary error."""
    if (
        not isinstance(slug, str)
        or not slug
        or slug in {".", ".."}
        or slug.startswith(".")
        or "/" in slug
        or "\\" in slug
        or any(slug.startswith(prefix) for prefix in PRIVATE_LIBRARY_PREFIXES)
    ):
        raise InvalidPublicCollectionSlug()
    try:
        encoded = slug.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidPublicCollectionSlug() from exc
    if len(encoded) > PORTABLE_PUBLIC_SLUG_BYTES or any(
        _unsafe_public_slug_character(character) for character in slug
    ):
        raise InvalidPublicCollectionSlug()
    return slug


def _public_collection_path(slug: object) -> Path:
    """Resolve exactly one validated public collection beneath the library."""
    public_slug = validate_public_collection_slug(slug)
    root = config.LIBRARY_DIR.resolve()
    candidate = (root / public_slug).resolve()
    if candidate.parent != root:
        raise InvalidPublicCollectionSlug()
    return candidate


def _private_library_path(name: str) -> Path:
    """Resolve one reserved internal directory beneath the library."""
    if (
        not isinstance(name, str)
        or not any(name.startswith(prefix) for prefix in PRIVATE_LIBRARY_PREFIXES)
        or "/" in name
        or "\\" in name
        or Path(name).is_absolute()
    ):
        raise ValueError("Invalid private collection path.")
    root = config.LIBRARY_DIR.resolve()
    candidate = (root / name).resolve()
    if candidate.parent != root:
        raise ValueError("Invalid private collection path.")
    return candidate


def _safe_stage_identity(identity: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(identity)).strip("-.")
    if not safe:
        raise ValueError("A collection stage needs an identity.")
    return safe[:160]


def _collection_stage_path(identity: str) -> Path:
    return _private_library_path(f"{COLLECTION_STAGE_PREFIX}{_safe_stage_identity(identity)}")


def _forge_stage_path(identity: str) -> Path:
    return _private_library_path(f"{FORGE_STAGE_PREFIX}{_safe_stage_identity(identity)}")


def _backup_stage_path(identity: str) -> Path:
    return _private_library_path(f"{BACKUP_STAGE_PREFIX}{_safe_stage_identity(identity)}")


def _slug_reservation_path(slug: str) -> Path:
    public_slug = validate_public_collection_slug(slug)
    return _private_library_path(f"{SLUG_RESERVATION_PREFIX}{public_slug}")


def _reservation_marker(path: Path) -> dict[str, Any]:
    try:
        return json.loads((path / SLUG_RESERVATION_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _write_reservation_marker(path: Path, marker: dict[str, Any]) -> None:
    target = path / SLUG_RESERVATION_MARKER
    temporary = path / f".{SLUG_RESERVATION_MARKER}.tmp"
    temporary.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    temporary.replace(target)


def _reserved_slug(identity: str) -> str | None:
    for path in config.LIBRARY_DIR.glob(f"{SLUG_RESERVATION_PREFIX}*"):
        marker = _reservation_marker(path)
        if marker.get("identity") == identity and marker.get("slug"):
            return str(marker["slug"])
    return None


def _reserve_final_slug(title: str, identity: str) -> str:
    existing = _reserved_slug(identity)
    if existing:
        return existing
    base = audio.slugify(title)
    counter = 1
    while True:
        slug = base if counter == 1 else f"{base}-{counter}"
        counter += 1
        visible = _public_collection_path(slug)
        reservation = _slug_reservation_path(slug)
        if visible.exists():
            continue
        try:
            reservation.mkdir()
        except FileExistsError:
            continue
        try:
            if visible.exists():
                shutil.rmtree(reservation)
                continue
            _write_reservation_marker(reservation, {
                "identity": identity,
                "slug": slug,
                "created_at": time.time(),
            })
            return slug
        except BaseException:
            shutil.rmtree(reservation, ignore_errors=True)
            raise


def _ensure_slug_reservation(slug: str, identity: str) -> None:
    reservation = _slug_reservation_path(slug)
    if reservation.is_dir():
        marker = _reservation_marker(reservation)
        if marker.get("identity") == identity and marker.get("slug") == slug:
            return
        raise RuntimeError(f"A different collection reserved {slug}.")
    if _public_collection_path(slug).exists():
        raise RuntimeError(f"A different collection already uses {slug}.")
    try:
        reservation.mkdir()
    except FileExistsError as exc:
        raise RuntimeError(f"A different collection reserved {slug}.") from exc
    try:
        if _public_collection_path(slug).exists():
            raise RuntimeError(f"A different collection already uses {slug}.")
        _write_reservation_marker(reservation, {
            "identity": identity,
            "slug": slug,
            "created_at": time.time(),
        })
    except BaseException:
        shutil.rmtree(reservation, ignore_errors=True)
        raise


def _release_slug_reservation(slug: str, identity: str) -> None:
    reservation = _slug_reservation_path(slug)
    marker = _reservation_marker(reservation)
    if marker.get("identity") == identity:
        shutil.rmtree(reservation, ignore_errors=True)


def _release_identity_reservations(identity: str) -> None:
    for path in config.LIBRARY_DIR.glob(f"{SLUG_RESERVATION_PREFIX}*"):
        if _reservation_marker(path).get("identity") == identity:
            shutil.rmtree(path, ignore_errors=True)


def _stage_marker(path: Path) -> dict[str, Any]:
    try:
        return json.loads((path / COLLECTION_STAGE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _write_stage_marker(path: Path, marker: dict[str, Any]) -> None:
    target = path / COLLECTION_STAGE_MARKER
    temporary = path / f".{COLLECTION_STAGE_MARKER}.tmp"
    temporary.write_text(
        json.dumps(marker, indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)


def find_published_stage(identity: str) -> dict[str, Any] | None:
    with _manifest_lock:
        config.ensure_dirs()
        for path in config.LIBRARY_DIR.iterdir():
            if not path.is_dir() or path.name.startswith("."):
                continue
            manifest = _read_manifest(path)
            if manifest.get("publication_id") == identity:
                marker = path / COLLECTION_STAGE_MARKER
                marker.unlink(missing_ok=True)
                discard_collection_stage(identity)
                return _decorate(path.name, path, manifest)
        return None


def begin_collection_stage(
    identity: str,
    *,
    title: str,
    source: str,
    extra: dict[str, Any] | None = None,
    restart: bool = False,
) -> CollectionStage:
    """Create or resume one hidden collection with a stable final slug."""
    with _manifest_lock:
        config.ensure_dirs()
        published = find_published_stage(identity)
        if published:
            return CollectionStage(
                identity,
                published["slug"],
                _public_collection_path(published["slug"]),
            )
        path = _collection_stage_path(identity)
        if path.is_dir():
            marker = _stage_marker(path)
            if marker.get("identity") != identity or not marker.get("slug"):
                shutil.rmtree(path)
                _release_identity_reservations(identity)
            elif not restart:
                _ensure_slug_reservation(marker["slug"], identity)
                return CollectionStage(identity, marker["slug"], path)
            else:
                slug = marker["slug"]
                _ensure_slug_reservation(slug, identity)
                shutil.rmtree(path)
                return _create_collection_stage(path, identity, slug, title, source, extra)
        slug = _reserve_final_slug(title, identity)
        return _create_collection_stage(path, identity, slug, title, source, extra)


def _create_collection_stage(
    path: Path,
    identity: str,
    slug: str,
    title: str,
    source: str,
    extra: dict[str, Any] | None,
) -> CollectionStage:
    try:
        path.mkdir(parents=True)
        manifest: dict[str, Any] = {
            "slug": slug,
            "title": title,
            "source": source,
            "stage": "extracted",
            "publication_id": identity,
            "created_at": time.time(),
            "tracks": [],
        }
        if extra:
            manifest.update(extra)
        _write_manifest(path, manifest)
        _write_stage_marker(path, {
            "kind": "collection",
            "identity": identity,
            "slug": slug,
            "state": "staging",
            "created_at": time.time(),
        })
        return CollectionStage(identity, slug, path)
    except BaseException:
        shutil.rmtree(path, ignore_errors=True)
        _release_slug_reservation(slug, identity)
        raise


def collection_stage(identity: str, refresh: bool = False) -> dict[str, Any] | None:
    with _manifest_lock:
        published = find_published_stage(identity)
        if published:
            return published
        path = _collection_stage_path(identity)
        marker = _stage_marker(path)
        if not path.is_dir() or marker.get("identity") != identity:
            return None
        return _get_at_path(path, marker["slug"], refresh=refresh)


def collection_stage_ready(identity: str) -> bool:
    """Report whether extraction reached its durable completion checkpoint."""
    with _manifest_lock:
        if find_published_stage(identity):
            return True
        path = _collection_stage_path(identity)
        marker = _stage_marker(path)
        return (
            path.is_dir()
            and marker.get("identity") == identity
            and marker.get("state") == "ready"
        )


def complete_collection_stage(identity: str) -> dict[str, Any]:
    """Checkpoint a complete extraction before Forge may consume it."""
    with _manifest_lock:
        path = _collection_stage_path(identity)
        marker = _stage_marker(path)
        if not path.is_dir() or marker.get("identity") != identity:
            raise RuntimeError("The staged collection is missing.")
        marker["state"] = "ready"
        marker["completed_at"] = time.time()
        _write_stage_marker(path, marker)
        return _get_at_path(path, marker["slug"])


def rescan_collection_stage(identity: str) -> dict[str, Any]:
    with _manifest_lock:
        path = _collection_stage_path(identity)
        marker = _stage_marker(path)
        if marker.get("identity") != identity:
            raise RuntimeError("The staged collection is missing.")
        return _decorate(marker["slug"], path, _rescan_path(path, marker["slug"]))


def discard_collection_stage(identity: str) -> None:
    with _manifest_lock:
        path = _collection_stage_path(identity)
        marker = _stage_marker(path)
        shutil.rmtree(path, ignore_errors=True)
        slug = marker.get("slug")
        if isinstance(slug, str):
            _release_slug_reservation(slug, identity)
        else:
            _release_identity_reservations(identity)


def sweep_collection_stages(referenced_identities: set[str]) -> None:
    """Remove hidden extracted collections that no resumable job owns."""
    with _manifest_lock:
        config.ensure_dirs()
        for path in config.LIBRARY_DIR.glob(f"{COLLECTION_STAGE_PREFIX}*"):
            if not path.is_dir():
                continue
            marker = _stage_marker(path)
            identity = marker.get("identity")
            if isinstance(identity, str) and find_published_stage(identity):
                continue
            if not isinstance(identity, str) or identity not in referenced_identities:
                shutil.rmtree(path, ignore_errors=True)
                if isinstance(identity, str):
                    _release_identity_reservations(identity)


def create_replacement_stage(slug: str, identity: str) -> Path:
    """Copy one visible collection to hidden same-filesystem Forge input."""
    with _manifest_lock:
        recover_collection_publications()
        source = _public_collection_path(slug)
        if not source.is_dir():
            raise RuntimeError(f"No collection named {slug}.")
        stage = _forge_stage_path(identity)
        shutil.rmtree(stage, ignore_errors=True)
        shutil.copytree(source, stage)
        _write_stage_marker(stage, {
            "kind": "replacement",
            "identity": identity,
            "slug": slug,
            "state": "staging",
            "created_at": time.time(),
        })
        return stage


def create_forge_stage_from_collection_stage(collection_identity: str, forge_identity: str) -> Path:
    """Copy immutable extracted staging into a disposable Forge workspace."""
    with _manifest_lock:
        if not collection_stage_ready(collection_identity):
            raise RuntimeError("The staged collection extraction is not complete.")
        source_manifest = collection_stage(collection_identity)
        if not source_manifest:
            raise RuntimeError("The extracted collection stage is missing.")
        source = Path(source_manifest["path"])
        stage = _forge_stage_path(forge_identity)
        shutil.rmtree(stage, ignore_errors=True)
        shutil.copytree(source, stage)
        _write_stage_marker(stage, {
            "kind": "new-collection",
            "identity": forge_identity,
            "collection_identity": collection_identity,
            "slug": source_manifest["slug"],
            "state": "staging",
            "created_at": time.time(),
        })
        return stage


def publish_forged_collection_stage(
    collection_identity: str,
    stage: Path,
    forge_identity: str,
) -> dict[str, Any]:
    """Atomically publish complete prepared output and retire extracted staging."""
    with _manifest_lock:
        published = find_published_stage(collection_identity)
        if published:
            shutil.rmtree(stage, ignore_errors=True)
            discard_collection_stage(collection_identity)
            return published
        stage = stage.resolve()
        if stage != _forge_stage_path(forge_identity).resolve():
            raise ValueError("The Forge stage path does not match its identity.")
        marker = _stage_marker(stage)
        if (
            marker.get("collection_identity") != collection_identity
            or marker.get("identity") != forge_identity
        ):
            raise RuntimeError("The Forge stage marker is invalid.")
        slug = marker["slug"]
        target = _public_collection_path(slug)
        _ensure_slug_reservation(slug, collection_identity)
        if target.exists():
            raise RuntimeError(f"A different collection already uses {slug}.")
        manifest = _read_manifest(stage)
        manifest["publication_id"] = collection_identity
        _write_manifest(stage, manifest)
        marker["state"] = "ready"
        _write_stage_marker(stage, marker)
        stage.replace(target)
        (target / COLLECTION_STAGE_MARKER).unlink(missing_ok=True)
        discard_collection_stage(collection_identity)
        return get(slug)


def publish_replacement(slug: str, stage: Path, identity: str) -> dict[str, Any]:
    """Publish a complete replacement with rollback if the second rename fails."""
    with _manifest_lock:
        visible = _public_collection_path(slug)
        stage = stage.resolve()
        if stage != _forge_stage_path(identity).resolve():
            raise ValueError("The Forge stage path does not match its identity.")
        marker = _stage_marker(stage)
        if marker.get("slug") != slug or marker.get("identity") != identity:
            raise RuntimeError("The Forge stage marker is invalid.")
        manifest = _read_manifest(stage)
        manifest["forge_operation_id"] = identity
        _write_manifest(stage, manifest)
        marker["state"] = "ready"
        _write_stage_marker(stage, marker)
        backup = _backup_stage_path(identity)
        shutil.rmtree(backup, ignore_errors=True)
        visible.replace(backup)
        try:
            stage.replace(visible)
        except BaseException:
            if not visible.exists() and backup.exists():
                backup.replace(visible)
            raise
        (visible / COLLECTION_STAGE_MARKER).unlink(missing_ok=True)
        shutil.rmtree(backup)
        return get(slug)


def completed_forge(slug: str) -> dict[str, Any] | None:
    """Return a forged collection, whose preparation stage is terminal."""
    with _manifest_lock:
        manifest = get(slug)
        if manifest and manifest.get("stage") == "forged":
            return manifest
        return None


def recover_collection_publications() -> None:
    """Finish or roll back interrupted same-filesystem collection swaps."""
    with _manifest_lock:
        config.ensure_dirs()
        for visible in list(config.LIBRARY_DIR.iterdir()):
            if not visible.is_dir() or visible.name.startswith("."):
                continue
            publication_id = _read_manifest(visible).get("publication_id")
            if isinstance(publication_id, str) and publication_id:
                (visible / COLLECTION_STAGE_MARKER).unlink(missing_ok=True)
                discard_collection_stage(publication_id)
        for backup in list(config.LIBRARY_DIR.glob(f"{BACKUP_STAGE_PREFIX}*")):
            if not backup.is_dir():
                continue
            manifest = _read_manifest(backup)
            slug = manifest.get("slug")
            if not slug:
                continue
            visible = _public_collection_path(slug)
            identity = backup.name.removeprefix(BACKUP_STAGE_PREFIX)
            stage = _forge_stage_path(identity)
            marker = _stage_marker(stage)
            if visible.exists():
                shutil.rmtree(backup)
            elif stage.is_dir() and marker.get("state") == "ready":
                stage.replace(visible)
                (visible / COLLECTION_STAGE_MARKER).unlink(missing_ok=True)
                shutil.rmtree(backup)
            else:
                backup.replace(visible)
                shutil.rmtree(stage, ignore_errors=True)
        for stage in list(config.LIBRARY_DIR.glob(f"{FORGE_STAGE_PREFIX}*")):
            marker = _stage_marker(stage)
            slug = marker.get("slug")
            if not slug:
                shutil.rmtree(stage, ignore_errors=True)
                continue
            visible = _public_collection_path(slug)
            if visible.exists():
                shutil.rmtree(stage, ignore_errors=True)
            elif marker.get("kind") == "new-collection" and marker.get("state") == "ready":
                collection_identity = marker.get("collection_identity")
                stage.replace(visible)
                (visible / COLLECTION_STAGE_MARKER).unlink(missing_ok=True)
                if collection_identity:
                    discard_collection_stage(collection_identity)
            elif marker.get("kind") == "new-collection":
                shutil.rmtree(stage, ignore_errors=True)
        for reservation in list(config.LIBRARY_DIR.glob(f"{SLUG_RESERVATION_PREFIX}*")):
            marker = _reservation_marker(reservation)
            identity = marker.get("identity")
            slug = marker.get("slug")
            if not isinstance(identity, str) or not isinstance(slug, str):
                shutil.rmtree(reservation, ignore_errors=True)
                continue
            visible = _public_collection_path(slug)
            collection_stage = _collection_stage_path(identity)
            forge_stage_exists = any(
                _stage_marker(stage).get("collection_identity") == identity
                for stage in config.LIBRARY_DIR.glob(f"{FORGE_STAGE_PREFIX}*")
            )
            if visible.exists() or (not collection_stage.exists() and not forge_stage_exists):
                shutil.rmtree(reservation, ignore_errors=True)


def create(title: str, source: str = "", extra: dict[str, Any] | None = None) -> str:
    with _manifest_lock:
        config.ensure_dirs()
        identity = f"direct-{uuid4().hex}"
        slug = _reserve_final_slug(title, identity)
        path = _public_collection_path(slug)
        try:
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
        finally:
            _release_slug_reservation(slug, identity)


# ------------------------------------------------------------- manifest i/o

def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    target = path / MANIFEST
    temporary = path / f".{MANIFEST}.tmp"
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(target)


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
        path = _public_collection_path(slug)
        manifest = _rescan_path(path, slug)
        return manifest


def _rescan_path(path: Path, slug: str) -> dict[str, Any]:
    manifest = _read_manifest(path)
    manifest.setdefault("slug", slug)
    manifest.setdefault("title", slug.replace("-", " ").title())
    manifest.setdefault("created_at", time.time())
    manifest.setdefault("stage", "extracted")
    on_disk = {p.name: p for p in audio_files(path)}
    cached = {t.get("name"): t for t in manifest.get("tracks", [])}
    ordered_names = [n for n in cached if n in on_disk]
    ordered_names += sorted(n for n in on_disk if n not in cached)
    tracks: list[dict[str, Any]] = []
    for name in ordered_names:
        file = on_disk[name]
        stat = file.stat()
        prior = cached.get(name)
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


def _get_at_path(path: Path, slug: str, refresh: bool = False) -> dict[str, Any]:
    manifest = _rescan_path(path, slug) if refresh or not (path / MANIFEST).exists() else _read_manifest(path)
    return _decorate(slug, path, manifest)


def get_at_path(path: Path, refresh: bool = False) -> dict[str, Any]:
    with _manifest_lock:
        manifest = _read_manifest(path)
        slug = manifest.get("slug") or path.name
        return _get_at_path(path, slug, refresh=refresh)


# -------------------------------------------------------------------- reads

def get(slug: str, refresh: bool = False) -> dict[str, Any] | None:
    with _manifest_lock:
        path = _public_collection_path(slug)
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
    # Computed last, so it covers the decorated track list the caller will
    # actually send. Both the index and the detail route go through here, so a
    # collection fingerprints identically wherever it is read.
    manifest["manifest_fingerprint"] = manifest_fingerprint(manifest)
    return manifest


def list_all() -> list[dict[str, Any]]:
    with _manifest_lock:
        config.ensure_dirs()
        out = []
        for path in sorted(config.LIBRARY_DIR.iterdir()):
            if not path.is_dir() or path.name.startswith("."):
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
        path = _public_collection_path(slug)
        manifest = _read_manifest(path)
        fn(manifest)
        _write_manifest(path, manifest)
        return get(slug)


def mutate_at_path(path: Path, fn) -> dict[str, Any]:
    with _manifest_lock:
        manifest = _read_manifest(path)
        fn(manifest)
        _write_manifest(path, manifest)
        return _get_at_path(path, manifest.get("slug") or path.name)


def rename_track_at_path(path: Path, name: str, title: str) -> dict[str, Any]:
    def apply(manifest: dict[str, Any]) -> None:
        for track in manifest.get("tracks", []):
            if track.get("name") == name:
                track["title"] = title
    return mutate_at_path(path, apply)


def replace_track_at_path(path: Path, name: str, new_names: list[str]) -> dict[str, Any]:
    def apply(manifest: dict[str, Any]) -> None:
        tracks = manifest.get("tracks", [])
        index = next((i for i, track in enumerate(tracks) if track["name"] == name), None)
        if index is None:
            return
        base = tracks[index]
        stand_ins = [
            {"name": new, "title": f"{base.get('title', name)} (part {position})"}
            for position, new in enumerate(new_names, start=1)
        ]
        manifest["tracks"] = tracks[:index] + stand_ins + tracks[index + 1:]
    return mutate_at_path(path, apply)


def set_forge_state_at_path(path: Path, state: dict[str, Any]) -> dict[str, Any]:
    def apply(manifest: dict[str, Any]) -> None:
        manifest["forge"] = state
        manifest["stage"] = "forged"
    return mutate_at_path(path, apply)


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
        path = _public_collection_path(slug)
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
        shutil.rmtree(_public_collection_path(slug))


# ------------------------------------------------------------------- paths

def track_path(slug: str, name: str) -> Path:
    base = _public_collection_path(slug)
    path = base / name
    if path.parent != base or not path.is_file():
        raise ValueError(f"No such track: {name}")
    return path


def cover_path(slug: str) -> Path | None:
    base = _public_collection_path(slug)
    cover = find_cover(base)
    return base / cover if cover else None


def next_index(path: Path) -> int:
    return len(audio_files(path)) + 1
