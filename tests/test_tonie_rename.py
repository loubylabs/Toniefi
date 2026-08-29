import pytest

from app import push, tonies


class FakeClient:
    def __init__(self, tonie, households=None, fail_with=None):
        self._tonie = tonie
        self._households = households or [{"id": "h1", "name": "Home"}]
        self.written = None
        self.closed = False
        self._fail_with = fail_with

    def get_tonie(self, household_id, tonie_id):
        return dict(self._tonie)

    def households(self):
        return list(self._households)

    def set_name(self, household_id, tonie_id, name):
        if self._fail_with:
            raise self._fail_with
        self.written = (household_id, tonie_id, name)

    def close(self):
        self.closed = True


def install(monkeypatch, client):
    monkeypatch.setattr(push, "client_from_settings", lambda: client)
    return client


def a_tonie(**overrides):
    base = {
        "id": "t1",
        "name": "Creative Tonie",
        "imageUrl": "https://cdn.tonies.de/thumbnails/50000072.png",
        "chapters": [],
        "secondsPresent": 0,
    }
    base.update(overrides)
    return base


def test_rename_writes_only_the_name(monkeypatch):
    client = install(monkeypatch, FakeClient(a_tonie()))
    answer = push.set_tonie_name("h1", "t1", "Creative Tonie", "  Bedtime Bear  ")
    assert client.written == ("h1", "t1", "Bedtime Bear")
    assert answer["name"] == "Bedtime Bear"
    assert answer["householdName"] == "Home"
    assert client.closed is True


def test_rename_is_capped_at_the_upstream_limit(monkeypatch):
    client = install(monkeypatch, FakeClient(a_tonie()))
    push.set_tonie_name("h1", "t1", "Creative Tonie", "x" * 250)
    assert client.written[2] == "x" * push.NAME_LIMIT
    assert push.NAME_LIMIT == 100


def test_rename_refuses_an_empty_name(monkeypatch):
    client = install(monkeypatch, FakeClient(a_tonie()))
    with pytest.raises(ValueError):
        push.set_tonie_name("h1", "t1", "Creative Tonie", "   ")
    assert client.written is None


def test_rename_refuses_when_the_name_changed_elsewhere(monkeypatch):
    client = install(monkeypatch, FakeClient(a_tonie(name="Renamed in the app")))
    with pytest.raises(push.StaleTonieName):
        push.set_tonie_name("h1", "t1", "Creative Tonie", "Bedtime Bear")
    assert client.written is None


def test_rename_surfaces_a_rate_limit_readably(monkeypatch):
    failure = tonies.TonieCloudError(
        "PATCH /households/h1/creativetonies/t1 failed (429): slow down"
    )
    client = install(monkeypatch, FakeClient(a_tonie(), fail_with=failure))
    with pytest.raises(tonies.TonieCloudError) as caught:
        push.set_tonie_name("h1", "t1", "Creative Tonie", "Bedtime Bear")
    assert "429" in str(caught.value)
    assert client.closed is True


def test_client_set_name_sends_only_the_name_key():
    sent = {}

    class Recorder(tonies.TonieCloud):
        def __init__(self):
            pass

        def _request(self, method, path, **kwargs):
            sent["method"] = method
            sent["path"] = path
            sent["json"] = kwargs.get("json")

    Recorder().set_name("h1", "t1", "Bedtime Bear")
    assert sent["method"] == "PATCH"
    assert sent["path"] == "/households/h1/creativetonies/t1"
    assert sent["json"] == {"name": "Bedtime Bear"}


def test_route_renames_and_reports_conflict(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    client = FakeClient(a_tonie())
    install(monkeypatch, client)
    http = TestClient(app)

    ok = http.patch(
        "/api/tonies/h1/t1",
        json={"base_name": "Creative Tonie", "name": "Bedtime Bear"},
    )
    assert ok.status_code == 200
    assert ok.json()["name"] == "Bedtime Bear"

    install(monkeypatch, FakeClient(a_tonie(name="Changed elsewhere")))
    stale = http.patch(
        "/api/tonies/h1/t1",
        json={"base_name": "Creative Tonie", "name": "Bedtime Bear"},
    )
    assert stale.status_code == 409

    install(monkeypatch, FakeClient(a_tonie()))
    empty = http.patch(
        "/api/tonies/h1/t1",
        json={"base_name": "Creative Tonie", "name": "  "},
    )
    assert empty.status_code == 400
