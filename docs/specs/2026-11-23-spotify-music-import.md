# Spotify Music Import

**Date:** 2026-11-23
**Status:** Approved for implementation

## Summary

A Spotify playlist, album or song link pasted on the Desk becomes one collection. TonieFi reads
the song list from Spotify's public embed page, finds each song on YouTube by search, and downloads
that audio with yt-dlp. Nobody logs in to Spotify, and nobody needs a Spotify developer app or
Spotify Premium.

This reverses an earlier product rule. Until now a Spotify music link was refused with "TonieFi
imports podcasts from Spotify, not music." The owner lifted that rule on 2026-11-23.

## Why

A household wants a child's playlist on a Creative Tonie for offline listening. The owner's
alternative was a second tool that needs a Spotify developer app, Premium, and a user allow-list
for every person who installs it. That setup is too much for a parent. Pasting a link is the bar.

## Goals

- Paste a public Spotify playlist, album or song link and get one collection.
- No Spotify account, login, API key or developer app, for anyone.
- Reuse the Desk as it is: the same tray row, the same picker, the same Prepare button.
- Let the operator tick which songs to bring, before anything downloads.
- One song that cannot be found never fails the rest.
- Prefer the right recording: match by song length, not by search rank alone.

## Non-goals

- Private playlists and Liked Songs. The embed page only serves public content.
- More than 100 songs from one playlist. The embed page stops there.
- Artist links. An artist page is not a list the operator chose.
- A Spotify login, the Spotify Web API, or an Exportify CSV import.
- Any audio from Spotify itself. Spotify supplies names and lengths only.
- Letting the operator choose between YouTube candidates for a song.
- Embedding ID3 tags beyond what the existing pipeline already writes.

## Anti-goals

- A wrong recording presented as the right one without any trace. A song with no close match is
  skipped and named, not replaced with the first search result.
- A second Desk workflow. If this needs its own screen, the design is wrong.

## Approaches considered

| Approach | Trade-off |
|---|---|
| **Paste a Spotify link, read the embed page** (chosen) | Zero setup. Public content only, 100 songs per playlist, and it breaks if Spotify changes the page. TonieFi already carries that last risk for podcasts. |
| Drop an Exportify CSV | Covers private playlists and Liked Songs. Costs the user a second website and a login, needs a new file-drop surface, and depends on a third party staying up. |
| Spotify Web API with OAuth | Covers everything. Every installer needs a developer app, Premium and an allow-list. This is the cost the feature exists to avoid. |

The owner picked the first. Exportify stays a possible later addition if Liked Songs matter.

## Link shapes

A music link is an `open.spotify.com` URL whose path, after an optional `intl-xx` locale segment
and an optional `embed` segment, is `playlist/<id>`, `album/<id>` or `track/<id>`.

An `artist/<id>` link is refused with 400 and this message:

> Paste a Spotify playlist, album or song link. Artist pages are not supported.

Show and episode links stay podcast links and are untouched.

## Reading the song list

`GET https://open.spotify.com/embed/<kind>/<id>` returns a page whose `__NEXT_DATA__` script holds
`props.pageProps.state.data.entity`. This is the same page and the same parse the podcast import
uses. The one reader is shared, not copied.

| Kind | Collection title | Owner | Songs |
|---|---|---|---|
| playlist | `entity.name` | `entity.subtitle` | `entity.trackList` |
| album | `entity.name` | `entity.subtitle` (the artist) | `entity.trackList` |
| track | `entity.name` | the joined `entity.artists[].name` | the entity itself, as one song |

Each `trackList` item gives `uri` (`spotify:track:<id>`), `title`, `subtitle` (the artists, joined
with a comma and a no-break space), `duration` in milliseconds, `isPlayable` and `isExplicit`. A
track entity gives `uri`, `name`, `artists`, `duration`, `isPlayable` and `isExplicit`.

Rules:

- The no-break space in an artist list is replaced with a normal space.
- A song whose `uri` already appeared earlier in the list is dropped. Songs are numbered from 1
  after that, in list order.
- The cover is the entry of `entity.visualIdentity.image` with the greatest `maxWidth`. A missing
  cover is not an error.
- A page with no entity, no name, or no songs raises the existing "Spotify changed its page"
  error, reworded so it no longer tells a music user to try Apple Podcasts. The podcast reader
  keeps its own wording.
- A page Spotify changed must never crash the reader. A field of the wrong type is treated as
  missing: a bad cover, owner, artist list or duration becomes empty or unknown, and the song
  stays; a song whose `uri` or `title` is not text is dropped. A `name` that is not text, or a
  list left with no usable song, raises the "Spotify changed its page" error, which the preview
  answers as a 502. The shared reader demands a text `name` for every kind, and the podcast
  reader also demands a text `subtitle`.
- A playlist that returns exactly 100 songs has reached the page's limit.

## Preview

`POST /api/playlist/preview` with a music link answers in the existing shape:

```json
{
  "title": "Car Songs",
  "entries": [
    {"index": 1, "id": "spotify:track:abc", "title": "Baby Beluga - Raffi", "available": true}
  ],
  "kind": "music",
  "notice": "Spotify lists only the first 100 songs of a playlist. Songs after that are not shown."
}
```

- `title` of an entry is `"<song> - <artists>"`. An explicit song gets the suffix ` (explicit)`,
  in the preview only. The audience is a child's speaker, so the operator should see it.
- `available` is `isPlayable`.
- `notice` is present only when a playlist reached the 100-song limit.
- There is no `preselect`. Every available song starts ticked, which is the picker's default.
- An unreadable page is a 502 with the reader's message. An artist link is a 400.

## Prepare

`POST /api/prepare` accepts a music source:

```json
{"sources": [{"url": "https://open.spotify.com/playlist/37i9...", "track_ids": ["spotify:track:abc"]}]}
```

- `track_ids` is a new optional field on a source: the `id` of each picked preview entry. Ids
  survive a playlist that gains or drops a song between the preview and the job, where positions
  would shift. This is the same reason podcasts pick by guid.
- Omitted or `null` means every available song.
- An empty list is refused with 400: "Pick at least one song, or remove that source."
- `track_ids` on a source that is not a music link is refused with 400.
- `playlist_items` or `episode_ids` on a music source is refused with 400.
- `kind` stays `"podcast"` only. A music link is always known by its shape.
- The job payload carries `track_ids`. The job kind stays `prepare_url`.

The music refusal and its constant are deleted, with the tests that pinned them.

## Import

A new module, `app/music.py`, owns link shapes, the listing, the preview, the match and the
import. `prepare.run` routes a music link to it before the podcast and URL branches.

`import_music(url, *, stage_id, track_ids=None, progress)`:

1. Return the published collection if this stage already published. Fail clearly if yt-dlp is
   missing.
2. Read the song list again. Take every available song when `track_ids` is `None`, else the songs
   whose id is in it, in list order. Picked ids that have left the list add one line to `skipped`:
   "1 picked song is no longer in the list." or "N picked songs are no longer in the list."
3. Fail with "None of the picked songs could be found on YouTube." if nothing is picked.
4. Begin the collection stage with `source="music"` and extras `url`, `uploader` (the owner), and
   `skipped`. The stage is not restarted, so a retried job resumes.
5. For each picked song, at position `p` from 1:
   - The song's key is the slug of `"song - artists"`. When two picked songs share a key, each of
     them gets `-<slug of its Spotify id>` appended, so neither is lost and both keep their names
     on a retry.
   - The file is `<p:03d>-<key>.mp3`. If it already exists, keep it. If an earlier run stored the
     same key under another position, because a song was added to or removed from the list since,
     rename that file to the new position instead of downloading it again.
   - Report `Finding p/n: <song>` with a measured percent.
   - Find a YouTube video (below). No match adds `"<song> - <artists>: no close match on YouTube."`
     to `skipped`; a failed search adds `"<song> - <artists>: <why it failed>"` instead.
   - Download it (below). A failure adds `"<song> - <artists>: <yt-dlp's error summary>"` to
     `skipped`.
6. Fail with the step-3 message if no song was stored.
7. Download the cover to `cover.jpg` when there is one and the file is absent. A failure is
   ignored.
8. Delete every audio file in the stage that is not a song stored by this run, and every `.part`
   file. A retry after the list changed would otherwise publish a song nobody picks any more.
9. Rescan the stage, write `skipped`, set each track's title to `"<song> - <artists>"`, probe
   durations, and complete the stage, the way the podcast import does.

The library lease (`library.collection_lease()`) is held only for step 4 and for steps 8 and 9.
It is the one lock behind the Library listing, the collection page and the other worker, so no
search or download runs under it. The stage needs no lock in between: only this job's identity
creates, resumes or discards it, and nothing reads its audio before step 9.

The default Forge sequence then runs as for any URL source.

### Finding the video

One yt-dlp call per song, with no download:

```
yt-dlp --flat-playlist --dump-single-json --no-warnings <player-client args> "ytsearch5:<artists> <song>"
```

From the returned entries, in rank order:

- When the song's Spotify length is known (greater than 0): consider only entries that report a
  `duration`. Pick the one whose length is closest to the Spotify length; a tie goes to the higher
  rank. If the closest is more than 30 seconds away, there is no match.
- When the Spotify length is unknown: take the first entry.
- Entries without an `id` are never considered. A search that exits cleanly with no entries or
  unreadable output means no match, and the song is skipped as "no close match on YouTube."
- A search that failed is not a missed match, so it is never worded as one. A search that exits
  non-zero and lists no video skips the song with yt-dlp's error summary, or "the search
  failed." when yt-dlp said nothing. A timeout (120 seconds) skips it with "the search timed
  out." A non-zero exit that still lists videos is matched as usual, since one refused entry
  makes yt-dlp exit non-zero after listing the rest.

30 seconds is a constant named in the module. It is loose enough for an official video with a
short intro, and tight enough to refuse a live set or an hour-long loop.

### Downloading the video

```
yt-dlp --no-playlist -x --audio-format mp3 --audio-quality 0 --no-progress --no-warnings
       <player-client args> -o <scratch>/song.%(ext)s https://www.youtube.com/watch?v=<id>
```

The download lands in a temporary directory under `config.WORK_DIR`. The work dir and the library
are separate mounts, so the move into the stage is a copy: it lands as `<final name>.part` and is
then renamed with `os.replace` onto its final name, which is atomic inside the stage. A killed
worker leaves at most a `.part` file, which is never taken for a stored song and is deleted in
step 8. The timeout is 30 minutes.
Success is judged by the mp3 existing, not by the exit code. The player-client arguments and the
error summary come from `ingest`, made public there rather than copied.

## Desk

- A music link shows the pick control, as a playlist or podcast link does.
- The control reads "Pick songs", "All 12 songs", "3 of 12 songs", "No songs picked", and
  "Reading Spotify" while the preview loads. The picker panel says "song" and "Songs in this list",
  and falls back to the heading "Spotify" when there is no title.
- A pick of nothing shows "Pick at least one song from this list, or remove the row."
- The prepare payload for a music row is `{url, track_ids}`, built from the preview entries the
  same way a podcast builds `episode_ids`. A row nobody opened sends `track_ids: null`.
- When a preview carries `notice`, the Desk shows it as an info notification.
- The paste box placeholder gains a `https://open.spotify.com/playlist/...` line.
- `sourceLabel` names a `source: "music"` collection "Spotify playlist", "Spotify album" or
  "Spotify song" from its URL path. A Spotify podcast collection stays "Spotify episode".

## Documentation

- `docs/architecture/api.md`: the `track_ids` field, the music preview shape, the artist refusal,
  and the removal of the old music refusal.
- `docs/operations/user-guide.md`: a **Music** section. It says the list must be public, that a
  playlist stops at 100 songs, that the audio comes from a YouTube search and can be a different
  recording, that unmatched songs are skipped and listed, and that this is for music the user has
  the right to copy.
- `PRODUCT.md`: the operating context names Spotify music links as a source, and the capabilities
  list states the public-only, 100-song and matched-from-YouTube limits honestly.
- `README.md`: one clause where it lists what else can be pasted.
- `docs/architecture/overview.md` and the project layout in `docs/operations/development.md`: the
  new module.

## Tests

No test touches the network or runs yt-dlp. Spotify pages come from the existing in-memory
transport; yt-dlp comes from a stubbed `subprocess.run`.

Python (`tests/test_music.py`):

- Link shapes: playlist, album, track, with locale and embed segments; artist, show, episode and
  non-Spotify links are not music links.
- Listing: playlist, album and single track; no-break spaces; a repeated uri listed once; cover
  choice; unreadable page; each malformed field degrades or answers 502, never a crash.
- Preview: shape, `kind`, the explicit suffix, `available`, `notice` at exactly 100 songs and
  absent at 99.
- Match: closest length wins; a tie goes to rank; nothing within 30 seconds is no match; unknown
  Spotify length takes the first; entries without duration or id; a failed or timed-out search
  is skipped with its reason, not as no match.
- Import: picks by id in list order; unpicked takes available songs; a picked id that left the
  list; one unmatched and one failed song are skipped and named while the rest land; nothing
  stored fails; an existing file is not downloaded again; titles and `source` in the manifest; a
  retry after a song was added renames stored songs instead of downloading them again; a stored
  song no longer picked is deleted; two songs with one label both land; a killed move leaves only
  a `.part` file, which the retry deletes; no search or download runs while the lease is held.
- Routes: preview answers for a music link; artist is 400; unreadable is 502; prepare carries
  `track_ids` into the job; each refused combination; `prepare.run` routes a music link to the
  music import.

Browser (`tests/static/desk.test.mjs` and the DOM tests beside it): the music link is recognized,
the pick labels, the empty-pick error, the `track_ids` payload, the notice, and `sourceLabel`.

## Left to the builder

- The exact names of private helpers and the dataclass fields in `app/music.py`.
- How the shared Spotify reader is split between `podcast.py` and `music.py`, provided there is
  one fetch-and-parse and both tests' transports still reach it.
- CSS for the picker, if any is needed. None is expected.
