#!/usr/bin/env bash
# Run Toniefi on this machine, no Docker. Idempotent: safe to re-run.
#
#   ./run-local.sh          -> http://127.0.0.1:8080
#   PORT=9000 ./run-local.sh
#
# The library, job database and scratch space land in ./library, ./data and
# ./work, all of which are gitignored.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8080}"
HOST="${HOST:-127.0.0.1}"

# ffmpeg does every probe, transcode and split. Nothing works without it.
for tool in ffmpeg ffprobe; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing $tool. Install it first:" >&2
    echo "  macOS:  brew install ffmpeg" >&2
    echo "  Debian: sudo apt install ffmpeg" >&2
    exit 1
  fi
done

# The app uses 3.10+ union syntax in its Pydantic models, and macOS still
# ships 3.9, so pick an interpreter rather than trusting `python3`.
if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.12 .venv
  else
    PY=""
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
      if command -v "$candidate" >/dev/null 2>&1 \
         && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
        PY="$candidate"
        break
      fi
    done
    [ -n "$PY" ] || { echo "Need Python 3.10 or newer. Try: brew install python@3.12" >&2; exit 1; }
    "$PY" -m venv .venv
  fi
fi

# yt-dlp is unpinned on purpose, so refresh it every start -- a stale copy is
# the single most common cause of "that YouTube link does not work".
if command -v uv >/dev/null 2>&1; then
  uv pip install --quiet --python .venv/bin/python --upgrade -r requirements.txt
else
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet --upgrade -r requirements.txt
fi

mkdir -p library data work data/upload-staging

# .venv/bin on PATH is what lets the app find yt-dlp, which it shells out to.
export PATH="$PWD/.venv/bin:$PATH"
export LIBRARY_DIR="${LIBRARY_DIR:-$PWD/library}"
export DATA_DIR="${DATA_DIR:-$PWD/data}"
export WORK_DIR="${WORK_DIR:-$PWD/work}"
export UPLOAD_STAGE_DIR="${UPLOAD_STAGE_DIR:-$DATA_DIR/upload-staging}"

echo "Toniefi on http://$HOST:$PORT   (library: $LIBRARY_DIR)"
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"
