# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is one person managing a single household's Creative Tonies from a self-hosted installation. They may prepare several audiobooks in one session and need long-running work to continue without holding the browser open.

## Product Purpose

TonieFi turns source audio into organized, Tonie-ready collections while keeping the finished library as ordinary folders and MP3 files on the user's own disk. Success means a user can add several audiobooks, let TonieFi prepare them independently, review the results, and deliberately choose which Creative Tonies receive them.

## Positioning

TonieFi combines a self-hosted, inspectable audio library with an end-to-end preparation and transfer workflow for Creative Tonies. The user's collection remains usable without TonieFi or the Tonie Cloud.

## Operating Context

- A user commonly starts with YouTube video or playlist links, LibriVox books, or local audio files.
- Each source becomes its own independent collection. A batch of five audiobook links produces five collections.
- URL imports run through extraction and the default Forge cleanup automatically, then stop in a review queue before any Creative Tonie is changed.
- Long-running extraction, Forge, and transfer work runs in background jobs and can survive the browser closing.
- Review includes cover art, chapter titles, order, playback, duration, and the planned split across one or more Creative Tonies.
- The myTonies account is configured once for the household and is only needed for listing, editing, and sending to Creative Tonies.

## Capabilities and Constraints

- A Creative Tonie has a 90-minute capacity, with configurable headroom used to prevent rounding-related rejection.
- TonieFi preserves source order when packing chapters across multiple Tonies.
- Default Forge processing normalizes perceived loudness to −16 LUFS with a −1.5 dBTP ceiling, cleans source-title noise, and splits oversized tracks. This is the strongest safe loudness target currently supported without clipping.
- Each collection progresses and fails independently so one bad source does not block the rest of a batch.
- Nothing is sent to a Creative Tonie without explicit user review and selection.
- myTonies uses a private, unsupported API. Two-factor accounts are not supported by the current login method.
- Credentials may come from environment variables or the local SQLite settings store. Credentials saved through the UI are stored as plain text and must be described honestly.
- Tonie chapter changes have no undo in the Tonie Cloud. Destructive actions require confirmation and concurrent external changes must never be overwritten silently.
- The app remains single-household for this redesign. Multi-user and multi-household management are outside scope.

## Brand Commitments

- The product name is TonieFi.
- Product language should be direct, calm, and understandable to a parent without hiding technical or data-safety limitations.
- Existing product terminology includes collections, chapters, Forge, Creative Tonies, review, and send.

## Evidence on Hand

- The current implementation and workflow live in `app/static/`, with API behavior in `app/main.py`.
- Real local collections provide covers, chapter lists, durations, and packing plans for verification.
- `README.md` documents current workflow behavior, storage guarantees, Forge processing, packing rules, and credential limitations.
- TonieFi has no official affiliation with tonies or Boxine and must not imply one.

## Product Principles

- Make the next safe action obvious, especially while several collections are moving through the pipeline.
- Automate repeatable preparation and reserve human attention for review, assignment, and destructive changes.
- Keep every collection independent, recoverable, and easy to locate after leaving the page.
- Be explicit about background state, failures, account connection, and irreversible Tonie Cloud changes.
- Preserve ownership through plain files and local storage.

## Accessibility & Inclusion

The web interface must remain fully keyboard operable, expose visible focus states, avoid color-only status communication, respect reduced-motion preferences, and remain usable on narrow mobile screens.
