"""Toniefi: the FastAPI application, serving the JSON API and the single-page front end."""
from __future__ import annotations

import json
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, unquote_to_bytes, urlparse

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import archive, audio, config, db, ingest, jobs, library, push, tonies

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    jobs.start()
    yield
    jobs.stop()


app = FastAPI(title="Toniefi", version="1.0.0", lifespan=lifespan)


def invalid_collection_slug_response() -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"detail": library.INVALID_PUBLIC_COLLECTION_SLUG_DETAIL},
    )


@app.exception_handler(library.InvalidPublicCollectionSlug)
async def invalid_collection_slug_handler(
    _: Request,
    __: library.InvalidPublicCollectionSlug,
) -> JSONResponse:
    return invalid_collection_slug_response()


def raw_collection_slug(request: Request) -> str | None:
    raw_path = request.scope.get("raw_path")
    if not isinstance(raw_path, bytes):
        raw_path = request.url.path.encode("utf-8")
    prefix = b"/api/collections/"
    if not raw_path.startswith(prefix):
        return None
    segment = raw_path[len(prefix):].split(b"/", 1)[0]
    try:
        return unquote_to_bytes(segment).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise library.InvalidPublicCollectionSlug() from exc


@app.middleware("http")
async def enforce_collection_path_boundary(request: Request, call_next):
    try:
        slug = raw_collection_slug(request)
        if slug is not None:
            library.validate_public_collection_slug(slug)
    except library.InvalidPublicCollectionSlug:
        return invalid_collection_slug_response()
    return await call_next(request)


def fail(status: int, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


def valid_source_url(value: str) -> bool:
    """Match the browser's accepted HTTP and HTTPS URL boundary."""
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(hostname)
        and not any(character.isspace() or ord(character) < 32 for character in parsed.netloc)
    )


# ------------------------------------------------------------------ models

class RequestModel(BaseModel):
    """The base every request body inherits, so extras are refused everywhere.

    A request that carries a retired field beside its replacement comes from a
    caller nobody migrated, and accepting it while silently discarding the
    retired field is how that caller stays wrong without anyone finding out.
    The only client is the browser in this repository, so there is no installed
    base to keep working and no reason to be lenient.
    """

    model_config = ConfigDict(extra="forbid")


class TitlePatch(RequestModel):
    title: str


class ReorderRequest(RequestModel):
    names: list[str]


class PrepareSource(RequestModel):
    url: str
    # Which playlist entries to download, as the numbers the preview showed.
    # None means nobody picked, and the link speaks for itself: one video, or
    # a whole playlist. A list is a pick, and a pick of nothing is a
    # contradiction rather than a default, so prepare_sources refuses it. The
    # two used to share the empty list, which turned "none of them" into "all
    # of them" for any link a --no-playlist flag does not restrain.
    playlist_items: list[int] | None = None


class PlaylistPreviewRequest(RequestModel):
    url: str


class PrepareOptions(RequestModel):
    use_chapters: bool = True
    normalize: bool = True
    clean_titles: bool = True
    trim_head: float = 0
    trim_tail: float = 0
    split_oversized: bool = True


class PrepareBatch(RequestModel):
    sources: list[PrepareSource]
    options: PrepareOptions = Field(default_factory=PrepareOptions)


class UploadPrepare(RequestModel):
    """The multipart intake form, declared so it is refused the same way.

    A form is a request body like any other, and the base class is what makes
    a stray field a 422 here rather than something the server drops in silence.
    """

    files: list[UploadFile]
    title: str = ""
    options: str = "{}"


class LibrivoxImport(RequestModel):
    book_id: str
    options: PrepareOptions = Field(default_factory=PrepareOptions)


class ForgeRequest(RequestModel):
    slug: str
    normalize: bool = True
    clean_titles: bool = True
    trim_head: float = 0
    trim_tail: float = 0
    split_oversized: bool = True


class ChapterRef(RequestModel):
    id: str
    title: str = ""


class PushSource(RequestModel):
    slug: str
    manifest_fingerprint: str = Field(min_length=64, max_length=64)
    files: list[str] = Field(min_length=1)


class PushAssignment(RequestModel):
    household_id: str
    tonie_id: str
    replace: bool
    remote_chapters: list[ChapterRef]
    # One assignment is one Creative Tonie, and a Tonie can hold chapters from
    # several collections, so the collection identity lives here rather than on
    # the batch.
    sources: list[PushSource] = Field(min_length=1)


class PushBatch(RequestModel):
    operation_key: str = Field(min_length=1, max_length=128)
    assignments: list[PushAssignment] = Field(min_length=1, max_length=100)


class ChaptersPut(RequestModel):
    # `base` carries titles as well as ids, because a rename made elsewhere is
    # invisible to an id-only precondition and would be silently reverted.
    base: list[ChapterRef]
    chapters: list[ChapterRef]


class Credentials(RequestModel):
    username: str
    password: str


# ------------------------------------------------------------------ status

@app.get("/api/status")
def status() -> dict[str, Any]:
    return {
        "library_dir": str(config.LIBRARY_DIR),
        "tonie_limit_seconds": config.TONIE_LIMIT_SECONDS,
        "usable_limit_seconds": config.usable_limit(),
        "tonie_limit_human": audio.human_duration(config.TONIE_LIMIT_SECONDS),
        "upload_max_bytes": jobs.UPLOAD_MAX_BYTES,
        "upload_max_human": jobs.upload_limit_label(jobs.UPLOAD_MAX_BYTES),
        "upload_max_files": jobs.UPLOAD_MAX_FILES,
        "upload_stage_retention_seconds": jobs.UPLOAD_STAGE_RETENTION_SECONDS,
        "upload_stage_dir": str(config.upload_stage_dir()),
        "tools": audio.have_tools(),
        "credentials": push.credential_status(),
    }


@app.post("/api/settings/credentials")
def save_credentials(body: Credentials) -> dict[str, Any]:
    db.replace_credentials(body.username.strip(), body.password)
    return {"ok": True}


@app.delete("/api/settings/credentials")
def delete_credentials() -> dict[str, Any]:
    db.delete_credentials()
    return push.credential_status()


@app.post("/api/settings/test")
def test_credentials() -> dict[str, Any]:
    client = None
    try:
        client = push.client_from_settings()
        profile = client.check_login()
    except tonies.TonieCloudError as exc:
        raise fail(400, str(exc)) from exc
    finally:
        if client is not None:
            client.close()
    return {"ok": True, "email": profile.get("email", "")}


# ---------------------------------------------------------- 1. preparation

@app.post("/api/prepare")
def prepare_sources(body: PrepareBatch) -> dict[str, Any]:
    sources = [source.url.strip() for source in body.sources]
    if not sources:
        raise fail(400, "At least one source URL is required.")
    if not all(valid_source_url(url) for url in sources):
        raise fail(400, "Sources must use HTTP or HTTPS.")
    if len(set(sources)) != len(sources):
        raise fail(400, "Duplicate source URLs are not allowed.")
    if len(sources) > 50:
        raise fail(400, "A batch can contain at most 50 sources.")
    for source in body.sources:
        if source.playlist_items is None:
            continue
        if not source.playlist_items:
            raise fail(400, "Pick at least one video, or remove that playlist source.")
        if any(item < 1 for item in source.playlist_items):
            raise fail(400, "Playlist entry numbers start at 1.")

    options = body.options.model_dump()
    entries = [
        ("prepare_url", url, {"url": url, "playlist_items": source.playlist_items, "options": options})
        for url, source in zip(sources, body.sources, strict=True)
    ]
    ids = jobs.enqueue_many(entries)
    created = [{"id": job_id, "url": url} for job_id, url in zip(ids, sources, strict=True)]
    return {"jobs": created}


@app.post("/api/playlist/preview")
def playlist_preview(body: PlaylistPreviewRequest) -> dict[str, Any]:
    """List a playlist's entries so the desk can offer them for picking."""
    url = body.url.strip()
    if not valid_source_url(url):
        raise fail(400, "Sources must use HTTP or HTTPS.")
    try:
        return ingest.playlist_preview(url)
    except RuntimeError as exc:
        raise fail(502, str(exc)) from exc


@app.get("/api/librivox/search")
def librivox_search(q: str, limit: int = 20) -> list[dict[str, Any]]:
    try:
        return ingest.librivox_search(q, limit)
    except RuntimeError as exc:
        raise fail(502, str(exc)) from exc


@app.post("/api/librivox/import")
def librivox_import(body: LibrivoxImport) -> dict[str, Any]:
    job_id = jobs.enqueue("librivox", f"LibriVox import {body.book_id}",
                          {"book_id": body.book_id, "options": body.options.model_dump()})
    return {"job_id": job_id}


@app.post("/api/uploads/prepare")
async def prepare_uploads(body: Annotated[UploadPrepare, Form()]) -> dict[str, Any]:
    files, title = body.files, body.title
    try:
        forge_options = PrepareOptions.model_validate(json.loads(body.options)).model_dump()
    except (json.JSONDecodeError, ValidationError) as exc:
        raise fail(400, "Upload options are invalid.") from exc
    if not files:
        raise fail(400, "Choose at least one audio file.")
    if len(files) > jobs.UPLOAD_MAX_FILES:
        raise fail(400, f"A collection can contain at most {jobs.UPLOAD_MAX_FILES} uploaded files.")

    described = []
    for index, upload in enumerate(files):
        name = upload.filename or f"upload-{index + 1}.mp3"
        suffix = Path(name).suffix.lower()
        if suffix not in audio.AUDIO_EXTENSIONS:
            raise fail(400, f"{suffix or 'That file'} is not a supported audio format.")
        described.append({
            "name": name,
            "stored": f"{index:03d}{suffix}",
            "target": f"{index + 1:03d}-{audio.slugify(Path(name).stem)}{suffix}",
        })

    jobs.sweep_upload_staging()
    _, stage = jobs.create_upload_stage()
    try:
        total_bytes = 0
        for upload, item in zip(files, described, strict=True):
            destination = stage / item["stored"]
            with destination.open("wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    total_bytes += len(chunk)
                    if total_bytes > jobs.UPLOAD_MAX_BYTES:
                        limit = jobs.upload_limit_label(jobs.UPLOAD_MAX_BYTES)
                        raise fail(
                            413,
                            f"The selected files exceed the upload limit of {limit}. "
                            "Choose fewer or smaller files and submit the collection again.",
                        )
                    output.write(chunk)
                    jobs.mark_upload_stage(stage)
        payload = {
            "title": title.strip(),
            "files": described,
            "options": forge_options,
        }
        label = title.strip() or described[0]["name"]
        job_id = jobs.enqueue_upload_stage(stage, f"Upload {label}", payload)
    except Exception:
        jobs.remove_upload_stage(stage)
        raise
    return {"job_id": job_id}


# --------------------------------------------------------------- 3. forge

@app.post("/api/forge")
def run_forge(body: ForgeRequest) -> dict[str, Any]:
    collection = library.get(body.slug)
    if not collection:
        raise fail(404, f"No collection named {body.slug}.")
    if collection.get("stage") == "forged":
        raise fail(409, "Forge is already complete for this collection.")
    job_id = db.create_forge_job_once(f"Forge {body.slug}", body.model_dump())
    return {"job_id": job_id}


# --------------------------------------------------------- 4. collections

@app.get("/api/collections")
def list_collections() -> list[dict[str, Any]]:
    return library.list_all()


@app.get("/api/collections/{slug}")
def get_collection(slug: str, refresh: bool = False) -> dict[str, Any]:
    manifest = library.get(slug, refresh=refresh)
    if not manifest:
        raise fail(404, f"No collection named {slug}.")
    manifest["plan"] = library.plan(slug)
    return manifest


@app.patch("/api/collections/{slug}")
def rename_collection(slug: str, body: TitlePatch) -> dict[str, Any]:
    try:
        return library.set_title(slug, body.title)
    except ValueError as exc:
        raise fail(400, str(exc)) from exc


@app.post("/api/collections/{slug}/reorder")
def reorder_tracks(slug: str, body: ReorderRequest) -> dict[str, Any]:
    try:
        return library.reorder(slug, body.names)
    except ValueError as exc:
        raise fail(400, str(exc)) from exc


@app.patch("/api/collections/{slug}/tracks/{name}")
def rename_track(slug: str, name: str, body: TitlePatch) -> dict[str, Any]:
    try:
        return library.rename_track(slug, name, body.title)
    except ValueError as exc:
        raise fail(400, str(exc)) from exc


@app.delete("/api/collections/{slug}/tracks/{name}")
def delete_track(slug: str, name: str) -> dict[str, Any]:
    try:
        return library.delete_track(slug, name)
    except ValueError as exc:
        raise fail(400, str(exc)) from exc


@app.delete("/api/collections/{slug}")
def delete_collection(slug: str) -> dict[str, Any]:
    try:
        library.delete(slug)
    except (ValueError, FileNotFoundError) as exc:
        raise fail(404, str(exc)) from exc
    return {"ok": True}


@app.get("/api/collections/{slug}/cover")
def collection_cover(slug: str):
    try:
        path = library.cover_path(slug)
    except ValueError as exc:
        raise fail(404, str(exc)) from exc
    if not path:
        raise fail(404, "No cover art for this collection.")
    return FileResponse(path)


@app.get("/api/collections/{slug}/tracks/{name}/audio")
def stream_track(slug: str, name: str):
    """Preview a track in the browser before committing it to a Tonie."""
    try:
        path = library.track_path(slug, name)
    except ValueError as exc:
        raise fail(404, str(exc)) from exc
    return FileResponse(path)


def content_disposition(filename: str) -> str:
    """Offer a download under `filename`, encoded only when it needs it.

    A collection folder dropped in by hand can be named anything printable, so
    a quote or a non-ASCII character must not be able to break the header apart.
    """
    fallback = "".join(
        character for character in filename
        if character.isascii() and character.isprintable() and character not in '"\\'
    )
    header = f'attachment; filename="{fallback or "collection.zip"}"'
    if fallback == filename:
        return header
    return f"{header}; filename*=UTF-8''{quote(filename, safe='')}"


@app.get("/api/collections/{slug}/download")
def download_collection(slug: str):
    """Hand one whole collection back as an ordinary zip: audio, cover, manifest.

    The archive streams as it is built, so a multi-Tonie collection never has
    to fit in memory or in the small RAM-backed scratch directory.
    """
    try:
        members = library.download_entries(slug)
    except FileNotFoundError as exc:
        raise fail(404, str(exc)) from exc
    return StreamingResponse(
        archive.stream(members),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(f"{slug}.zip")},
    )


# ---------------------------------------------------------------- 5. send

@app.get("/api/tonies")
def list_tonies() -> list[dict[str, Any]]:
    try:
        client = push.client_from_settings()
        result = client.all_creative_tonies()
        client.close()
    except tonies.TonieCloudError as exc:
        raise fail(400, str(exc)) from exc
    return [push.describe_tonie(tonie) for tonie in result]


def _push_job_title(assignment: dict[str, Any]) -> str:
    """Name a send by what it carries, since a batch is no longer one slug."""
    slugs = {source["slug"] for source in assignment["sources"]}
    if len(slugs) == 1:
        return f"Send {next(iter(slugs))} to a Creative Tonie"
    return f"Send {len(slugs)} collections to a Creative Tonie"


@app.post("/api/push/batch")
def push_batch(body: PushBatch) -> dict[str, Any]:
    assignments = [assignment.model_dump() for assignment in body.assignments]
    for assignment in assignments:
        for source in assignment["sources"]:
            library.validate_public_collection_slug(source["slug"])
    targets = [(item["household_id"], item["tonie_id"]) for item in assignments]
    if len(targets) != len(set(targets)):
        raise fail(400, "Each capacity group needs a different Creative Tonie.")
    try:
        canonical = body.model_dump(exclude={"operation_key"})
        digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        existing = db.existing_idempotent_jobs(body.operation_key, digest)
        if existing is not None:
            return {"operation_key": body.operation_key, "job_ids": existing}
        push.validate_confirmed_batch(assignments)
        entries = [
            ("push", _push_job_title(assignment), assignment)
            for assignment in assignments
        ]
        job_ids, _ = db.create_idempotent_jobs(body.operation_key, digest, entries)
    except (push.StalePush, ValueError) as exc:
        raise fail(409, str(exc)) from exc
    except db.OperationConflict as exc:
        raise fail(409, str(exc)) from exc
    return {"operation_key": body.operation_key, "job_ids": job_ids}


@app.put("/api/tonies/{household_id}/{tonie_id}/chapters")
def put_tonie_chapters(household_id: str, tonie_id: str, body: ChaptersPut) -> dict[str, Any]:
    """Rename, reorder, remove or clear the chapters on a Creative Tonie."""
    try:
        return push.set_tonie_chapters(
            household_id, tonie_id,
            [chapter.model_dump() for chapter in body.base],
            [chapter.model_dump() for chapter in body.chapters],
        )
    except push.StaleChapters as exc:
        raise fail(409, str(exc)) from exc
    except ValueError as exc:
        raise fail(400, str(exc)) from exc
    except tonies.TonieCloudError as exc:
        raise fail(400, str(exc)) from exc


# -------------------------------------------------------------------- jobs

@app.get("/api/jobs")
def list_jobs(limit: int = 40) -> list[dict[str, Any]]:
    return [jobs.present(job) for job in db.jobs_for_refresh(limit)]


@app.get("/api/jobs/history")
def list_job_history(limit: int = 40) -> list[dict[str, Any]]:
    return [jobs.present(job) for job in db.jobs_for_history(limit)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job:
        raise fail(404, "No such job.")
    return jobs.present(job)


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: int) -> dict[str, Any]:
    try:
        retry_id = jobs.retry_failed_job(job_id)
    except jobs.PushRetryConflict as exc:
        raise fail(409, str(exc)) from exc
    if not retry_id:
        raise fail(400, "Only failed jobs can be retried.")
    job = db.get_job(retry_id)
    if not job:
        raise fail(404, "No such job.")
    return jobs.present(job)


# ------------------------------------------------------------------- pages

@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


@app.get("/")
@app.get("/desk")
@app.get("/library")
@app.get("/tonies")
@app.get("/activity")
@app.get("/settings")
def application_document() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/collection/{slug}")
def collection_document(slug: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
