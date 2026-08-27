"""The route, against a stub Tonie Cloud. No network, no real account.

The Tonie Cloud has no sandbox and no undo, so the destructive paths are
proved here and never against the owner's own Creative Tonie.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import audio, main, push, tonies


class ResponseBuildFailed(Exception):
    """Raised from a monkeypatched collaborator to fail the response build.

    Deliberately not a type the route handles, so the test below cannot pass
    by accident through a handler that turns it into a status code.
    """


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
        self.calls: list[str] = []
        self.seconds_present_override: float | None = None

    def _payload(self, tonie_id: str = "t1") -> dict:
        """One Tonie as the Tonie Cloud reports it.

        `lastUpdate` changes on every read, because the real Tonie Cloud stamps
        it. Anything comparing two reads has to compare keys, not values.
        """
        self.reads += 1
        seconds_present = (
            self.seconds_present_override
            if self.seconds_present_override is not None
            else sum(c["seconds"] for c in self.chapters)
        )
        return {"id": tonie_id, "name": "Creative Tonie",
                "lastUpdate": f"2026-08-26T12:00:0{self.reads}Z",
                "secondsPresent": seconds_present,
                "chapters": [dict(c) for c in self.chapters]}

    def households(self) -> list[dict]:
        self.calls.append("households")
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
        self.calls.append("get_tonie")
        return self._payload(tonie_id)

    def set_chapters(self, household_id: str, tonie_id: str, chapters: list[dict]):
        self.calls.append("set_chapters")
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


def test_seconds_present_comes_from_the_cloud_not_a_naive_sum(client, cloud):
    """A chapter mid-transcode reports no length of its own, so summing the
    remaining chapters throws away the Cloud's own accounting. A rename
    changes no durations, so the figure must not move at all."""
    cloud.chapters[1]["seconds"] = 0.0        # as if still transcoding
    cloud.seconds_present_override = 200.0     # what the Cloud actually reports
    resp = client.put(URL, json={
        "base": [{"id": "a", "title": "One"}, {"id": "b", "title": "Two"}],
        "chapters": [{"id": "a", "title": "Renamed"}, {"id": "b", "title": "Two"}],
    })
    assert resp.json()["seconds_present"] == 200.0


def test_removing_a_chapter_subtracts_only_that_chapter(client, cloud):
    cloud.seconds_present_override = 200.0
    resp = client.put(URL, json={
        "base": [{"id": "a", "title": "One"}, {"id": "b", "title": "Two"}],
        "chapters": [{"id": "a", "title": "One"}],
    })
    assert resp.json()["seconds_present"] == 200.0 - 70.0


def test_clearing_sets_seconds_present_to_zero(client, cloud):
    """The Cloud reports 200 while the chapters only account for 130, so the
    general rule would hand the 70 remainder to somebody. Nothing survives a
    clear, so the answer is exactly 0 and not the remainder."""
    cloud.seconds_present_override = 200.0
    resp = client.put(URL, json={
        "base": [{"id": "a", "title": "One"}, {"id": "b", "title": "Two"}],
        "chapters": [],
    })
    assert resp.json()["seconds_present"] == 0


@pytest.mark.parametrize(
    "b_seconds, requested, expected",
    [
        # Nothing is dropped, so every second the Cloud counts still belongs
        # to a chapter that is staying. The figure must not move.
        (0.0, [{"id": "a", "title": "Renamed"}, {"id": "b", "title": "Two"}], 130.0),
        # `b` reported its full 70s, so the Cloud's total is fully accounted
        # for and removing it leaves exactly `a`.
        (70.0, [{"id": "a", "title": "One"}], 60.0),
        # `b` is mid-transcode and reported nothing, yet the Cloud already
        # counts 70s nobody claims. That remainder is probably `b`'s, and it
        # cannot be split, so it leaves with `b` rather than inflating `a`.
        (0.0, [{"id": "a", "title": "One"}], 60.0),
        # Nothing survives, so there is nothing to attribute anything to.
        (0.0, [], 0.0),
    ],
    ids=["rename-moves-nothing", "drop-a-finished-chapter",
         "drop-a-transcoding-chapter", "clear-everything"],
)
def test_seconds_present_attributes_the_clouds_remainder(
    client, cloud, b_seconds, requested, expected
):
    """The Cloud's secondsPresent and the chapters' own reported seconds
    disagree while anything is still transcoding, and the difference belongs
    to the chapters that have not finished. Removing a transcoding chapter
    used to subtract its reported 0 from the Cloud's total and hand back a
    figure counting audio that had just left the Tonie.
    """
    cloud.chapters[1]["seconds"] = b_seconds
    cloud.chapters[1]["transcoding"] = b_seconds == 0.0
    cloud.seconds_present_override = 130.0

    resp = client.put(URL, json={"base": BASE, "chapters": requested})

    assert resp.status_code == 200
    assert resp.json()["seconds_present"] == expected


def test_a_failed_response_build_writes_nothing(client, cloud, monkeypatch):
    """If the answer cannot be built, the Tonie is left alone.

    The contract, not the call order: everything that can raise has to run
    before set_chapters. A PATCH that landed and was then reported as a
    failure sends the user back to retry into a 409, on a Tonie the Tonie
    Cloud has already changed and cannot undo.
    """
    def boom(_seconds: float) -> str:
        raise ResponseBuildFailed("no duration for you")

    # human_duration is a collaborator of describe_tonie, not the thing whose
    # position is being pinned, so this fails the response build without
    # stubbing out the response build itself.
    monkeypatch.setattr(audio, "human_duration", boom)

    with pytest.raises(ResponseBuildFailed):
        client.put(URL, json={
            "base": BASE,
            "chapters": [{"id": "a", "title": "Renamed"}, {"id": "b", "title": "Two"}],
        })

    assert cloud.set_calls == []
    assert cloud.chapters[0]["title"] == "One"
    # The finally has to hold even on a path nothing handles.
    assert cloud.closed is True


def test_households_is_resolved_before_the_write(client, cloud):
    """Resolving a cosmetic display name must not be able to fail after the
    destructive write has landed."""
    client.put(URL, json={
        "base": [{"id": "a", "title": "One"}, {"id": "b", "title": "Two"}],
        "chapters": [{"id": "a", "title": "One"}],
    })
    assert cloud.calls.index("households") < cloud.calls.index("set_chapters")


def test_the_client_is_closed_on_the_success_path(client, cloud):
    client.put(URL, json={
        "base": [{"id": "a", "title": "One"}, {"id": "b", "title": "Two"}],
        "chapters": [{"id": "a", "title": "One"}],
    })
    assert cloud.closed is True


def test_an_empty_cloud_response_is_a_400_and_writes_nothing(client, monkeypatch):
    """The guard and the route have to compose, so neither half is stubbed.

    A real TonieCloud is installed with only its _request monkeypatched, so
    the empty body raises the real TonieCloudError from the real get_tonie
    and the route maps the real exception type. Proving the guard against the
    client alone would still pass if a refactor lifted get_tonie out of the
    route's TonieCloudError handler, and an empty Cloud response would then
    be a 500 rather than a clean 400.
    """
    calls: list[tuple[str, str]] = []

    def record(method: str, path: str, **kwargs: object) -> None:
        calls.append((method, path))
        return None  # what _request returns for a 2xx with no body

    cloud = tonies.TonieCloud("user", "pass")
    monkeypatch.setattr(cloud, "_request", record)
    monkeypatch.setattr(push, "client_from_settings", lambda: cloud)

    resp = client.put(URL, json={
        "base": BASE,
        "chapters": [{"id": "a", "title": "One"}],
    })

    assert resp.status_code == 400
    # The whole call log, not just the absence of a PATCH: the read happened,
    # it failed the guard, and nothing was attempted afterwards.
    assert calls == [("GET", "/households/h1/creativetonies/t1")]
    assert not any(method == "PATCH" for method, _ in calls)
