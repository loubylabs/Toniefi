# TonieFi

Self-hosted workspace for Creative Tonies. Paste a few links, let TonieFi clean the audio up,
then choose what goes on which Tonie. The finished library stays on your own disk as ordinary
folders and MP3 files.

```text
Paste sources  ->  automatic cleanup  ->  Library  ->  confirmed send
```

Nothing reaches a Creative Tonie until you pick it, name a target, and press Send.

![The TonieFi Desk, with source links pasted and prepared stories waiting in the work cart](docs/screenshots/01-desk.png)

## Quick start

You do not need to clone anything. The image is built and published by GitHub, and the compose
file is the only thing you download.

```bash
mkdir toniefi && cd toniefi
curl -O https://raw.githubusercontent.com/loubylabs/Toniefi/main/docker-compose.yml
docker compose up -d
```

Open <http://127.0.0.1:8080>.

That folder is now the whole installation. TonieFi creates `library/` and `data/` inside it on
first start, and the image comes from `ghcr.io/loubylabs/toniefi:latest`.

No Docker yet? See [Get Docker](#get-docker) below. Prefer to run from source? See
[Without Docker](#without-docker).

### Get Docker

| Your machine | What to install |
|---|---|
| macOS | [Docker Desktop](https://www.docker.com/products/docker-desktop/). Pick the Apple silicon or Intel build to match your Mac |
| Windows | [Docker Desktop](https://www.docker.com/products/docker-desktop/). The installer turns on WSL2 for you |
| Linux | Docker Engine: `curl -fsSL https://get.docker.com \| sh`, then `sudo usermod -aG docker $USER` and log back in |

On macOS and Windows, open Docker Desktop once and leave it running. Then check it works:

```bash
docker --version
```

### Running it, day to day

Every command below runs in the folder that holds `docker-compose.yml`.

| Command | What it does |
|---|---|
| `docker compose up -d` | Starts TonieFi in the background |
| `docker compose ps` | Shows whether it is running |
| `docker compose logs -f` | Watches what it is doing. `Ctrl-C` stops watching, not the app |
| `docker compose restart` | Restarts it |
| `docker compose down` | Stops it and removes the container. Your `library/` and `data/` stay |
| `docker compose pull && docker compose up -d` | Takes an update |

Take an update whenever a source stops downloading. Published images resolve `yt-dlp` at build
time, and a stale copy is the most common cause of "that link does not work".

### Settings you may want

Every setting is optional. They go in a file called `.env` next to `docker-compose.yml`:

```bash
curl -o .env https://raw.githubusercontent.com/loubylabs/Toniefi/main/.env.example
```

On plain Linux, set your own user and group first, or everything the container writes lands
owned by root:

```bash
printf 'TONIEFI_UID=%s\nTONIEFI_GID=%s\n' "$(id -u)" "$(id -g)" >> .env
```

Docker Desktop already maps ownership back to you on macOS and Windows.

[Configuration](docs/operations/configuration.md) lists every variable, including how to put the
library on a NAS share and how to run TonieFi on Unraid.

### Without Docker

This is the path that needs the source. It wants `ffmpeg` and Python 3.10 or newer.

```bash
git clone https://github.com/loubylabs/Toniefi.git
cd Toniefi
brew install ffmpeg          # Debian: sudo apt install ffmpeg
./run-local.sh
```

Open <http://127.0.0.1:8080>. `Ctrl-C` stops it, `PORT=9000 ./run-local.sh` moves it.
`run-local.sh` is safe to re-run, and it upgrades `yt-dlp` on every start.

You can prepare and organise collections with no myTonies account at all. Only reading and
writing Creative Tonies needs one.

## How it works

### 1. Add sources on the Desk

Paste up to 50 links, one per line, and press **Prepare**. Each link becomes its own job, so one
dead link never blocks the rest of the batch. You can also search LibriVox or upload your own
files.

Every source is downloaded and then run through **Forge**, which is the automatic cleanup pass:

| Pass | What it does |
|---|---|
| Chapters | Keeps the source's own chapter markers |
| Level | Normalises loudness to −16 LUFS with a −1.5 dBTP ceiling |
| Titles | Strips noise such as `FULL AUDIOBOOK`, `[HD]` and channel-name prefixes |
| Split | Cuts any track too long to fit one Tonie into even parts |
| Trim | Off by default. Set it if your source has an intro to cut |

Your **Forge defaults** save themselves. Change them once and the same settings prefill the next
URL, LibriVox and upload preparation, on this machine, for every browser that opens TonieFi. They
are starting values and not a rule: each batch stays editable, so one import can keep the source's
chapter markers while the next one ignores them.

A link with `list=` in it gets a **Pick videos** control. It lists the playlist without
downloading anything, and you untick what you do not want. Only the ticked entries are
downloaded, so removing a video costs nothing. Untick every entry and the row is held back with
an inline error rather than submitted, because none of them is not all of them.

The work cart below shows each job as it runs. A row that is ready, has been sent, or has failed
carries a **Dismiss** control that clears it out of the way. Dismissing hides the row; it never
deletes anything, and the job is still there in Activity.

### 2. Choose and send from the Library

![The Library with two stories ticked and a Creative Tonie chosen, ready to send](docs/screenshots/02-library.png)

The Library lists every local collection. Open one to check its cover, chapter titles, order and
playback, or reorder and rename chapters. **Download** hands you the whole collection as one zip.

If a playlist held a video the site refused to serve, the rest still arrive and the row says how
many were left out. The tracks that did arrive are numbered without gaps, so nothing else would
tell you a video was missing.

Sending starts here. Tick the collections you want and a selection bar appears. It packs them
into capacity groups, one Tonie's worth of audio each, and shows exactly which chapters land in
which group. Every group needs its own Creative Tonie before **Send** unlocks, and two groups
cannot name the same Tonie.

A story you do not want to send whole has a **Choose chapters** control. It opens that story's
chapter list with a tick box on each chapter, so a long import can go to one Creative Tonie now and
the rest another day. **All** and **None** tick the whole list, and **Add a Tonie's worth** ticks
forward from the last ticked chapter until the next one would not fit, so nobody has to count
minutes. The story's own tick box still means every chapter, and shows a dash while only some are
chosen.

**Append to the back** is the default and sends straight away, because you can remove the added
chapters afterwards. **Replace everything** always asks first and names every affected Tonie,
because it destroys that Tonie's current cloud audio with no undo.

A send reports itself while it happens. The selection bar becomes a live receipt with a real
percentage, the work cart shows the same send, and the Creative Tonie being written to says so on
its own row. If a send stops part way, it tells you what already landed.

### 3. Tidy a Creative Tonie

![A Creative Tonie expanded, showing its chapter list with rename, reorder and remove controls](docs/screenshots/03-tonies.png)

The Creative Tonies screen reads the live cloud list before every change. Rename a chapter,
reorder by drag or with the Move buttons, tick several chapters and remove them in one save, or
clear the Tonie completely. Each Tonie shows its own figure, and you can rename the Tonie itself
here, so two that were both called "Creative-Tonie" stop being guesswork. Removing and clearing
both ask for confirmation, and the dialog names the figure it is about to change.

If the myTonies app or another tab changed the same Tonie first, TonieFi refuses the stale write
and reloads the real list rather than overwriting someone else's edit. These actions only touch
the Tonie Cloud. Your local library is never changed from this screen.

### Work keeps running

Jobs live in SQLite and carry on when you close the browser. **Activity** keeps the 40 most
recent jobs with their progress, errors and retry buttons. A failed preparation can be retried
without losing the original error.

**Settings** holds your myTonies credentials and a connection test, and names the version you are
running together with the commit its image was built from. Quote both when you report a problem.

## Your files stay yours

```text
library/peter-pan/collection.json
library/peter-pan/cover.jpg
library/peter-pan/001-chapter-i.mp3
library/peter-pan/002-chapter-ii.mp3
```

`collection.json` holds the track order, titles and durations. Files you drop in by hand show up
after a Library rescan. Delete TonieFi and the folders and MP3 files still work in any other
player.

## Before you rely on it

- **The Tonie Cloud API is private and unsupported.** TonieFi is not affiliated with, endorsed by,
  or supported by tonies or Boxine. Endpoints can change without notice. If they do, your local
  library is untouched and the official myTonies app still works.
- **Cloud writes have no undo.** Replacing or clearing a Tonie destroys its current audio.
- **Credentials saved through Settings are stored as plain text** in the local SQLite database.
  Protect the data directory. Environment variables are the safer route. Two-factor myTonies
  accounts are not supported by the current login method.
- **There is no login and no HTTPS.** Do not port-forward TonieFi. Use Tailscale or another
  private network for remote access.
- **Use it for audio you are allowed to copy.** LibriVox is included because its recordings are
  public domain. TonieFi does not bypass DRM.

## Documentation

| Document | What is in it |
|---|---|
| [Configuration](docs/operations/configuration.md) | Every environment variable, compose settings, Unraid, accounts |
| [Troubleshooting](docs/operations/troubleshooting.md) | Sources that will not load, stale `yt-dlp`, common failures |
| [Development](docs/operations/development.md) | Running the test suite, project layout |
| [Architecture](docs/architecture/overview.md) | Modules, background jobs, recovery, storage guarantees |
| [HTTP API](docs/architecture/api.md) | Endpoints and worked `curl` examples |

## License

MIT
