"""Toniefi — FastAPI application: JSON API plus the single-page front end."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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


class ProbeRequest(BaseModel):
    url: str


class UrlIngest(BaseModel):
    url: str
    title: str | None = None
    slug: str | None = None
    use_chapters: bool = True


class LibrivoxImport(BaseModel):
    book_id: str


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


# --------------------------------------------------------- 1. paste/probe

@app.post("/api/probe")
def probe(body: ProbeRequest) -> dict[str, Any]:
    """Look at a URL without downloading, so step 1 can show what's coming."""
    if not body.url.strip():
        raise fail(400, "A URL is required.")
    try:
        return ingest.probe_url(body.url.strip())
    except RuntimeError as exc:
        raise fail(400, str(exc)) from exc


# ------------------------------------------------------------- 2. extract

@app.post("/api/ingest/url")
def ingest_url(body: UrlIngest) -> dict[str, Any]:
    if not body.url.strip():
        raise fail(400, "A URL is required.")
    job_id = jobs.enqueue("url", body.title or body.url, body.model_dump())
    return {"job_id": job_id}


@app.get("/api/librivox/search")
def librivox_search(q: str, limit: int = 20) -> list[dict[str, Any]]:
    try:
        return ingest.librivox_search(q, limit)
    except RuntimeError as exc:
        raise fail(502, str(exc)) from exc


@app.post("/api/librivox/import")
def librivox_import(body: LibrivoxImport) -> dict[str, Any]:
    job_id = jobs.enqueue("librivox", f"LibriVox import {body.book_id}",
                          {"book_id": body.book_id})
    return {"job_id": job_id}


@app.post("/api/ingest/upload")
async def ingest_upload(
    file: UploadFile = File(...),
    slug: str | None = Form(None),
    title: str | None = Form(None),
) -> dict[str, Any]:
    data = await file.read()
    try:
        return ingest.import_upload(file.filename or "upload.mp3", data, slug, title)
    except RuntimeError as exc:
        raise fail(400, str(exc)) from exc


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
    for tonie in result:
        tonie["chapter_count"] = len(tonie.get("chapters", []))
        seconds = float(tonie.get("secondsPresent") or 0)
        tonie["seconds_present"] = seconds
        tonie["time_used"] = audio.human_duration(seconds)
        tonie["seconds_free"] = max(0, config.TONIE_LIMIT_SECONDS - seconds)
        tonie["time_free"] = audio.human_duration(tonie["seconds_free"])
    return result


@app.post("/api/push")
def push_to_tonie(body: PushRequest) -> dict[str, Any]:
    job_id = jobs.enqueue("push", f"Send {body.slug} to a Tonie", body.model_dump())
    return {"job_id": job_id}


# -------------------------------------------------------------------- jobs

@app.get("/api/jobs")
def list_jobs(limit: int = 40) -> list[dict[str, Any]]:
    return db.recent_jobs(limit)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job:
        raise fail(404, "No such job.")
    return job


# ------------------------------------------------------------------- pages

@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
