# TonieFi

TonieFi is a self-hosted audiobook preparation workspace for Creative Tonies. It accepts several sources at once, prepares each collection independently, and stops at review before any Creative Tonie changes. The finished library stays as ordinary folders and MP3 files on your own disk.

```text
Batch intake -> Automatic extraction and Forge -> Review Shelf -> Confirmed send
```

## The Command Desk workflow

The application has six destinations:

- **Desk** accepts up to 50 HTTP or HTTPS source URLs at once. LibriVox search and multi-file upload are available as secondary intake modes.
- **Review Shelf** holds every collection that completed Forge and is ready for a deliberate Creative Tonie choice.
- **Library** shows every local collection, including extracted work that is not ready to send. Search, rescan, Finish preparation, open, and local deletion live here.
- **Creative Tonies** reads current remote contents before writes. It supports chapter rename, pointer and keyboard reorder, remove, and clear.
- **Activity** keeps the 40 most recent jobs with progress, timestamps, errors, result links, and eligible retries.
- **Settings** manages the myTonies credential source, connection tests, local credential removal, capacity, paths, and tool status.

### Batch preparation

Paste one source URL per line on Desk. TonieFi trims whitespace, rejects duplicates and unsupported schemes, preserves source order, and creates one independent job per source. A failure in one source does not block the rest of the batch.

Every accepted source runs extraction and the default Forge sequence automatically:

- Preserve source chapter markers.
- Normalize perceived loudness to −16 LUFS with a −1.5 dBTP ceiling.
- Clean common source noise from titles.
- Split tracks that exceed usable Creative Tonie capacity.
- Apply no automatic head or tail trim.

Each successful preparation stops on Review Shelf. TonieFi never assigns a Creative Tonie automatically.

### Review Shelf and confirmed sends

Open a prepared collection to inspect its cover, source, chapter titles, order, playback, duration, Forge result, and sequential capacity plan. Chapter edits change the local collection. Pointer drag has visible Move up and Move down alternatives for keyboard and touch use.

Choose one Creative Tonie for each capacity group, then choose whether the group replaces current remote chapters or appends after them. TonieFi refreshes remote state before presenting targets and asks for one final confirmation before queueing a send.

`POST /api/push/batch` validates the complete confirmed assignment against the reviewed local manifest and current remote chapter state. Job creation for the batch is atomic. Either every assignment is queued or none is. An operation key makes an uncertain response safe to retry without creating a second send batch.

If a send fails, return to Review and confirm the assignment again. Activity does not offer a generic Retry for push jobs because Creative Tonie writes have no undo and remote state may have changed.

### Recovery and job history

Background jobs persist in SQLite and continue when the browser closes. Desk shows active and recent preparation work. Activity retains failed attempts after an eligible retry, so ordinary recovery never erases the original error. A failed historical Forge attempt whose collection is already forged resolves to the completed collection instead.

Eligible preparation retries create a new job. A forged collection is terminal, so an older Forge worker or cloned job returns its existing output without transforming audio again. URL, LibriVox, and upload inputs keep a hidden deterministic collection stage until Forge and final publication succeed. Final slugs are reserved before extraction across visible and hidden collections, so same-title jobs keep distinct retry-stable folders. A durable extraction-complete checkpoint prevents Forge from consuming partial staged files. Retry resumes safe staged work or restarts from immutable input without exposing a partial collection. A visible publication receipt consumes its hidden source during recovery and sweep even while a failed job still references that source. Failed upload staging remains available for retry for 24 hours.

Legacy manual Forge stores one operation identity in its SQLite job and writes the same identity as a completion receipt before publication. Database initialization gives every older persisted Forge job a stable missing identity before workers can claim it. Once a collection manifest reaches `forged`, that stage is the canonical terminal receipt regardless of which historical Forge operation completed it. A restart, direct worker invocation, cloned job, or retry returns the existing collection without transforming its audio again. Repeated History retry for an extracted collection reuses its active Forge job. Retry for an already forged collection resolves the failed row to that completed collection.

The interface refreshes jobs, history, collections, and status through one application coordinator. Each resource publishes independently, so job progress stays current while a collection lease delays the collection index. The coordinator polls faster while work is active, slows when idle, and stops while the page is hidden.

### Creative Tonie chapter management

Creative Tonie edits always begin from a fresh remote list. The save precondition includes chapter titles as well as IDs, so a rename made elsewhere cannot be overwritten silently. While a save is running, every competing edit is disabled.

Remove and Clear require an explicit irreversible-action confirmation. These operations affect only the Tonie Cloud. Your local library is never changed from the Creative Tonies screen.

If the myTonies app, another tab, or a background send changes the same Tonie first, TonieFi refuses the stale write and reloads remote truth. A failed response also triggers a remote reload before controls become available again.

Renaming trims surrounding spaces and caps a changed title at 128 characters. An empty changed title keeps the existing title. Untouched chapter fields survive reorder and rename writes.

## Quick start

Both run modes keep the library in `library/` beside the repository by default.

### Docker

Docker Desktop is sufficient on macOS and Windows. Linux needs Docker Engine.

```bash
git clone https://github.com/loubylabs/Toniefi.git
cd Toniefi
docker compose pull
docker compose up -d
```

Open <http://127.0.0.1:8080>. Follow logs with `docker compose logs -f`. Stop with `docker compose down`.

On Linux, set the host user and group IDs first so the container does not create root-owned library files:

```bash
printf 'TONIEFI_UID=%s\nTONIEFI_GID=%s\n' "$(id -u)" "$(id -g)" >> .env
docker compose pull
docker compose up -d
```

Docker Desktop maps ownership back to the host user on macOS and Windows.

### Local development run

Install `ffmpeg` and Python 3.10 or newer, then start the local server:

```bash
brew install ffmpeg
./run-local.sh
```

Open <http://127.0.0.1:8080>. `Ctrl-C` stops it. Override the port with `PORT=9000 ./run-local.sh`.

`run-local.sh` creates `.venv/`, installs or upgrades dependencies, creates the local directories, and starts the server. Run only one TonieFi process against a SQLite database at a time.

You can prepare and review collections without a myTonies account. Creative Tonie reads and confirmed sends require one.

## API examples

### Prepare several source URLs

`POST /api/prepare` creates one independent preparation job per source and returns every job ID in source order.

```bash
curl -s -X POST http://127.0.0.1:8080/api/prepare \
  -H 'content-type: application/json' \
  -d '{
    "sources": [
      {"url": "https://www.youtube.com/watch?v=FIRST"},
      {"url": "https://www.youtube.com/watch?v=SECOND"}
    ],
    "options": {
      "use_chapters": true,
      "normalize": true,
      "clean_titles": true,
      "trim_head": 0,
      "trim_tail": 0,
      "split_oversized": true
    }
  }' | python3 -m json.tool
```

The response has this shape:

```json
{
  "jobs": [
    {"id": 42, "url": "https://www.youtube.com/watch?v=FIRST"},
    {"id": 43, "url": "https://www.youtube.com/watch?v=SECOND"}
  ]
}
```

Read current and historical work with `GET /api/jobs`. Retry one eligible failed job with `POST /api/jobs/{job_id}/retry`.

### Upload one collection from several files

Send every selected file under the repeated `files` field. The server stages the whole collection and creates one persisted preparation job.

```bash
curl -s -X POST http://127.0.0.1:8080/api/uploads/prepare \
  -F 'files=@chapter-01.mp3' \
  -F 'files=@chapter-02.mp3' \
  -F 'title=Peter Pan' \
  -F 'options={"use_chapters":true,"normalize":true,"clean_titles":true,"trim_head":0,"trim_tail":0,"split_oversized":true}'
```

One upload collection can contain up to 500 files and 20 GiB of staged audio. TonieFi streams incoming files to `UPLOAD_STAGE_DIR`, which defaults to persistent storage under `DATA_DIR`. Expired owned upload stages are cleaned after 24 hours. The default 2 GiB `/work` tmpfs is reserved for disposable downloads and transcodes.

### Finish a legacy extracted collection

Library and focused Review show **Finish preparation** for an older collection whose manifest stage is `extracted`. The action enqueues the supported persisted Forge job exactly once. The same migration route is available directly:

```bash
curl -s -X POST http://127.0.0.1:8080/api/forge \
  -H 'content-type: application/json' \
  -d '{"slug":"legacy-collection"}'
```

Assignment remains unavailable until the collection reaches manifest stage `forged` and appears on Review Shelf.

## Account management

Settings labels a complete environment or saved credential pair **Configured**. It becomes **Connected** only after a successful connection test in the current browser session. The tested timestamp is retained for that session, and a failed test shows an explicit connection failure. Settings also shows whether active credentials come from environment variables, local SQLite, or nowhere.

Environment credentials have precedence. When both `TONIES_USERNAME` and `TONIES_PASSWORD` are set, local form fields are disabled because saved values cannot override them. Removing saved credentials is idempotent and does not remove environment variables.

Credentials saved through Settings are stored as plain text in the local SQLite database. Protect the TonieFi data directory. The current private password-grant login does not support myTonies accounts with two-factor authentication. Passwords are never returned by TonieFi APIs or placed back into browser fields.

## Library layout and ownership

The library is deliberately plain:

```text
/library/peter-pan/collection.json
/library/peter-pan/cover.jpg
/library/peter-pan/001-chapter-i.mp3
/library/peter-pan/002-chapter-ii.mp3
```

`collection.json` owns track order, titles, metadata, and cached durations. Files added by hand appear after a Library rescan. Deleting TonieFi leaves the folders and MP3 files usable by other software.

Library deletion is intentionally destructive. Its confirmation names the local collection folder and audio files that will be removed.

## Forge and packing

| Pass | Behavior |
|---|---|
| Trim | Cuts the configured number of seconds from the front or back of every track. The batch default is zero. |
| Level | Normalizes to −16 LUFS under EBU R128 with a −1.5 dBTP ceiling. |
| Titles | Removes common source labels such as `FULL AUDIOBOOK`, `[HD]`, trailing `- YouTube`, and channel-name prefixes. |
| Split | Divides an oversized track into even re-encoded parts that fit usable capacity. |

A Creative Tonie holds 90 minutes. TonieFi subtracts configurable safety headroom before planning. It packs chapters sequentially and preserves source order. A later chapter never moves ahead of an earlier chapter to fill a gap.

## Source troubleshooting

Sites change their players frequently. A stale `yt-dlp` can report that a playable video is unavailable. Published images resolve `yt-dlp` during build, and `run-local.sh` upgrades dependencies on start.

The YouTube client list is configurable:

```bash
YTDLP_PLAYER_CLIENTS=default,android,web_safari ./run-local.sh
```

Test a candidate client with:

```bash
yt-dlp --extractor-args "youtube:player_client=CLIENT" --simulate URL
```

TonieFi cannot load a private, deleted, region-blocked, or DRM-protected source.

## Running on Unraid

Use the same compose file with array-backed paths in `.env`:

```text
TONIEFI_LIBRARY=/mnt/user/media/toniefi
TONIEFI_DATA=/mnt/user/appdata/toniefi
TONIEFI_UID=99
TONIEFI_GID=100
```

Start with `docker compose pull && docker compose up -d`. Open `http://<tower>:8080`. Use Tailscale or another private network for remote access. TonieFi binds plain HTTP and has no application-level access control, so do not port-forward it directly.

## Configuration

### Compose settings

| Variable | Default | Purpose |
|---|---|---|
| `TONIEFI_LIBRARY` | `./library` | Host path for audiobook collections |
| `TONIEFI_DATA` | `./data` | Host path for SQLite history and settings |
| `TONIEFI_PORT` | `8080` | Published host port |
| `TONIEFI_UID` / `TONIEFI_GID` | `0` | Container user and group on Linux |
| `TONIEFI_WORK_SIZE` | `2g` | RAM-backed download and transcode scratch space |

### Application settings

| Variable | Default | Purpose |
|---|---|---|
| `LIBRARY_DIR` | `/library` | Finished and in-progress local collections |
| `DATA_DIR` | `/data` | SQLite job history and locally saved settings |
| `WORK_DIR` | `/work` | Disposable downloads and transcodes |
| `UPLOAD_STAGE_DIR` | `DATA_DIR/upload-staging` | Restart-safe retained upload inputs |
| `TONIE_LIMIT_SECONDS` | `5400` | Capacity of one Creative Tonie |
| `TONIE_HEADROOM_SECONDS` | `30` | Safety margin used by planning |
| `TONIES_USERNAME` / `TONIES_PASSWORD` | unset | Environment myTonies credentials |
| `AUDIO_BITRATE` | `128k` | Transcode target |
| `WORKER_THREADS` | `2` | Concurrent background jobs |
| `YTDLP_PLAYER_CLIENTS` | `default,android` | YouTube clients that `yt-dlp` may use |

## Architecture

```text
app/
  main.py       FastAPI routes and application shell
  prepare.py    Extract-to-Forge preparation orchestration
  ingest.py     URL, LibriVox, and staged upload extraction
  forge.py      Trim, loudness, title cleanup, and splitting
  audio.py      ffmpeg and ffprobe wrappers plus capacity packing
  library.py    On-disk manifests, writer leases, and atomic staged publication
  tonies.py     Private Tonie Cloud client
  push.py       Confirmed sends and canonical chapter-list writes
  jobs.py       SQLite-backed background worker and retry rules
  db.py         Job state and settings storage
  static/       Command Desk browser modules with no build step
tests/          Python API and service tests plus Node browser behavior tests
```

The former manual probe, single-URL import route, five-step wizard, and one-job browser watcher have been retired. New preparation enters through `/api/prepare`, `/api/librivox/import`, or `/api/uploads/prepare` and advances through automatic Forge before review. The persisted `/api/forge` route remains the single migration path for legacy extracted collections.

## Development

Install development dependencies and run the full suite:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
for file in app/static/*.js; do node --check "$file"; done
node --test tests/static/*.test.mjs
```

The suite covers staged publication recovery, exact Forge retry output, upload retention, writer leases, atomic confirmed sends, retryability, collection editing, title-aware Creative Tonie concurrency, atomic credential pairs, route cancellation, and browser workflow behavior. Tests use local fixtures and stub cloud clients. They do not contact a real myTonies account.

## Tonie Cloud disclosure

TonieFi uses a private, unsupported REST API used by the myTonies web application. TonieFi is not affiliated with, endorsed by, or supported by tonies or Boxine. Endpoints can change without notice.

If the private API changes, the local library remains intact and the official myTonies web application remains available. Creative Tonie cloud writes have no undo.

## Content responsibility

LibriVox is included because its recordings are public domain. Use URL intake for material you own, public-domain material, or audio you are licensed to copy. TonieFi does not bypass DRM.

## License

MIT
