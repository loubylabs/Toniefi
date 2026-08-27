"""Client for the Tonie Cloud REST API.

This is the same private API the myTonies web app uses. It is not an
officially published or supported interface, and this project is not
affiliated with Boxine GmbH / tonies. Endpoints can change without notice;
treat upload failures as "the API moved", not as data loss -- the library on
disk is always the source of truth.

Flow for putting audio on a Creative Tonie:
    1. POST /file                     -> {fileId, request:{url, fields}}
    2. POST <request.url> (S3)        -> multipart form with fields + file
    3. POST /households/{h}/creativetonies/{t}/chapters  {title, file:fileId}
    4. PATCH /households/{h}/creativetonies/{t}          {chapters:[...]}  (reorder/clear)
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import httpx

TOKEN_URL = "https://login.tonies.com/auth/realms/tonies/protocol/openid-connect/token"
API_BASE = "https://api.tonie.cloud/v2"
CLIENT_ID = "my-tonies"

# Tokens are short-lived; refresh a little before they actually expire.
TOKEN_SKEW_SECONDS = 60


class TonieCloudError(RuntimeError):
    """Any failure talking to the Tonie Cloud, with a human-readable message."""


class AuthError(TonieCloudError):
    pass


class TonieCloud:
    def __init__(self, username: str, password: str, timeout: float = 60.0) -> None:
        if not username or not password:
            raise AuthError("No myTonies username/password configured.")
        self._username = username
        self._password = password
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)

    # ------------------------------------------------------------- auth

    def _fetch_token(self) -> None:
        try:
            resp = self._client.post(
                TOKEN_URL,
                data={
                    "grant_type": "password",
                    "client_id": CLIENT_ID,
                    "scope": "openid",
                    "username": self._username,
                    "password": self._password,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.RequestError as exc:
            raise TonieCloudError(f"Could not reach the Tonies login service: {exc}") from exc

        if resp.status_code == 401:
            raise AuthError("myTonies rejected those credentials.")
        if resp.status_code >= 400:
            raise AuthError(
                f"Login failed ({resp.status_code}). If your account has two-factor "
                f"authentication enabled, this password grant will not work."
            )
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise AuthError("Login succeeded but no access token was returned.")
        self._token = token
        self._expires_at = time.time() + int(body.get("expires_in", 300))

    def _auth_header(self) -> dict[str, str]:
        with self._lock:
            if not self._token or time.time() >= self._expires_at - TOKEN_SKEW_SECONDS:
                self._fetch_token()
            return {"Authorization": f"Bearer {self._token}"}

    def check_login(self) -> dict[str, Any]:
        """Verify credentials and return the account profile."""
        return self._request("GET", "/me")

    # ---------------------------------------------------------- requests

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{API_BASE}{path}"
        headers = {**self._auth_header(), **kwargs.pop("headers", {})}
        try:
            resp = self._client.request(method, url, headers=headers, **kwargs)
        except httpx.RequestError as exc:
            raise TonieCloudError(f"Could not reach the Tonie Cloud: {exc}") from exc

        if resp.status_code == 401:
            # Token may have been invalidated early; retry once with a fresh one.
            with self._lock:
                self._token = None
            headers = {**self._auth_header(), **kwargs.pop("headers", {})}
            resp = self._client.request(method, url, headers=headers, **kwargs)

        if resp.status_code >= 400:
            raise TonieCloudError(
                f"{method} {path} failed ({resp.status_code}): {resp.text[:400]}"
            )
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # -------------------------------------------------------- resources

    def households(self) -> list[dict[str, Any]]:
        return self._request("GET", "/households") or []

    def creative_tonies(self, household_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/households/{household_id}/creativetonies") or []

    def all_creative_tonies(self) -> list[dict[str, Any]]:
        """Every Creative Tonie across every household, annotated with its household."""
        out: list[dict[str, Any]] = []
        for house in self.households():
            hid = house.get("id")
            for tonie in self.creative_tonies(hid):
                tonie["householdId"] = hid
                tonie["householdName"] = house.get("name", "")
                out.append(tonie)
        return out

    def get_tonie(self, household_id: str, tonie_id: str) -> dict[str, Any]:
        tonie = self._request("GET", f"/households/{household_id}/creativetonies/{tonie_id}")
        # A 2xx with an empty body is not a Tonie. Returning None here would
        # surface as an AttributeError: a 500 at the chapter-write caller, a
        # failed job with an ugly message at the send caller, which runs in
        # a worker that catches Exception (app/jobs.py) rather than an HTTP
        # request.
        if not isinstance(tonie, dict):
            raise TonieCloudError(f"Tonie Cloud returned no Tonie for {tonie_id}.")
        return tonie

    # ----------------------------------------------------------- upload

    def upload_file(self, path: Path) -> str:
        """Upload one audio file, returning the Tonie Cloud file id."""
        ticket = self._request("POST", "/file")
        if not ticket or "request" not in ticket:
            raise TonieCloudError("Tonie Cloud did not return an upload ticket.")

        file_id = ticket.get("fileId") or ticket["request"]["fields"].get("key")
        target = ticket["request"]["url"]
        fields = ticket["request"]["fields"]

        with path.open("rb") as fh:
            try:
                # Deliberately unauthenticated: this is a presigned S3 POST.
                resp = httpx.post(
                    target,
                    data=fields,
                    files={"file": (path.name, fh, "audio/mpeg")},
                    timeout=600.0,
                )
            except httpx.RequestError as exc:
                raise TonieCloudError(f"Upload to storage failed: {exc}") from exc

        if resp.status_code >= 400:
            raise TonieCloudError(
                f"Upload to storage rejected ({resp.status_code}): {resp.text[:300]}"
            )
        if not file_id:
            raise TonieCloudError("Upload succeeded but no file id came back.")
        return file_id

    # --------------------------------------------------------- chapters

    def add_chapter(self, household_id: str, tonie_id: str, title: str, file_id: str) -> Any:
        return self._request(
            "POST",
            f"/households/{household_id}/creativetonies/{tonie_id}/chapters",
            json={"title": title[:128], "file": file_id},
        )

    def set_chapters(self, household_id: str, tonie_id: str, chapters: list[dict]) -> Any:
        """Replace the whole chapter list -- also how you reorder or clear."""
        return self._request(
            "PATCH",
            f"/households/{household_id}/creativetonies/{tonie_id}",
            json={"chapters": chapters},
        )

    def clear_tonie(self, household_id: str, tonie_id: str) -> Any:
        return self.set_chapters(household_id, tonie_id, [])

    def close(self) -> None:
        self._client.close()
