# Toniefi

Self-hosted audiobook pipeline for Creative Tonies. Paste a link, get chapters
on a Tonie, with the library staying as plain folders of MP3s on your own disk.

```
Paste  ->  Extract  ->  Forge  ->  Review  ->  Send
```

1. **Paste**: drop in a URL or playlist. Toniefi reads it *without downloading*
   and tells you the runtime, whether it has chapter markers, and how many
   Tonies it will take. Or search LibriVox, or upload files you already have.
2. **Extract**: pulls the audio, cover art and metadata. When the source has
   chapter markers, each becomes its own track instead of one long blob.
3. **Forge**: the clean-up passes, each optional. Trim a fixed intro/outro,
   level the loudness, tidy the titles, split anything too long for one Tonie.
4. **Review**: cover, chapter list, durations, total runtime, and how it packs
   onto Tonies. Drag to reorder, click to retitle, play to check.
5. **Send**: pick a Creative Tonie and upload, replacing or appending.

On the **My Tonies** tab, opening a Creative Tonie shows the chapters already on
it. You can rename a chapter in place, drag one to reorder, remove a single
chapter, or clear the whole Tonie. Every change is written straight to the Tonie
Cloud, which has no undo, so Remove and Clear both ask first. Your library on
disk is never touched by anything on this tab.

If someone else changes the same Tonie while you have it open (the myTonies app,
or a Toniefi upload running in another tab), the save is refused and the list
reloads rather than overwriting their change.

Renaming trims surrounding spaces and caps a title at 128 characters, which is
what the Tonie Cloud accepts. That only ever applies to a title you actually
changed. Chapters you did not touch are written back exactly as they were, so
reordering one chapter never rewrites the names of the others. Emptying a name
does nothing: the chapter keeps the title it already has, so a slipped
keystroke cannot leave a chapter unnamed.

## Quick start

Two ways to run it on your own machine. Both put the library in `library/`
beside the repo, so you can switch between them and see the same collections.

### With Docker

Nothing to install but Docker itself. Docker Desktop is enough on macOS and
Windows; on Linux, Docker Engine.

```bash
git clone https://github.com/loubylabs/Toniefi.git
cd Toniefi
docker compose pull
docker compose up -d
```

Open <http://127.0.0.1:8080>. `docker compose logs -f` follows it, and
`docker compose down` stops it.

Nothing needs configuring first. `docker-compose.yml` defaults every path and
port, and creates `library/` and `data/` for you on first run. Copy
`.env.example` to `.env` only when you want to change something.

**On Linux, set your user id first**, or the container writes root-owned files
into your library and you need `sudo` to delete them:

```bash
printf 'TONIEFI_UID=%s\nTONIEFI_GID=%s\n' "$(id -u)" "$(id -g)" >> .env
docker compose pull
docker compose up -d
```

Docker Desktop maps ownership back to you already, so macOS and Windows can
skip that.

### Without Docker

Faster to restart while you are changing code. You need `ffmpeg` and Python
3.10 or newer; everything else installs itself.

```bash
brew install ffmpeg                 # macOS. Debian: sudo apt install ffmpeg
./run-local.sh
```

Open <http://127.0.0.1:8080>. `Ctrl-C` stops it.

`run-local.sh` is idempotent, so re-running it is the normal way to start the
app. On each run it creates `.venv/` if missing, installs and **upgrades** the
dependencies, creates `library/`, `data/` and `work/`, and starts the server.
Override the port with `PORT=9000 ./run-local.sh`.

### Either way

You do not need a myTonies account to try it. Without one, steps 1 to 4 still
work and you end up with tidy MP3s in `library/`; only **Send** needs to log in.

Run only one of the two at a time. They both want port 8080, and two writers
on one SQLite job database is asking for trouble.

## Feeding it a YouTube link

In the browser, under **Paste a link**:

1. Paste the video or playlist URL and press **Look it up**. Nothing downloads
   yet. You get the runtime, the chapter count, and how many Tonies it needs.
2. Press **Pull the audio**. Every chapter marker becomes its own MP3 track. A
   video with no markers arrives as one track, which the **Split** pass in
   Forge can cut down later.
3. Press **Forge it**, fix anything in Review, then **Send to a Tonie**.

Long videos run as background jobs, so the page stays responsive and you can
close the tab.

Same thing from a terminal, if you would rather not use the browser:

```bash
# 1. Look before you leap: runtime, chapters, Tonies needed.
curl -s -X POST http://127.0.0.1:8080/api/probe \
  -H 'content-type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID"}' | python3 -m json.tool

# 2. Extract it. Returns a job id.
curl -s -X POST http://127.0.0.1:8080/api/ingest/url \
  -H 'content-type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID","use_chapters":true}'

# 3. Watch it. Poll until "status":"done".
curl -s http://127.0.0.1:8080/api/jobs/1 | python3 -m json.tool
```

Already have the files on disk? Submit the whole collection as one persisted preparation job:

```bash
curl -s -X POST http://127.0.0.1:8080/api/uploads/prepare \
  -F 'files=@chapter-01.mp3' \
  -F 'files=@chapter-02.mp3' \
  -F 'title=Peter Pan' \
  -F 'options={"use_chapters":true,"normalize":true,"clean_titles":true,"trim_head":0,"trim_tail":0,"split_oversized":true}'
```

### When a link will not load

**`This video is not available`, but the browser plays it fine.** YouTube is
refusing the client `yt-dlp` asked as. Its default set gets turned away often:
the `tv` client asks to reload the page, and `web` and `ios` hand back the
metadata with no downloadable formats, which yt-dlp reports as the video being
unavailable. Misleading, but not your video's fault.

Toniefi already names `android` as a fallback, which currently works where the
default set does not. Which clients YouTube accepts drifts, so that list is an
environment variable rather than a constant:

```bash
YTDLP_PLAYER_CLIENTS=default,android,web_safari ./run-local.sh
```

`yt-dlp --extractor-args "youtube:player_client=CLIENT" --simulate URL` is the
quick way to find one that answers before you change the setting.

**`The page needs to be reloaded`** on every URL is a stale `yt-dlp`. Sites
change their player every few weeks and yt-dlp ships a fix within days. Every
published image resolves `yt-dlp` fresh at build time, so pulling a newer
image does get a newer `yt-dlp`:

```bash
docker compose pull
docker compose up -d
```

If `main` has not moved since your last pull, `docker compose pull` reports
the image as already up to date and there is no newer `yt-dlp` to fetch. Run
the `publish` workflow by hand from the repository's Actions tab
(`workflow_dispatch`) to publish a fresh image on demand. `run-local.sh` is
the other route: it upgrades `yt-dlp` on every start.

**Nothing will fix these:** a genuinely private, deleted or region-blocked
video, and a DRM'd stream (Audible, Spotify, Apple Music).

## What each Forge pass does

| Pass | What it does |
|---|---|
| Trim | Cuts N seconds off the front and/or back of every track, for channel intros and outros. |
| Level | Loudness-matches to −16 LUFS (EBU R128, −1.5 dBTP ceiling) so chapter 3 isn't twice as loud as chapter 2. |
| Titles | Strips `FULL AUDIOBOOK`, `[HD]`, `1080p`, trailing `- YouTube`, and channel-name prefixes. Conservative by design: a bare quality token is only dropped when a separator sets it apart, so *The Hobbit in 4K* keeps its 4K. |
| Split | Breaks any track over the Tonie limit into **even** parts. A 2-hour file becomes 2×62 min, not 90 + 30. Re-encodes rather than stream-copying, because copying cuts on frame boundaries and drifts on VBR rips. |

## Library layout

Deliberately boring, so nothing is trapped in the app:

```
/library/peter-pan/collection.json     metadata, track order, cached durations
/library/peter-pan/cover.jpg
/library/peter-pan/001-chapter-i.mp3
/library/peter-pan/002-chapter-ii.mp3
```

Delete Toniefi tomorrow and you still have folders of MP3s. Track **order**
lives in `collection.json` rather than in the filenames. The Review step lets
you drag chapters around, and renaming files to match would churn the whole
folder on every move. Files you drop in by hand are picked up on the next
rescan and appended at the end.

## Packing

A Creative Tonie holds 90 minutes. Toniefi packs chapters **in order**, filling
each Tonie until the next chapter would overflow it. That is sequential
first-fit, not a bin-packing optimum: chapter 7 never lands before chapter 6,
which matters more for an audiobook than squeezing out the last three minutes.

## Where the image comes from

GitHub builds and publishes `ghcr.io/loubylabs/toniefi` on every push to
`main`, but only after the test suite passes, so nothing broken reaches the
registry. The package is meant to be public, so no `docker login` is needed
to pull it. If a pull is denied with `unauthorized` or `denied`, the
package's visibility needs setting to Public once, in its GHCR package
settings. `latest` follows `main`, `sha-<short>` pins one exact commit, and a `v*` tag
gets semver forms of its own. Taking an update is the same command as a
first run:

```bash
docker compose pull && docker compose up -d
```

## Running it on Unraid

Same compose file as everywhere else. Only the `.env` differs, because the
library belongs on the array rather than beside the repo.

1. Copy the repo to the server, e.g. `/mnt/user/appdata/toniefi/src`.
2. Write a `.env` next to `docker-compose.yml`:

   ```bash
   TONIEFI_LIBRARY=/mnt/user/media/toniefi
   TONIEFI_DATA=/mnt/user/appdata/toniefi
   TONIEFI_UID=99
   TONIEFI_GID=100
   ```

   The two ids are Unraid's `nobody:users`, which is what its shares expect.
3. `docker compose pull && docker compose up -d`
4. Open `http://<tower>:8080` and add your myTonies account on Settings, or
   put `TONIES_USERNAME` / `TONIES_PASSWORD` in that same `.env`, which keeps
   them out of the database.

Reaching it from off the network is a Tailscale job, not this app's. It binds
plain HTTP on 8080 with no authentication of its own. Do not port-forward it.

## Configuration

### Compose settings

These shape the container and only apply to the Docker path. Put them in a
`.env` beside `docker-compose.yml`; see `.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `TONIEFI_LIBRARY` | `./library` | Host path for the audiobooks |
| `TONIEFI_DATA` | `./data` | Host path for the job database and settings |
| `TONIEFI_PORT` | `8080` | Host port to publish on |
| `TONIEFI_UID` / `TONIEFI_GID` | `0` (root) | Who the container runs as. Set to your own ids on Linux |
| `TONIEFI_WORK_SIZE` | `2g` | Scratch space. It is RAM, so stay under what the Docker VM has |

### Application settings

These are read by the app itself, so they work on both paths. `run-local.sh`
fills in the three directories for you.

| Variable | Default | Purpose |
|---|---|---|
| `LIBRARY_DIR` | `/library` | Where collections live |
| `DATA_DIR` | `/data` | SQLite job history and saved settings |
| `WORK_DIR` | `/work` | Scratch space for downloads and transcodes |
| `TONIE_LIMIT_SECONDS` | `5400` | Capacity of one Creative Tonie |
| `TONIE_HEADROOM_SECONDS` | `30` | Safety margin against duration rounding |
| `TONIES_USERNAME` / `TONIES_PASSWORD` | unset | myTonies account |
| `AUDIO_BITRATE` | `128k` | Transcode target |
| `WORKER_THREADS` | `2` | Concurrent background jobs |
| `YTDLP_PLAYER_CLIENTS` | `default,android` | Which YouTube clients yt-dlp may ask as |

## Architecture

```
app/
  main.py      FastAPI routes; the five steps map to /api/probe, /api/ingest/*,
               /api/forge, /api/collections/*, /api/push
  ingest.py    Extract: yt-dlp, LibriVox, uploads
  forge.py     Forge: trim, loudness, title cleaning
  audio.py     ffmpeg/ffprobe wrappers, splitting, Tonie packing
  library.py   On-disk collections, ordering, manifests
  tonies.py    Tonie Cloud API client
  push.py      Send: resolve a group, upload, append chapters. Also the
               chapter rewrite behind My Tonies: rename, reorder, remove, clear
  jobs.py      Background worker (SQLite-backed queue)
  db.py        Job state and settings
  static/      Single-page front end, no build step
tests/         pytest suite: the chapter write path, against a stub Tonie Cloud
```

Long operations run as background jobs so an HTTP request is never left holding
the bag; the UI polls `/api/jobs/{id}` for progress.

## Development

`run-local.sh` creates `.venv/` on its first run. The tests need one extra
package on top of it:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q
```

The suite covers the chapter write path (`merge_chapters`, `describe_tonie`
and `PUT /api/tonies/{h}/{t}/chapters` against a stub Tonie Cloud), which is
the only code here that can change a Tonie irreversibly. It touches neither
the network nor a real account.

## How the Tonie Cloud integration works

The myTonies web app talks to a private REST API. Toniefi uses the same one:

1. **Auth**: OIDC password grant against
   `login.tonies.com/auth/realms/tonies/protocol/openid-connect/token`
   with `client_id=my-tonies`, returning a bearer token.
2. **Discovery**: `GET /v2/households`, then
   `GET /v2/households/{id}/creativetonies`.
3. **Upload**: `POST /v2/file` returns a presigned S3 POST; the audio goes
   straight to S3 and you get back a `fileId`.
4. **Chapters**: `POST /v2/households/{h}/creativetonies/{t}/chapters` with
   `{title, file}` appends; `PATCH` on the Tonie with a `chapters` array
   replaces the whole list, which is how renaming, reordering, removing and
   clearing are all done.

### Caveats

- **Not an official or supported API.** This project is not affiliated with
  tonies / Boxine. Endpoints can change without notice.
- The password grant stores real credentials and **does not work with
  two-factor accounts**.
- If pushes start failing, the API most likely moved. The library on disk is
  untouched and the myTonies web app remains a working fallback.
- DRM'd files (Audible, Spotify, Apple Music) are rejected by the Tonie Cloud
  itself. That is encryption, not policy, so there is no workaround.

## On content

LibriVox is built in because everything there is public domain: free to
download, free to put on a Tonie, no rights question at all. URL ingest is a
general tool. Point it at your own recordings, Creative Commons uploads, or
public domain material. Most "full audiobook" uploads on video sites are
unlicensed rips of commercial recordings.

## License

MIT
