"""Runtime configuration, all overridable by environment variable."""
from __future__ import annotations

import os
from pathlib import Path

# Where finished, Tonie-ready collections live. On Unraid, bind-mount your
# array share here (e.g. /mnt/user/media/tonies -> /library).
LIBRARY_DIR = Path(os.getenv("LIBRARY_DIR", "/library"))

# Scratch space for downloads and transcodes. Keep it on fast storage.
WORK_DIR = Path(os.getenv("WORK_DIR", "/work"))

# SQLite job/settings store.
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "portal.db"

# A Creative Tonie holds 90 minutes. Leave a little headroom so a rounding
# error in a source file's duration never gets a push rejected.
TONIE_LIMIT_SECONDS = int(os.getenv("TONIE_LIMIT_SECONDS", str(90 * 60)))
TONIE_HEADROOM_SECONDS = int(os.getenv("TONIE_HEADROOM_SECONDS", "30"))

# myTonies credentials. Prefer env vars; the UI can also store them in the DB.
TONIES_USERNAME = os.getenv("TONIES_USERNAME", "")
TONIES_PASSWORD = os.getenv("TONIES_PASSWORD", "")

# Transcode target. Creative Tonies accept a lot of formats, but normalizing
# to a consistent MP3 avoids surprises with odd containers from the web.
AUDIO_BITRATE = os.getenv("AUDIO_BITRATE", "128k")
AUDIO_SAMPLE_RATE = os.getenv("AUDIO_SAMPLE_RATE", "44100")

# Concurrency for the background worker.
WORKER_THREADS = int(os.getenv("WORKER_THREADS", "2"))


def usable_limit() -> int:
    return max(60, TONIE_LIMIT_SECONDS - TONIE_HEADROOM_SECONDS)


def ensure_dirs() -> None:
    for d in (LIBRARY_DIR, WORK_DIR, DATA_DIR):
        d.mkdir(parents=True, exist_ok=True)
