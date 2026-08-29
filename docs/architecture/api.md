# HTTP API

Every endpoint is unauthenticated and speaks JSON unless noted. TonieFi has no application-level
access control, so treat network reachability as the only gate and do not expose it.

## Route map

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/status` | Paths, capacity, tool availability, credential source |
| `POST` | `/api/settings/credentials` | Save a myTonies credential pair locally |
| `DELETE` | `/api/settings/credentials` | Remove locally saved credentials (idempotent) |
| `POST` | `/api/settings/test` | Test the active credentials against the Tonie Cloud |
| `POST` | `/api/prepare` | Queue one preparation job per source URL |
| `POST` | `/api/playlist/preview` | List a playlist's entries without downloading audio |
| `GET` | `/api/librivox/search` | Search LibriVox (`q`, optional `limit`) |
| `POST` | `/api/librivox/import` | Queue a LibriVox book |
| `POST` | `/api/uploads/prepare` | Stage uploaded files as one collection and queue it |
| `POST` | `/api/forge` | Migration path for a legacy `extracted` collection |
| `GET` | `/api/collections` | Every local collection |
| `GET` | `/api/collections/{slug}` | One collection (`refresh=true` rescans it) |
| `PATCH` | `/api/collections/{slug}` | Rename a collection |
| `DELETE` | `/api/collections/{slug}` | Delete a collection and its audio files |
| `POST` | `/api/collections/{slug}/reorder` | Reorder tracks |
| `PATCH` | `/api/collections/{slug}/tracks/{name}` | Rename one track |
| `DELETE` | `/api/collections/{slug}/tracks/{name}` | Delete one track |
| `GET` | `/api/collections/{slug}/cover` | Cover image |
| `GET` | `/api/collections/{slug}/tracks/{name}/audio` | Stream one track |
| `GET` | `/api/collections/{slug}/download` | Whole collection as a streamed zip |
| `GET` | `/api/tonies` | Creative Tonies with their current chapters |
| `POST` | `/api/push/batch` | Queue one confirmed send batch |
| `PUT` | `/api/tonies/{household}/{tonie}/chapters` | Canonical whole-list chapter write |
| `PATCH` | `/api/tonies/{household}/{tonie}` | Rename one Creative Tonie |
| `GET` | `/api/jobs` | Active and recent jobs (default 40) |
| `GET` | `/api/jobs/history` | Job history (default 40) |
| `GET` | `/api/jobs/{id}` | One job |
| `POST` | `/api/jobs/{id}/retry` | Retry one eligible failed job |
| `GET` | `/healthz` | Liveness |

## Prepare several source URLs

`POST /api/prepare` creates one independent preparation job per source and returns every job ID in
source order.

```bash
curl -s -X POST http://127.0.0.1:8080/api/prepare \
  -H 'content-type: application/json' \
  -d '{
    "sources": [
      {"url": "https://www.youtube.com/watch?v=FIRST"},
      {"url": "https://www.youtube.com/playlist?list=SECOND", "playlist_items": [1, 3, 4, 5]}
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

```json
{
  "jobs": [
    {"id": 42, "url": "https://www.youtube.com/watch?v=FIRST"},
    {"id": 43, "url": "https://www.youtube.com/playlist?list=SECOND"}
  ]
}
```

A batch holds at most 50 sources. Whitespace is trimmed, duplicates and unsupported schemes are
rejected, and source order is preserved.

`playlist_items` is optional. It names the playlist entries to download, numbered from 1 in
playlist order. Omit it, or send `null`, to let the link decide. A list is a pick, so it is
rejected when it is empty and when any number in it is below 1.

`POST /api/playlist/preview` with `{"url": "..."}` returns those numbers alongside each entry
title, without downloading audio.

## Upload one collection from several files

Send every selected file under the repeated `files` field. The server stages the whole collection
and creates one persisted preparation job.

```bash
curl -s -X POST http://127.0.0.1:8080/api/uploads/prepare \
  -F 'files=@chapter-01.mp3' \
  -F 'files=@chapter-02.mp3' \
  -F 'title=Peter Pan' \
  -F 'options={"use_chapters":true,"normalize":true,"clean_titles":true,"trim_head":0,"trim_tail":0,"split_oversized":true}'
```

One upload collection can hold up to 500 files and 20 GiB of staged audio. Incoming files stream to
`UPLOAD_STAGE_DIR`, which defaults to persistent storage under `DATA_DIR`. Expired owned upload
stages are cleaned after 24 hours. The `/work` tmpfs is reserved for disposable downloads and
transcodes.

## Send a confirmed batch

`POST /api/push/batch` takes one assignment per capacity group. Each assignment carries its target
Tonie, its effect, and its own `sources`: collection slugs, manifest fingerprints, and the exact
files in that group.

The server validates every assignment against the manifests on disk, never against the file order
the browser submitted. Job creation is atomic: either every assignment is queued or none is. The
`operation_key` makes an uncertain response safe to retry without creating a second send batch.

A batch holds at most 100 assignments. Manifest fingerprints are exactly 64 characters.

Push jobs are not retryable through `/api/jobs/{id}/retry`, because a Creative Tonie write has no
undo and remote state may have changed.

## Rename a Creative Tonie

```bash
curl -s -X PATCH http://127.0.0.1:8080/api/tonies/HOUSEHOLD/TONIE \
  -H 'content-type: application/json' \
  -d '{"base_name": "Creative Tonie", "name": "Bedtime Bear"}'
```

The body is only the name and the name the browser had on screen. The upstream
`PATCH /households/{h}/creativetonies/{t}` accepts a bare `{"name": ...}`, and its own
documentation states that only including `chapters` triggers a re-transcode, so a rename never
touches a Tonie's audio.

`base_name` is the precondition. The Tonie Cloud offers no conditional write, so TonieFi re-reads
the Tonie and compares its current name before writing. That narrows the lost-update window to one
round trip; it cannot close it.

| Status | When |
| --- | --- |
| `200` | Renamed. The body is the same shape as one entry of `GET /api/tonies`. |
| `400` | The name was empty after trimming, or the Tonie Cloud refused the write. |
| `409` | The Tonie was renamed somewhere else since the browser read it. |

The name is trimmed and capped at 100 characters, matching the upstream `maxLength`. That is a
different limit from the 128 characters a chapter title allows.

## Finish a legacy extracted collection

Library and the collection page show **Finish preparation** for an older collection whose manifest
stage is `extracted`. The action enqueues the persisted Forge job exactly once. The same migration
route is available directly:

```bash
curl -s -X POST http://127.0.0.1:8080/api/forge \
  -H 'content-type: application/json' \
  -d '{"slug":"legacy-collection"}'
```

Assignment stays unavailable until the collection reaches manifest stage `forged` and appears in
the Library ready to send.

## Jobs

```bash
curl -s http://127.0.0.1:8080/api/jobs | python3 -m json.tool
curl -s -X POST http://127.0.0.1:8080/api/jobs/42/retry
```

`GET /api/jobs` returns current and recent work; `GET /api/jobs/history` returns the history,
including failures kept after an eligible retry. Both default to 40 rows.

Every job carries `progress`, the worker's own sentence, and `progress_percent`, a number from 0 to
100 or `null`. `null` means the current phase has nothing it can honestly measure, such as signing
in to myTonies, and the front end renders it as an indeterminate meter rather than as zero
progress. A send's percentage is weighted by audio bytes uploaded, not by chapter count, so a
30-second intro and a 20-minute chapter do not count as equal work. Nothing derives the figure by
parsing `progress`.

A push job's `phase` reads `sending` while it runs and `sent` once it finishes.
