# Development

## Running from source

```bash
brew install ffmpeg
./run-local.sh
```

`run-local.sh` is idempotent. It creates `.venv/`, installs or upgrades the dependencies, creates
`library/`, `data/` and `work/`, and starts uvicorn on <http://127.0.0.1:8080>.

`yt-dlp` is deliberately unpinned and refreshed on every start, because a stale copy is the most
common cause of "that YouTube link does not work".

Run only one TonieFi process against a SQLite database at a time.

## The test suite

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
for file in app/static/*.js; do node --check "$file"; done
node --test tests/static/*.test.mjs
```

Three commands, because the browser modules are plain ES modules with no build step: `pytest`
covers the Python service, `node --check` catches a syntax error in a module no test imports, and
`node --test` runs the browser behaviour tests against a small DOM stub in
`tests/static/mini-dom.mjs`.

The suite covers staged publication recovery, exact Forge retry output, upload retention, writer
leases, atomic confirmed sends, retryability, collection editing, title-aware Creative Tonie
concurrency, atomic credential pairs, route cancellation, and browser workflow behaviour.

`.github/workflows/publish.yml` runs both halves on a pull request. Adding a browser test without
the workflow step is how 164 tests once gated nothing.

Tests use local fixtures and stubbed cloud clients. **No test contacts a real myTonies account.**
Keep it that way: Remove and Clear have no undo in the Tonie Cloud, so those paths are covered by
stubs precisely so nobody has to destroy real audio to prove they work.

## Retired vocabulary

`tests/test_retired_vocabulary.py` scans the product docs, `app/`, `docs/` and `tests/` for a
workflow word this build retired. The screen it named was deleted outright, with no route, no
alias and no redirect, so it may not survive as a word either. Read that file for the exact
phrasing it refuses; it spells the list out on purpose and is the only file exempt from its own
scan.

Two things it deliberately lets through: "preview", which is the chapter player's own word and the
name of the `/api/playlist/preview` endpoint, and a short list of pinned exact lines that exist to
assert the retired step is gone.

A failure here means something reintroduced the deleted workflow. Fix the prose. Do not widen the
pattern until the scan goes quiet.

## Project layout

```text
app/
  main.py       FastAPI routes and application shell
  version.py    Semantic application release and normalized build label
  prepare.py    Extract-to-Forge preparation orchestration
  ingest.py     URL, LibriVox, and staged upload extraction
  forge.py      Trim, loudness, title cleanup, and splitting
  audio.py      ffmpeg and ffprobe wrappers plus capacity packing
  library.py    On-disk manifests, writer leases, atomic staged publication
  archive.py    Streamed zip download of one collection
  tonies.py     Private Tonie Cloud client
  push.py       Confirmed sends and canonical chapter-list writes
  jobs.py       SQLite-backed background worker and retry rules
  db.py         Job state and settings storage
  config.py     Environment-backed runtime configuration
  static/       Browser modules, no build step
tests/          Python API and service tests plus Node browser behaviour tests
docs/           This documentation
```

## Publishing the image

`.github/workflows/publish.yml` builds and pushes `ghcr.io/loubylabs/toniefi`. Four things in it
look simplifiable and are not:

- **`latest` moves in a separate guarded step, not in the build.** Queued runs are not guaranteed
  to start in push order and the emulated arm64 leg runs for minutes, so an older build can finish
  last. The build publishes only immutable per-commit tags; a later step re-fetches the default
  branch, checks this commit is still the tip, and only then retags `latest` from the build digest.
- **Published builds have no release-tag path.** Git tag pushes do not trigger the workflow. A
  manual dispatch targeting a tag still publishes only the immutable `sha-` image tag and never
  semantic tags. `latest` comes only from the guarded retag after a default branch build.
- **Concurrency is per-ref, not per-commit.** A per-commit group lets two `main` builds run at
  once, so `latest` can move backwards. The accepted cost is that three quick pushes can leave the
  middle one without a `sha-` tag.
- **`YTDLP_REFRESH` is keyed on the run, not on the commit.** `requirements.txt` leaves `yt-dlp`
  unpinned on purpose, but the pip layer keys on that file, which never changes, so the build cache
  would silently reintroduce the pin.

The semantic application version has one authority: `app/version.py`. The publish workflow passes
the full Git commit to Docker as `TONIEFI_BUILD_COMMIT`. Docker makes it available to the
application, which reports the first seven characters as its build label. A source checkout with
no commit injection reports `development`.

One step cannot be automated: a GHCR package is created private even from a public repository, and
no workflow can change that. It is set once in the package settings.
