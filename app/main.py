"""Toniefi: the FastAPI application, serving the JSON API and the single-page front end."""
from __future__ import annotations

import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import audio, config, db, ingest, jobs, library, push, tonies

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    jobs.start()
    yield
    jobs.stop()


app = FastAPI(title="Toniefi", version="1.0.0", lifespan=lifespan)


def fail(status: int, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


# ------------------------------------------------------------------ models

class TitlePatch(BaseModel):
    title: str


class ReorderRequest(BaseModel):
    names: list[str]


class PrepareSource(BaseModel):
    url: str


class PrepareOptions(BaseModel):
    use_chapters: bool = True
    normalize: bool = True
    clean_titles: bool = True
    trim_head: float = 0
    trim_tail: float = 0
    split_oversized: bool = True


class PrepareBatch(BaseModel):
    sources: list[PrepareSource]
    options: PrepareOptions = Field(default_factory=PrepareOptions)


class LibrivoxImport(BaseModel):
    book_id: str
    options: PrepareOptions = Field(default_factory=PrepareOptions)


class ForgeRequest(BaseModel):
    slug: str
    normalize: bool = True
    clean_titles: bool = True
    trim_head: float = 0
    trim_tail: float = 0
    split_oversized: bool = True


class PushRequest(BaseModel):
    slug: str
    household_id: str
    tonie_id: str
    names: list[str] | None = None
    group_index: int | None = None
    replace: bool = True


class ChapterRef(BaseModel):
    id: str
    title: str = ""


class ChaptersPut(BaseModel):
    # `base` carries titles as well as ids, because a rename made elsewhere is
    # invisible to an id-only precondition and would be silently reverted.
    base: list[ChapterRef]
    chapters: list[ChapterRef]


class Credentials(BaseModel):
    username: str
    password: str


# ------------------------------------------------------------------ status

@app.get("/api/status")
def status() -> dict[str, Any]:
    from_env = bool(config.TONIES_USERNAME and config.TONIES_PASSWORD)
    from_db = bool(db.get_setting("tonies_username") and db.get_setting("tonies_password"))
    return {
        "library_dir": str(config.LIBRARY_DIR),
        "tonie_limit_seconds": config.TONIE_LIMIT_SECONDS,
        "usable_limit_seconds": config.usable_limit(),
        "tonie_limit_human": audio.human_duration(config.TONIE_LIMIT_SECONDS),
        "tools": audio.have_tools(),
        "credentials": {
            "configured": from_env or from_db,
            "source": "environment" if from_env else ("saved" if from_db else "none"),
            "username": config.TONIES_USERNAME or db.get_setting("tonies_username"),
        },
    }


@app.post("/api/settings/credentials")
def save_credentials(body: Credentials) -> dict[str, Any]:
    db.set_setting("tonies_username", body.username.strip())
    db.set_setting("tonies_password", body.password)
    return {"ok": True}


@app.post("/api/settings/test")
def test_credentials() -> dict[str, Any]:
    try:
        client = push.client_from_settings()
        profile = client.check_login()
        client.close()
    except tonies.TonieCloudError as exc:
        raise fail(400, str(exc)) from exc
    return {"ok": True, "email": profile.get("email", "")}


# ---------------------------------------------------------- 1. preparation

@app.post("/api/prepare")
def prepare_sources(body: PrepareBatch) -> dict[str, Any]:
    sources = [source.url.strip() for source in body.sources]
    if not sources:
        raise fail(400, "At least one source URL is required.")
    try:
        parsed_sources = [urlparse(url) for url in sources]
        valid_urls = all(
            parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            for parsed in parsed_sources
        )
    except ValueError:
        valid_urls = False
    if not valid_urls:
        raise fail(400, "Sources must use HTTP or HTTPS.")
    if len(set(sources)) != len(sources):
        raise fail(400, "Duplicate source URLs are not allowed.")
    if len(sources) > 50:
        raise fail(400, "A batch can contain at most 50 sources.")

    options = body.options.model_dump()
    entries = [
        ("prepare_url", url, {"url": url, "options": options})
        for url in sources
    ]
    ids = jobs.enqueue_many(entries)
    created = [{"id": job_id, "url": url} for job_id, url in zip(ids, sources, strict=True)]
    return {"jobs": created}


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
async def prepare_uploads(
    files: list[UploadFile] = File(...),
    title: str = Form(""),
    options: str = Form("{}"),
) -> dict[str, Any]:
    try:
        forge_options = PrepareOptions.model_validate(json.loads(options)).model_dump()
    except (json.JSONDecodeError, ValidationError) as exc:
        raise fail(400, "Upload options are invalid.") from exc
    if not files:
        raise fail(400, "Choose at least one audio file.")

    described = []
    for index, upload in enumerate(files):
        name = upload.filename or f"upload-{index + 1}.mp3"
        suffix = Path(name).suffix.lower()
        if suffix not in audio.AUDIO_EXTENSIONS:
            raise fail(400, f"{suffix or 'That file'} is not a supported audio format.")
        described.append({"name": name, "stored": f"{index:03d}{suffix}"})

    stage_name = f"upload-{uuid4().hex}"
    stage = config.WORK_DIR / stage_name
    stage.mkdir(parents=True)
    try:
        for upload, item in zip(files, described, strict=True):
            destination = stage / item["stored"]
            with destination.open("wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    output.write(chunk)
        payload = {
            "title": title.strip(),
            "stage": stage_name,
            "files": described,
            "options": forge_options,
        }
        label = title.strip() or described[0]["name"]
        job_id = jobs.enqueue("upload_prepare", f"Upload {label}", payload)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {"job_id": job_id}


# --------------------------------------------------------------- 3. forge

@app.post("/api/forge")
def run_forge(body: ForgeRequest) -> dict[str, Any]:
    if not library.get(body.slug):
        raise fail(404, f"No collection named {body.slug}.")
    job_id = jobs.enqueue("forge", f"Forge {body.slug}", body.model_dump())
    return {"job_id": job_id}


# -------------------------------------------------------------- 4. review

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


@app.post("/api/push")
def push_to_tonie(body: PushRequest) -> dict[str, Any]:
    job_id = jobs.enqueue("push", f"Send {body.slug} to a Tonie", body.model_dump())
    return {"job_id": job_id}


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


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job:
        raise fail(404, "No such job.")
    return jobs.present(job)


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: int) -> dict[str, Any]:
    retry_id = db.retry_failed_job(job_id)
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
@app.get("/review")
@app.get("/library")
@app.get("/tonies")
@app.get("/activity")
@app.get("/settings")
def application_document() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/review/{slug}")
def collection_review_document(slug: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
