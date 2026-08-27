"""TonieCloud.get_tonie: the guard against a 2xx with no body.

_request returns None for an empty response (app/tonies.py:114-115), and a
non-dict there would surface as an AttributeError, a 500, at every caller.
get_tonie is the one place that guard belongs, so it is tested directly here,
against a monkeypatched _request rather than the network.
"""
from __future__ import annotations

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
