"""Build identity: the Git commit the running image was built from."""
from __future__ import annotations

import os

REPOSITORY_URL = "https://github.com/loubylabs/Toniefi"


def build_label(commit: str) -> str:
    normalized = commit.strip()
    return normalized[:7] if normalized else "development"


def build_url(commit: str) -> str | None:
    normalized = commit.strip()
    return f"{REPOSITORY_URL}/commit/{normalized}" if normalized else None


_COMMIT = os.getenv("TONIEFI_BUILD_COMMIT", "")
BUILD = build_label(_COMMIT)
BUILD_URL = build_url(_COMMIT)
