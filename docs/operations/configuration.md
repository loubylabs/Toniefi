# Configuration

Every setting is an environment variable. The defaults run TonieFi with its library in
`library/`, in the folder that holds `docker-compose.yml`, so a first run needs no configuration
at all.

## Compose settings

These are read by `docker-compose.yml` and belong in a `.env` file next to it. A Docker
installation is only that compose file, so fetch the commented starting point rather than
looking for it in a checkout:

```bash
curl -o .env https://raw.githubusercontent.com/loubylabs/Toniefi/main/.env.example
```

| Variable | Default | Purpose |
|---|---|---|
| `TONIEFI_LIBRARY` | `./library` | Host path for audiobook collections |
| `TONIEFI_DATA` | `./data` | Host path for SQLite history and settings |
| `TONIEFI_PORT` | `8080` | Published host port |
| `TONIEFI_UID` / `TONIEFI_GID` | `0` | Container user and group on Linux |
| `TONIEFI_WORK_SIZE` | `2g` | RAM-backed download and transcode scratch space |

`TONIEFI_WORK_SIZE` sizes a tmpfs, which is real memory. Keep it below what the Docker VM
actually has.

## Application settings

These are read by the application itself and work in both run modes.

| Variable | Default | Purpose |
|---|---|---|
| `LIBRARY_DIR` | `/library` | Finished and in-progress local collections |
| `DATA_DIR` | `/data` | SQLite job history and locally saved settings |
| `WORK_DIR` | `/work` | Disposable downloads and transcodes |
| `UPLOAD_STAGE_DIR` | `DATA_DIR/upload-staging` | Restart-safe retained upload inputs |
| `TONIE_LIMIT_SECONDS` | `5400` | Capacity of one Creative Tonie |
| `TONIE_HEADROOM_SECONDS` | `30` | Safety margin used by capacity planning |
| `TONIES_USERNAME` / `TONIES_PASSWORD` | unset | Environment myTonies credentials |
| `AUDIO_BITRATE` | `128k` | Transcode target bitrate |
| `AUDIO_SAMPLE_RATE` | `44100` | Transcode target sample rate |
| `WORKER_THREADS` | `2` | Concurrent background jobs |
| `YTDLP_PLAYER_CLIENTS` | `default,android` | YouTube clients that `yt-dlp` may use |

`UPLOAD_STAGE_DIR` deliberately defaults inside `DATA_DIR` rather than `WORK_DIR`. Retained
upload input has to survive a restart, and `/work` is a bounded tmpfs holding only disposable
downloads and transcodes.

## Storage limits

| Limit | Value |
|---|---|
| Sources in one prepare batch | 50 |
| Files in one upload collection | 500 |
| Staged audio in one upload collection | 20 GiB |
| Upload staging retention after a failure | 24 hours |
| Jobs kept in Activity | 40 |

## myTonies accounts

Settings labels a complete environment or saved credential pair **Configured**. It becomes
**Connected** only after a successful connection test in the current browser session. The tested
timestamp is kept for that session, and a failed test shows an explicit connection failure.
Settings also names where the active credentials come from: environment variables, local SQLite,
or nowhere.

Environment credentials take precedence. When both `TONIES_USERNAME` and `TONIES_PASSWORD` are
set, the local form fields are disabled, because a saved value could not override them anyway.
Removing saved credentials is idempotent and never touches environment variables.

Credentials saved through Settings are stored as **plain text** in the local SQLite database.
Protect the data directory, or prefer the environment variables. The private password-grant login
TonieFi uses does not support myTonies accounts with two-factor authentication. Passwords are
never returned by any TonieFi API and are never placed back into a browser field.

## Running on Unraid

Use the same compose file with array-backed paths in `.env`:

```text
TONIEFI_LIBRARY=/mnt/user/media/toniefi
TONIEFI_DATA=/mnt/user/appdata/toniefi
TONIEFI_UID=99
TONIEFI_GID=100
```

Then `docker compose pull && docker compose up -d`, and open `http://<tower>:8080`.

TonieFi binds plain HTTP and has no application-level access control. Do not port-forward it.
Use Tailscale or another private network for remote access.

## Taking an update

The image is built and published by GitHub Actions to `ghcr.io/loubylabs/toniefi:latest`. Every
commit on `main` also gets an immutable `sha-<short>` tag, which is what to pin if you ever need
to hold a version still. Settings names the commit the running image was built from.

```bash
docker compose pull
docker compose up -d
```

`run-local.sh` upgrades its Python dependencies on every start, so a local run picks up a fresh
`yt-dlp` by restarting it.
