# Creative Tonie Version History

**Date:** 2026-10-10
**Status:** Approved for implementation

## Summary

Toniefi keeps a read-only history of the chapter list on each Creative Tonie. Every time Toniefi
sees a chapter list that differs from the last one it stored for that Tonie, it stores a new
version. The Tonies screen shows the versions newest first, with a one-line summary of what
changed and the full chapter list of each version.

The Tonie Cloud has no undo: every chapter write replaces the whole list. This history is the
household's record of what used to be on a Tonie.

## Goals

- Store one version per distinct chapter list, per Creative Tonie.
- Capture changes made by Toniefi and changes made elsewhere (the myTonies app), and say which.
- Show the history on the Tonies screen, including for a Tonie that is now empty.
- Never let history recording turn a landed Tonie write into a reported failure.

## Non-goals

- Restoring a version. There is no restore button and no restore endpoint.
- Copying or keeping any audio. A version holds titles, ids and durations only.
- Seeing changes made elsewhere at the moment they happen. Toniefi only sees a Tonie when it reads it.
- History from before this ships.
- Tracking the Tonie's name as a change. The name is stored for display only.
- A retention limit. Rows are small text; one household produces few of them.

## Data model

A new table, created by `CREATE TABLE IF NOT EXISTS` in `db.SCHEMA` (a new table needs no migration):

```sql
CREATE TABLE IF NOT EXISTS tonie_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id  TEXT NOT NULL,
    tonie_id      TEXT NOT NULL,
    tonie_name    TEXT NOT NULL DEFAULT '',
    chapters      TEXT NOT NULL,           -- JSON list of {"id", "title", "seconds"}
    source        TEXT NOT NULL,           -- 'toniefi' or 'seen'
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tonie_versions_tonie_idx ON tonie_versions(household_id, tonie_id, id);
```

- `source = 'toniefi'`: the list Toniefi itself just wrote.
- `source = 'seen'`: a list Toniefi read and had not stored before, so something else changed it
  (or it is the first time Toniefi has seen this Tonie).

## The "is it different" rule

A list is different from the last stored version when its ordered sequence of `(id, title)` pairs
differs. This is the same identity the chapter write's `base` precondition uses.

`seconds` is stored but **not compared**. A chapter mid-transcode reports `seconds: 0` and later a
real figure; comparing seconds would store a fake version on every transcode.

The first list ever seen for a Tonie is always stored, including an empty list.

## Recording

One database function, `db.record_tonie_version(household_id, tonie_id, tonie_name, chapters, source, now)`.
Under the module `_lock` and one `BEGIN IMMEDIATE` transaction, it reads the newest row for that
Tonie, applies the rule above, and inserts only when different. It returns whether it inserted.

One wrapper in a new module, `tonie_history.remember(household_id, tonie_id, tonie, source)`, takes a raw or described
Tonie dict, normalises its chapters to `{id, title, seconds}`, and calls the database function.
**It never raises.** Any exception is logged and swallowed, because it runs after landed writes,
where the existing rule is that nothing that can raise may run.

### Where it runs

| Place | When | Source |
|---|---|---|
| `GET /api/tonies` (`main.list_tonies`) | for every Tonie returned, except one being written or written since the read started | `seen` |
| `push._set_tonie_chapters_locked` | right after `get_tonie`, before the merge | `seen` |
| `push._set_tonie_chapters_locked` | right after `set_chapters` returns, with the list just written | `toniefi` |
| `push.set_tonie_name` | right after `get_tonie` | `seen` |
| `push._push_confirmed_tracks` | right after the first `get_tonie`, before the stale check | `seen` |
| `push._push_confirmed_tracks` | after the final confirming `get_tonie` | `toniefi` |
| `push._push_confirmed_tracks` | on `PartialSend`: one best-effort `get_tonie`, then record | `toniefi` |

Notes:

- The `seen` record before a write captures a myTonies change made since Toniefi last looked, even
  when the write then refuses as stale. That is exactly the case worth keeping.
- `GET /api/tonies` takes no write lease, so `push.remember_seen_if_quiet` guards it. It skips a
  Tonie whose lease another request holds (a read mid-send holds a partial list), and one whose
  lease was released after the read started (the read may hold the list from before that write).
  Either stored as `seen` would label Toniefi's own change as made outside it. The skipped state is
  already recorded as `toniefi` by the write, or gets recorded by the next read.
- A rename of the Tonie itself changes no chapters, so it records no `toniefi` version.
- A replace send clears and then uploads. Only the final list is stored, not the empty middle state.
- The `PartialSend` capture must not mask the `PartialSend` itself. If its `get_tonie` fails, the
  next read records the state as `seen`.

## API

`GET /api/tonies/{household_id}/{tonie_id}/versions` returns versions newest first:

```json
[
  {
    "id": 12,
    "created_at": 1791648000.0,
    "source": "toniefi",
    "tonie_name": "Bedtime",
    "chapters": [{"id": "a", "title": "Chapter 1", "seconds": 312.0, "duration": "5m 12s"}],
    "changes": {"first": false, "added": 2, "removed": 1, "renamed": 0, "reordered": false}
  }
]
```

`changes` compares each version to the one before it, by chapter id:

- `added`: ids in this version, not the previous one.
- `removed`: ids in the previous version, not this one.
- `renamed`: ids in both, with a different title.
- `reordered`: the ids in both appear in a different relative order.
- `first`: true for the oldest stored version; the counts are then 0.

An unknown Tonie returns `[]`, not 404. The route reads only the database and never calls the Tonie Cloud.

## Screen

In each open Tonie's detail panel on the Tonies screen, a **Version history** disclosure. It is
added before the empty-state return, so an empty Tonie shows it too.

- Opening it fetches the versions once per open. A failed fetch shows a short error inside the
  disclosure; the rest of the panel keeps working.
- One row per version: the date and time, the source ("Toniefi" or "Changed outside Toniefi"), and
  the summary, for example "2 added, 1 removed" or "First seen, 12 chapters". A version whose only
  change is order reads "Reordered".
- Each row is a native `<details>`; opening it shows that version's numbered chapter list with titles
  and durations.
- No version reads as "now". The newest version is the last one Toniefi saw, not a live read.
- Text only via `textContent` (the existing `element` helper). No `innerHTML`.

## Error handling

- Recording failures are logged and swallowed (see Recording). History is best effort; Tonie writes are not.
- The versions route returns 500 only on a database failure.

## Testing

All tests use stubbed cloud clients. No test contacts a real myTonies account.

- `db.record_tonie_version`: first list stored; identical list skipped; title change stored;
  reorder stored; seconds-only change skipped; separate Tonies kept apart.
- `changes` summary: first, added, removed, renamed, reordered, and combinations.
- Hooks: `GET /api/tonies` records `seen`; a chapter save records `seen` then `toniefi`; a stale
  chapter save still records the `seen` state; a recording failure after `set_chapters` still
  returns success; a push records `seen` then `toniefi`; a `PartialSend` records the partial state
  and still raises `PartialSend`.
- Route: shape, newest first, unknown Tonie returns `[]`.
- Browser: the history disclosure renders rows and summaries from a stubbed response, and shows
  on an empty Tonie.

## Live verification

Only self-reversing operations against the real account: rename one chapter and rename it back.
Expect exactly two new `toniefi` versions and no `seen` version on the following list read. That
proves the Cloud keeps chapter ids across a whole-list write. Never Remove or Clear live.
