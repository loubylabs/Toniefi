# Troubleshooting

## A YouTube link says the video is unavailable

Sites change their players often, and a stale `yt-dlp` reports a perfectly playable video as
unavailable. That is the single most common cause.

Published images resolve `yt-dlp` at build time, so `docker compose pull` picks up a fresh one.
`run-local.sh` upgrades dependencies on every start, so restarting it is enough.

If the link still fails, try a different YouTube client:

```bash
YTDLP_PLAYER_CLIENTS=default,android,web_safari ./run-local.sh
```

Test a candidate client on its own first:

```bash
yt-dlp --extractor-args "youtube:player_client=CLIENT" --simulate URL
```

The default client set alone gets refused for a lot of otherwise-playable videos. The `tv` client
asks to reload the page, and `web`/`ios` return metadata but no downloadable formats, which
surfaces as the misleading "This video is not available". `android` is named as a fallback for
exactly that reason.

TonieFi cannot load a private, deleted, region-blocked, or DRM-protected source, and no client
setting changes that.

## The tray will not accept my playlist row

Unticking every entry holds the row back with **Pick at least one video from this playlist, or
remove the row**. A pick that names nothing is refused rather than treated as "no choice made",
because the old fallback downloaded the entire playlist. Tick at least one entry, or remove the
row.

## A download stopped part way through

That is deliberate. Each file's identity is confirmed before its first block and again after its
last, so a delete or a Forge replacement landing mid-download ends the stream. The response is
chunked, so an ended stream never sends its terminating chunk and the browser reports an
interrupted download rather than saving a plausible-looking archive.

Wait until no job is touching that collection, then download again.

## A job failed and Activity offers no Retry

Preparation jobs are retryable. Push jobs are not, because a Creative Tonie write has no undo and
the remote state may have changed since the failure. Fix the problem on the Library and send the
selection again.

## Nothing happens when I press Send

Send stays disabled until every capacity group names a Creative Tonie, and two groups may not
name the same Tonie. The selection bar states which group is missing a target.

## The library is full of root-owned files

On plain Linux the container runs as root unless told otherwise. Set your own ids before starting:

```bash
printf 'TONIEFI_UID=%s\nTONIEFI_GID=%s\n' "$(id -u)" "$(id -g)" >> .env
docker compose up -d
```

Docker Desktop maps ownership back to the host user on macOS and Windows, so this is a Linux-only
step.

## Files added by hand do not appear

The Library indexes what is on disk when it scans. Press **Rescan** on the Library after adding
folders or audio files by hand.

## Two TonieFi processes at once

Run only one TonieFi process against a given SQLite database. Two writers on one `portal.db` is
not a supported configuration.
