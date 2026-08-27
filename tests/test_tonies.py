"""Two TonieCloud guards, both about not turning a non-problem into one.

get_tonie: _request returns None for an empty response, and a non-dict there
would surface as an AttributeError: a 500 at the chapter-write caller, and a
failed job with an ugly message at the send caller, which runs in a worker
that catches Exception rather than in an HTTP request.

close: a failed teardown must not escape, or it masks a write that already
landed on a Tonie the Tonie Cloud cannot un-change.

Both live on the client, so both are tested against it directly here, with
_request or the pool monkeypatched rather than the network.
"""
from __future__ import annotations

import httpx
import pytest

from app.tonies import TonieCloud, TonieCloudError


@pytest.fixture
def cloud() -> TonieCloud:
    """The constructor only stores the credentials and builds an httpx
    client; it makes no request. Monkeypatching _request below means this
    test never touches the network."""
    return TonieCloud("user", "pass")


def test_an_empty_2xx_body_is_an_error_not_a_crash(cloud, monkeypatch):
    monkeypatch.setattr(cloud, "_request", lambda *a, **k: None)
    with pytest.raises(TonieCloudError):
        cloud.get_tonie("h1", "t1")


def test_a_non_dict_body_is_an_error_not_a_crash(cloud, monkeypatch):
    """The Tonie Cloud is only ever expected to answer with an object, but
    the guard checks shape, not just None-ness, so prove it catches a list
    too."""
    monkeypatch.setattr(cloud, "_request", lambda *a, **k: [])
    with pytest.raises(TonieCloudError):
        cloud.get_tonie("h1", "t1")


def test_a_dict_body_is_returned_unchanged(cloud, monkeypatch):
    tonie = {"id": "t1", "name": "Creative Tonie"}
    monkeypatch.setattr(cloud, "_request", lambda *a, **k: tonie)
    assert cloud.get_tonie("h1", "t1") is tonie


def test_close_swallows_a_teardown_failure(cloud, monkeypatch):
    """Closing is the last thing every caller does, from a `finally`, so a
    teardown error can only land on top of a finished answer. If it escaped,
    a chapter PATCH that had already reached the Tonie Cloud would be
    reported as a failure and the user would retry a change with no undo.
    """
    class BrokenPool:
        def close(self) -> None:
            raise httpx.TransportError("connection pool teardown failed")

    monkeypatch.setattr(cloud, "_client", BrokenPool())
    cloud.close()  # must not raise
