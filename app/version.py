"""Application release and build identity."""
from __future__ import annotations

import os

APP_VERSION = "1.0.0"


def build_label(commit: str) -> str:
    normalized = commit.strip()
    return normalized[:7] if normalized else "development"


BUILD = build_label(os.getenv("TONIEFI_BUILD_COMMIT", ""))
