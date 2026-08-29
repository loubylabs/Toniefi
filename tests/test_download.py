"""Downloading one collection as an ordinary zip archive.

The archive is the escape hatch the library promises: plain files, playable
anywhere, with no part of the collection trapped behind this application.
Track order lives in the manifest rather than in the filenames, so the archive
has to renumber from the manifest or a reordered story unpacks out of order.
"""
from __future__ import annotations

import io
import json
import zipfile
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app import archive, config, db, library, main


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "UPLOAD_STAGE_DIR", tmp_path / "data" / "upload-staging", raising=False)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "portal.db")
    monkeypatch.setattr(config, "TONIE_LIMIT_SECONDS", 5400)
    monkeypatch.setattr(config, "TONIE_HEADROOM_SECONDS", 30)
    config.ensure_dirs()
    db.init()
    yield tmp_path
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


def make_collection(title: str = "Peter Pan", *, cover: str | None = "cover.jpg") -> str:
    """One published collection whose disk names already match its order."""
    slug = library.create(title, source="url")
    path = config.LIBRARY_DIR / slug
    tracks = []
    for index, chapter in enumerate(("The Boy Who Would Not Grow Up", "The Shadow"), start=1):
        name = f"{index:03d}-chapter.mp3"
        target = path / name
        target.write_bytes(f"audio for {chapter}".encode("utf-8"))
        stat = target.stat()
        tracks.append({
            "name": name,
            "title": chapter,
            "seconds": 600,
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
        })
    if cover:
        (path / cover).write_bytes(b"cover bytes")
    manifest_path = path / library.MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["stage"] = "forged"
    manifest["tracks"] = tracks
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return slug


def fetch_archive(slug: str):
    with TestClient(main.app) as client:
        response = client.get(f"/api/collections/{slug}/download")
    assert response.status_code == 200, response.text
    return response, zipfile.ZipFile(io.BytesIO(response.content))


def test_archive_carries_every_track_the_cover_and_the_manifest(isolated):
    slug = make_collection()

    _, bundle = fetch_archive(slug)

    assert bundle.namelist() == [
        "001-the-boy-who-would-not-grow-up.mp3",
        "002-the-shadow.mp3",
        "cover.jpg",
        "collection.json",
    ]
    assert bundle.read("cover.jpg") == b"cover bytes"
    assert json.loads(bundle.read("collection.json"))["slug"] == slug


def test_archive_preserves_the_original_audio_bytes(isolated):
    slug = make_collection()

    _, bundle = fetch_archive(slug)

    assert bundle.read("001-the-boy-who-would-not-grow-up.mp3") == b"audio for The Boy Who Would Not Grow Up"
    assert bundle.read("002-the-shadow.mp3") == b"audio for The Shadow"
    assert bundle.testzip() is None


def test_archive_numbers_tracks_from_the_reviewed_order_not_the_disk_names(isolated):
    """A Review reorder leaves the disk numbers stale. The archive must not."""
    slug = make_collection()
    library.reorder(slug, ["002-chapter.mp3", "001-chapter.mp3"])

    _, bundle = fetch_archive(slug)

    assert bundle.namelist()[:2] == [
        "001-the-shadow.mp3",
        "002-the-boy-who-would-not-grow-up.mp3",
    ]
    assert bundle.read("001-the-shadow.mp3") == b"audio for The Shadow"


def test_archive_stores_audio_rather_than_compressing_it_again(isolated):
    """MP3 is already compressed. Deflating it burns CPU and saves nothing."""
    slug = make_collection()

    _, bundle = fetch_archive(slug)

    assert [info.compress_type for info in bundle.infolist()] == [zipfile.ZIP_STORED] * 4


def test_archive_skips_a_track_the_manifest_lists_but_disk_no_longer_holds(isolated):
    slug = make_collection()
    (config.LIBRARY_DIR / slug / "001-chapter.mp3").unlink()

    _, bundle = fetch_archive(slug)

    assert bundle.namelist()[:1] == ["001-the-shadow.mp3"]


def test_archive_falls_back_to_the_disk_name_when_a_track_has_no_title(isolated):
    slug = make_collection()
    path = config.LIBRARY_DIR / slug / library.MANIFEST
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["tracks"][0]["title"] = "   "
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    _, bundle = fetch_archive(slug)

    assert bundle.namelist()[0] == "001-001-chapter.mp3"


def test_archive_works_for_a_collection_that_never_had_a_cover(isolated):
    slug = make_collection(cover=None)

    _, bundle = fetch_archive(slug)

    assert bundle.namelist() == [
        "001-the-boy-who-would-not-grow-up.mp3",
        "002-the-shadow.mp3",
        "collection.json",
    ]


def test_response_offers_the_archive_as_a_download_named_for_the_collection(isolated):
    slug = make_collection()

    response, _ = fetch_archive(slug)

    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == f'attachment; filename="{slug}.zip"'


def test_response_encodes_a_folder_name_that_would_break_the_header(isolated):
    """A folder dropped in by hand can hold a quote or an accent. Both survive."""
    slug = 'märchen "gute nacht"'
    path = config.LIBRARY_DIR / slug
    path.mkdir(parents=True)
    (path / "001-chapter.mp3").write_bytes(b"audio")

    with TestClient(main.app) as client:
        response = client.get(f"/api/collections/{quote(slug)}/download")

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="mrchen gute nacht.zip"')
    assert disposition.endswith("filename*=UTF-8''m%C3%A4rchen%20%22gute%20nacht%22.zip")
    assert zipfile.ZipFile(io.BytesIO(response.content)).namelist() == [
        "001-001-chapter.mp3",
        "collection.json",
    ]


def test_download_refuses_a_slug_outside_the_public_library(isolated):
    """The private staging folders and dotfiles are not the owner's library."""
    with TestClient(main.app) as client:
        for slug in (".toniefi-stage-abc", ".toniefi-forge-abc", ".hidden"):
            response = client.get(f"/api/collections/{slug}/download")
            assert response.status_code == 400, slug
            assert response.json()["detail"] == library.INVALID_PUBLIC_COLLECTION_SLUG_DETAIL


def test_stream_emits_the_archive_in_chunks_rather_than_one_buffer(isolated):
    """A multi-Tonie collection must never be assembled in memory to be sent."""
    slug = make_collection()
    (config.LIBRARY_DIR / slug / "001-chapter.mp3").write_bytes(b"x" * (3 * archive.CHUNK_BYTES))

    chunks = list(archive.stream(library.download_entries(slug)))

    assert len(chunks) > 3
    assert zipfile.ZipFile(io.BytesIO(b"".join(chunks))).testzip() is None


def test_download_reports_a_missing_collection_as_not_found(isolated):
    with TestClient(main.app) as client:
        response = client.get("/api/collections/never-prepared/download")

    assert response.status_code == 404


def test_download_entries_names_every_member_from_the_manifest(isolated):
    slug = make_collection()
    path = config.LIBRARY_DIR / slug

    entries = library.download_entries(slug)

    assert entries == [
        (path / "001-chapter.mp3", "001-the-boy-who-would-not-grow-up.mp3"),
        (path / "002-chapter.mp3", "002-the-shadow.mp3"),
        (path / "cover.jpg", "cover.jpg"),
        (path / library.MANIFEST, library.MANIFEST),
    ]


def test_download_entries_rejects_a_missing_collection(isolated):
    with pytest.raises(FileNotFoundError):
        library.download_entries("never-prepared")
