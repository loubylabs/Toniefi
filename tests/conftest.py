"""Every test gets its own database, so a hook never writes to a real one."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Callable

import httpx
import pytest

from app import config, db, ingest, podcast

Answer = Callable[[httpx.Request], httpx.Response]


def _drop_cached_connection() -> None:
    connection = getattr(db._local, "conn", None)
    if connection is not None:
        connection.close()
        del db._local.conn


@pytest.fixture(autouse=True)
def _isolated_database(monkeypatch, tmp_path):
    _drop_cached_connection()
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "toniefi.db")
    yield
    _drop_cached_connection()


def reply(body: str | bytes = b"", status: int = 200, content_type: str = "text/html") -> Answer:
    content = body.encode() if isinstance(body, str) else body
    return lambda request: httpx.Response(status, content=content, headers={"content-type": content_type})


def reply_json(payload) -> Answer:
    return reply(json.dumps(payload), content_type="application/json")


@pytest.fixture
def web(monkeypatch):
    """Serve canned answers keyed by host and path; anything else is a 404."""
    routes: dict[str, Answer] = {}
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = routes.get(f"{request.url.host}{request.url.path}")
        return answer(request) if answer else httpx.Response(404, text="not here")

    def client(timeout: float = 45.0) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True,
                            headers={"User-Agent": ingest.USER_AGENT})

    monkeypatch.setattr(podcast, "_client", client)
    return SimpleNamespace(routes=routes, seen=seen)


def spotify_page(entity) -> str:
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return ('<html><body><script id="__NEXT_DATA__" type="application/json">'
            f"{json.dumps(data)}</script></body></html>")
