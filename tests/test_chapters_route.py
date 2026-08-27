"""The route, against a stub Tonie Cloud. No network, no real account.

The Tonie Cloud has no sandbox and no undo, so the destructive paths are
proved here and never against the owner's own Creative Tonie.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main, push, tonies


class StubCloud:
    """Stands in for tonies.TonieCloud. Records what it was asked to write."""

    def __init__(self, chapters: list[dict]) -> None:
        self.chapters = chapters
        self.set_calls: list[list[dict]] = []
        self.gets = 0
        self.closed = False
        self.reads = 0
        # Set to a message to make the write fail, the way the real cloud can.
        self.set_error: str | None = None

    def _payload(self, tonie_id: str = "t1") -> dict:
        """One Tonie as the Tonie Cloud reports it.

        `lastUpdate` changes on every read, because the real Tonie Cloud stamps
        it. Anything comparing two reads has to compare keys, not values.
        """
        self.reads += 1
        return {"id": tonie_id, "name": "Creative Tonie",
                "lastUpdate": f"2026-08-26T12:00:0{self.reads}Z",
                "secondsPresent": sum(c["seconds"] for c in self.chapters),
                "chapters": [dict(c) for c in self.chapters]}

    def households(self) -> list[dict]:
        return [{"id": "h1", "name": "Emily' household"}]

    def all_creative_tonies(self) -> list[dict]:
        """Mirrors the real client, which stamps the household onto each Tonie."""
        out = []
        for house in self.households():
            tonie = self._payload()
            tonie["householdId"] = house["id"]
            tonie["householdName"] = house["name"]
            out.append(tonie)
        return out

    def get_tonie(self, household_id: str, tonie_id: str) -> dict:
        self.gets += 1
        return self._payload(tonie_id)

    def set_chapters(self, household_id: str, tonie_id: str, chapters: list[dict]):
        if self.set_error:
            raise tonies.TonieCloudError(self.set_error)
        self.set_calls.append([dict(c) for c in chapters])
        self.chapters = [dict(c) for c in chapters]
        return None

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def cloud(monkeypatch) -> StubCloud:
    stub = StubCloud([
        {"id": "a", "title": "One", "file": "f-a", "seconds": 60.0, "transcoding": False},
        {"id": "b", "title": "Two", "file": "f-b", "seconds": 70.0, "transcoding": False},
    ])
    monkeypatch.setattr(push, "client_from_settings", lambda: stub)
    return stub


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


URL = "/api/tonies/h1/t1/chapters"

# What the browser had on screen for the stub fixture above.
BASE = [{"id": "a", "title": "One"}, {"id": "b", "title": "Two"}]


def test_rename_returns_the_refreshed_tonie(client, cloud):
    resp = client.put(URL, json={
        "base": BASE,
        "chapters": [{"id": "a", "title": "Renamed"}, {"id": "b", "title": "Two"}],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert [c["title"] for c in body["chapters"]] == ["Renamed", "Two"]
    assert body["chapter_count"] == 2
    assert cloud.set_calls[0][0]["file"] == "f-a"


def test_reorder_sends_the_new_order(client, cloud):
    client.put(URL, json={
        "base": BASE,
        "chapters": [{"id": "b", "title": "Two"}, {"id": "a", "title": "One"}],
    })
    assert [c["id"] for c in cloud.set_calls[0]] == ["b", "a"]


def test_remove_sends_the_shortened_list(client, cloud):
    resp = client.put(URL, json={
        "base": BASE,
        "chapters": [{"id": "a", "title": "One"}],
    })
    assert resp.status_code == 200
    assert [c["id"] for c in cloud.set_calls[0]] == ["a"]


def test_clear_sends_an_empty_list(client, cloud):
    resp = client.put(URL, json={"base": BASE, "chapters": []})
    assert resp.status_code == 200
    assert cloud.set_calls[0] == []
    assert resp.json()["chapter_count"] == 0


def test_stale_base_returns_409_and_writes_nothing(client, cloud):
    """The assertion that matters: the guard blocks the write, it does not
    merely report on it afterwards."""
    resp = client.put(URL, json={
        "base": [{"id": "a", "title": "One"}],
        "chapters": [{"id": "a", "title": "One"}],
    })
    assert resp.status_code == 409
    assert cloud.set_calls == []
    assert len(cloud.chapters) == 2


def test_equal_length_base_with_different_ids_returns_409(client, cloud):
    """Same count, different ids: `d` instead of the `b` the Tonie holds. A
    length check would accept this and the PATCH would delete `b`."""
    resp = client.put(URL, json={
        "base": [{"id": "a", "title": "One"}, {"id": "d", "title": "Four"}],
        "chapters": [{"id": "a", "title": "One"}],
    })
    assert resp.status_code == 409
    assert cloud.set_calls == []
    assert len(cloud.chapters) == 2


def test_a_stale_title_in_base_returns_409_and_writes_nothing(client, cloud):
    """Another client renamed `a` while this tab sat open. The ids still match,
    so only a title-aware precondition catches it. Without the 409 the whole
    list write would send `a` back as "One" and revert the rename."""
    cloud.chapters[0]["title"] = "Alpha"
    resp = client.put(URL, json={
        "base": BASE,
        "chapters": [{"id": "a", "title": "One"}, {"id": "b", "title": "Beta"}],
    })
    assert resp.status_code == 409
    assert cloud.set_calls == []
    assert cloud.chapters[0]["title"] == "Alpha"


def test_unknown_requested_id_returns_400_and_writes_nothing(client, cloud):
    resp = client.put(URL, json={
        "base": BASE,
        "chapters": [{"id": "zzz", "title": "Ghost"}],
    })
    assert resp.status_code == 400
    assert cloud.set_calls == []


def test_the_client_is_always_closed(client, cloud):
    client.put(URL, json={"base": [{"id": "a", "title": "One"}], "chapters": []})
    assert cloud.closed is True


def test_response_carries_the_household_like_a_get_entry(client, cloud):
    """The spec requires the identical shape to a GET /api/tonies entry, and
    get_tonie does not carry the household. The server stamps it, not the
    browser, or every non-browser caller gets a broken response."""
    resp = client.put(URL, json={
        "base": BASE,
        "chapters": [{"id": "a", "title": "One"}, {"id": "b", "title": "Two"}],
    })
    body = resp.json()
    assert body["householdId"] == "h1"
    assert body["householdName"] == "Emily' household"


def test_the_put_response_has_the_same_keys_as_a_get_entry(client, cloud):
    """The spec's phrase is "the identical shape to one GET /api/tonies
    entry", so check the whole key set, not two spot-checked fields. Drop a
    key from either path and this fails.

    Key sets, not values: `lastUpdate` and the chapter titles legitimately
    differ between a read and a write of the same Tonie."""
    listed = client.get("/api/tonies")
    assert listed.status_code == 200
    entry = listed.json()[0]

    written = client.put(URL, json={
        "base": BASE,
        "chapters": [{"id": "a", "title": "One"}, {"id": "b", "title": "Two"}],
    })
    assert written.status_code == 200
    body = written.json()

    assert set(body) == set(entry)
    # And one level down, because a chapter is the part this endpoint rebuilds.
    assert set(body["chapters"][0]) == set(entry["chapters"][0])


def test_the_result_is_not_read_back_after_the_write(client, cloud):
    """One GET, for the precondition. A post-write read-back would add a case
    where the write landed and Toniefi reported failure."""
    client.put(URL, json={
        "base": BASE,
        "chapters": [{"id": "b", "title": "Two"}],
    })
    assert cloud.gets == 1


def test_seconds_present_is_recomputed_from_what_was_written(client, cloud):
    resp = client.put(URL, json={
        "base": BASE,
        "chapters": [{"id": "a", "title": "One"}],
    })
    assert resp.json()["seconds_present"] == 60.0


def test_a_refused_write_returns_400_and_says_why(client, cloud):
    """The likeliest real failure: the PATCH reaches the Tonie Cloud and the
    Tonie Cloud says no. The reason has to survive into the response, or the
    browser can only offer a bare 400 for something the user might fix."""
    cloud.set_error = "Tonie Cloud refused the chapter list (422)."
    resp = client.put(URL, json={
        "base": BASE,
        "chapters": [{"id": "a", "title": "One"}],
    })
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Tonie Cloud refused the chapter list (422)."
    assert cloud.closed is True
