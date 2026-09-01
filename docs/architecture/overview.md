# Architecture

How TonieFi is put together, and which guarantees the code actually makes. This is the reference
for the behaviour the [README](../../README.md) summarises.

## Shape

```text
Desk  ->  /api/prepare  ->  extraction  ->  Forge  ->  Library  ->  /api/push/batch  ->  Tonie Cloud
```

One HTTP process, one SQLite database, one background worker pool, and a browser front end of
plain ES modules with no build step. Everything long-running is a persisted job, so closing the
browser never cancels work.

| Module | Responsibility |
|---|---|
| `app/main.py` | FastAPI routes and the application shell |
| `app/prepare.py` | Extract-to-Forge preparation orchestration |
| `app/ingest.py` | URL, LibriVox, and staged upload extraction |
| `app/forge.py` | Trim, loudness, title cleanup, splitting |
| `app/audio.py` | ffmpeg and ffprobe wrappers, capacity packing |
| `app/library.py` | On-disk manifests, writer leases, atomic staged publication |
| `app/archive.py` | Streamed zip download of one collection |
| `app/tonies.py` | Private Tonie Cloud client |
| `app/push.py` | Confirmed sends and canonical chapter-list writes |
| `app/jobs.py` | SQLite-backed background worker and retry rules |
| `app/db.py` | Job state and settings storage |

The former manual probe, single-URL import route, five-step wizard, and one-job browser watcher
have all been retired. New preparation enters through `/api/prepare`, `/api/librivox/import`, or
`/api/uploads/prepare` and advances through automatic Forge before appearing in the Library. The
persisted `/api/forge` route remains as the single migration path for legacy extracted
collections.

## Batch preparation

A batch is up to 50 HTTP or HTTPS source URLs. TonieFi trims whitespace, rejects duplicates and
unsupported schemes, preserves source order, and creates one independent job per source. A failure
in one source does not block the rest of the batch.

Every accepted source runs extraction and then the selected Forge sequence automatically. Forge
defaults save automatically as one complete local profile in SQLite. They prefill URL, LibriVox,
and upload preparation and remain editable for each batch. One application-owned coordinator keeps
saves ordered across Desk navigation and uses unload-safe delivery. An invalid stored profile
surfaces an error, and a later complete edit repairs it. The profile supplies starting values and
never enforces chapter behavior. TonieFi never assigns a Creative Tonie automatically; the
finished collection lands in the Library and waits there.

### Playlists

A source link carrying a `list=` parameter offers a **Pick videos** control. It lists the playlist
via `POST /api/playlist/preview` without downloading any audio, ticks every playable entry, and
lets the operator untick what they do not want. Only ticked entries are downloaded, so removing a
video costs nothing.

Leave the control alone and the link speaks for itself. A `playlist?list=...` link brings every
entry; a `watch?v=...&list=...` link brings that one video, not the playlist standing behind it.

**No pick and a pick of nothing are different values end to end.** `playlist_items` is `null`
until the playlist has actually been read, and a list once it has. Only `null` falls back to
`--no-playlist`; a list that names nothing is refused by `POST /api/prepare`, raised on by
`import_url`, and held back in the tray with an inline error. Collapsing the two is what once made
unticking every entry download all of them, since a bare playlist link ignores `--no-playlist`.

A playlist that mixes videos carrying chapter markers with videos that have none keeps both.
Chapters win for the video that has them, and a video without any keeps its whole file.

## Forge and packing

| Pass | Behaviour |
|---|---|
| Trim | Cuts the configured number of seconds from the front or back of every track. The batch default is zero. |
| Level | Normalises to −16 LUFS under EBU R128 with a −1.5 dBTP ceiling. |
| Titles | Removes common source labels such as `FULL AUDIOBOOK`, `[HD]`, trailing `- YouTube`, and channel-name prefixes. |
| Split | Divides an oversized track into even re-encoded parts that fit usable capacity. |

−16 LUFS with a −1.5 dBTP ceiling is the strongest safe loudness target currently supported
without clipping.

A Creative Tonie holds 90 minutes. TonieFi subtracts configurable safety headroom before planning,
packs chapters **sequentially**, and preserves source order. A later chapter never moves ahead of
an earlier chapter to fill a gap.

## Confirmed sends

A send starts on the Library. Ticking finished collections raises a selection bar showing the
total selected and the send order: the Library's own order, newest first, and within a collection
its manifest track order. The bar breaks the selection into capacity groups, one per Tonie's
worth of audio, and shows each group's exact chapter membership.

Each capacity group gets its own Creative Tonie picker. No target is preselected, Send stays
disabled until every group names a Tonie, and two groups may not name the same Tonie. Each option
shows the Tonie's name, its household, and its free space.

**Append to the back** is the default effect for every group. **Replace everything** is the
deliberate opposite choice.

An append-only send posts with no confirmation dialog, because appending is recoverable: the added
chapters can be removed afterwards. Any send that includes a replace opens the irreversible-action
dialog first, naming every affected Tonie, because replacing destroys that Tonie's current cloud
audio with no undo.

### What `/api/push/batch` guarantees

`POST /api/push/batch` submits one assignment per capacity group, each carrying its own `sources`:
the collection slugs, their manifest fingerprints, and the exact files in that group.

- The server validates every assignment **against the manifests on disk**, never against the file
  order the browser submitted. An omitted track, a duplicated track, a reordered collection, or two
  interleaved collections are all refused, while a legitimate capacity split is accepted.
- Job creation for the batch is atomic. Either every assignment is queued or none is.
- One selection holds one **operation key** until its payload changes, which makes an uncertain
  response safe to retry without creating a second send batch. An in-flight lock keeps a double
  click from queuing a second batch.

The operation key tracks the operator's intent, not the remote snapshot. It clears on a
membership, target or effect change, and on success. **A target refresh never clears it**, whatever
the refresh returned and whether it failed, because clearing it on refresh means a send that landed
but lost its response gets appended a second time.

Activity offers no generic Retry for push jobs, because Creative Tonie writes have no undo and
remote state may have changed. If a send fails, fix the problem on the Library and send again.

## Creative Tonie chapter management

Two upstream facts shape all of this code, and neither is discoverable without reading the Tonie
Cloud's behaviour:

1. **The chapter write is whole-list only.** `PATCH /households/{h}/creativetonies/{t}` with a body
   of `{"chapters": [...]}`. Rename, reorder and delete are all that one call, and it silently
   drops whatever the list omits. There is no undo.
2. **There is no conditional write.** No ETag, no `If-Match`, no version, no compare-and-set.

So every edit begins from a fresh remote list, and the save carries a `base` precondition. That
precondition compares chapter **titles as well as IDs**, because the title is the only field the
endpoint writes, so an ID-only guard would be blind to exactly the change it most needs to catch.
This narrows the lost-update window from the life of an open tab to a single HTTP round trip. It
cannot close it; that is blocked upstream, not by the code.

While a save is running, every competing edit is disabled. If the myTonies app, another tab, or a
background send changes the same Tonie first, TonieFi refuses the stale write and reloads remote
truth. A failed response also triggers a remote reload before controls become available again.

Chapters are ticked and removed together: Select all, or tick individual rows, then remove the
selected chapters in one whole-list save behind one confirmation. Clear all chapters remains, for
wiping a Tonie in one step. Both require explicit irreversible-action confirmation. These
operations affect only the Tonie Cloud; the local library is never changed from this screen.

Renaming trims surrounding spaces and caps a changed title at 128 characters. An empty changed
title keeps the existing title. Untouched chapter fields survive reorder and rename writes.

`TonieCloud.close()` deliberately cannot raise. A transport error tearing down the connection used
to propagate out of the `finally` after a successful write, turning a landed write into a reported
failure.

## Recovery and job history

Background jobs persist in SQLite and continue when the browser closes. Desk shows active and
recent preparation work. Activity retains failed attempts after an eligible retry, so ordinary
recovery never erases the original error.

- Eligible preparation retries create a new job. A forged collection is terminal, so an older Forge
  worker or cloned job returns its existing output without transforming audio again.
- URL, LibriVox, and upload inputs keep a hidden deterministic collection stage until Forge and
  final publication both succeed. Nothing half-built is ever visible in the Library.
- Final slugs are reserved before extraction across visible and hidden collections, so same-title
  jobs keep distinct, retry-stable folders.
- A durable extraction-complete checkpoint prevents Forge from consuming partial staged files.
- Retry resumes safe staged work or restarts from immutable input, without ever exposing a partial
  collection.
- A visible publication receipt consumes its hidden source during recovery and sweep, even while a
  failed job still references that source.
- Failed upload staging stays available for retry for 24 hours.
- A failed historical Forge attempt whose collection is already forged resolves to the completed
  collection instead of re-running.

Once a collection manifest reaches `forged`, that stage is the canonical terminal receipt
regardless of which historical Forge operation completed it. A restart, direct worker invocation,
cloned job, or retry returns the existing collection without transforming its audio again.

### The refresh coordinator

The interface refreshes jobs, history, collections, and status through one application coordinator.
Each resource publishes independently, so job progress stays current while a collection lease
delays the collection index. The coordinator polls faster while work is active, slows when idle,
and stops while the page is hidden.

## Library layout and ownership

```text
library/peter-pan/collection.json
library/peter-pan/cover.jpg
library/peter-pan/001-chapter-i.mp3
library/peter-pan/002-chapter-ii.mp3
```

`collection.json` owns track order, titles, metadata, and cached durations. Files added by hand
appear after a Library rescan. Deleting TonieFi leaves the folders and MP3 files usable by other
software.

Library deletion is intentionally destructive. Its confirmation names the local collection folder
and the audio files that will be removed.

### The zip download

**Download** on any Library row returns that collection as one zip of audio, cover art, and
`collection.json`, so the files are reachable without shell access to the host.

- Track order lives in the manifest rather than in the filenames, so the archive renumbers its
  tracks from the manifest. A collection reordered on its collection page still unpacks in that
  order.
- The archived `collection.json` is rewritten to name the files the archive actually holds, so the
  index never points at a filename the archive lacks.
- The archive is streamed as it is built and stores its members uncompressed, so it never has to
  fit in memory and never re-compresses already-compressed audio.
- One file is open at a time, so a collection at the 500-file intake limit cannot exhaust the
  process descriptor budget, and an abandoned download strands nothing.
- Each file is fingerprinted (inode, size, mtime) when the download is planned, then confirmed
  from the open descriptor **twice**: once before its first block and again after its last. A
  delete or a Forge replacement landing between two files ends the stream at the next file, and one
  landing inside a single file's read loop ends it at that file.
- The response is chunked, so an ended stream never sends its terminating chunk and the browser
  reports an interrupted download instead of saving a plausible-looking archive.

The closing check is what covers a writer rewriting a file **in place** while it is being read. The
descriptor stays valid through that, so a single check before the loop saw nothing wrong and the
download finished as a valid zip holding half of each version behind an index describing neither.
