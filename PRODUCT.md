# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is one person managing a single household's Creative Tonies from a self-hosted installation. They may prepare several audiobooks in one session and need long-running work to continue without holding the browser open.

## Product Purpose

TonieFi turns source audio into organized, Tonie-ready collections while keeping the finished library as ordinary folders and MP3 files on the user's own disk. Success means a user can add several audiobooks, let TonieFi prepare them independently, and then deliberately choose which Creative Tonies receive them.

## Positioning

TonieFi combines a self-hosted, inspectable audio library with an end-to-end preparation and transfer workflow for Creative Tonies. The user's collection remains usable without TonieFi or the Tonie Cloud.

## Operating Context

- A user commonly starts with YouTube video or playlist links, LibriVox books, or local audio files.
- Each source becomes its own independent collection. A batch of five audiobook links produces five collections.
- URL imports run through extraction and the default Forge cleanup automatically, then appear in the Library, where the operator selects what to send.
- Long-running extraction, Forge, and transfer work runs in background jobs and can survive the browser closing.
- URL, LibriVox, upload, and Forge output stays hidden until one complete collection can be published atomically.
- The Library selection bar shows the send order, the capacity groups for the selection, each group's chapter membership, and the target Tonie with its household and free space.
- The myTonies account is configured once for the household and is only needed for listing, editing, and sending to Creative Tonies.

## Capabilities and Constraints

- A Creative Tonie has a 90-minute capacity, with configurable headroom used to prevent rounding-related rejection.
- TonieFi preserves source order when packing chapters across multiple Tonies.
- Default Forge processing normalizes perceived loudness to −16 LUFS with a −1.5 dBTP ceiling, cleans source-title noise, and splits oversized tracks. This is the strongest safe loudness target currently supported without clipping.
- Each collection progresses and fails independently so one bad source does not block the rest of a batch.
- Nothing is sent to a Creative Tonie without explicit user selection and confirmation.
- A Creative Tonie can be renamed from TonieFi. The rename writes only the name through the Tonie Cloud, so it changes nothing about the Tonie's chapters and triggers no re-transcoding, and it is visible in the myTonies app as well.
- A send reports a real percentage, measured by audio bytes uploaded rather than by chapter count. Phases with nothing to measure, such as signing in, report no percentage rather than an estimate.
- myTonies uses a private, unsupported API. Two-factor accounts are not supported by the current login method.
- Credentials may come from environment variables or the local SQLite settings store. Credentials saved through the UI are stored as plain text and must be described honestly.
- Forge defaults are stored locally as one validated profile, save automatically when edited, and prefill rather than enforce each preparation.
- A complete credential pair is Configured. Connected is reserved for a successful connection test in the current browser session.
- Legacy extracted collections use the persisted Forge job as their only migration path to the Library.
- Appending to a Creative Tonie is recoverable, because the added chapters can be removed afterwards, so it posts without a confirmation dialog and **Append to the back** is the preselected default for every capacity group. Replacing a Creative Tonie's chapters destroys its current cloud audio with no undo, so it always confirms and is never the default; the operator has to choose **Replace everything** deliberately.
- Tonie chapter changes have no undo in the Tonie Cloud. Destructive actions require confirmation and concurrent external changes must never be overwritten silently.
- On a Creative Tonie's own screen, several chapters can be ticked (individually or with Select all) and removed together: one whole-list save behind one confirmation, not one request per chapter, because a Tonie write has no undo to fall back on.
- The app remains single-household for this redesign. Multi-user and multi-household management are outside scope.

## Brand Commitments

- The product name is TonieFi.
- Product language should be direct, calm, and understandable to a parent without hiding technical or data-safety limitations.
- Existing product terminology includes collections, chapters, Forge, Creative Tonies, and send.

## Evidence on Hand

- The current implementation and workflow live in `app/static/`, with API behavior in `app/main.py`.
- Real local collections provide covers, chapter lists, durations, and packing plans for verification.
- `README.md` documents the current workflow and its safety limits. The detail behind it lives in `docs/architecture/` (runtime behaviour, storage guarantees, Forge processing, packing rules, HTTP API) and `docs/operations/` (configuration, credential limitations, troubleshooting, development).
- TonieFi has no official affiliation with tonies or Boxine and must not imply one.

## Product Principles

- Make the next safe action obvious, especially while several collections are moving through the pipeline.
- Automate repeatable preparation and reserve human attention for selection, assignment, and destructive changes.
- Keep every collection independent, recoverable, and easy to locate after leaving the page.
- Be explicit about background state, failures, account connection, and irreversible Tonie Cloud changes.
- Preserve ownership through plain files and local storage.

## Accessibility & Inclusion

The web interface must remain fully keyboard operable, expose visible focus states, avoid color-only status communication, respect reduced-motion preferences, and remain usable on narrow mobile screens.
