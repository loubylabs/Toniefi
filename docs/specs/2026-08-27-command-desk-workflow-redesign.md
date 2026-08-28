# TonieFi Command Desk Workflow Redesign

**Date:** 2026-08-27
**Status:** Approved for implementation
**Product record:** `PRODUCT.md`
**Approved direction:** Circulation Desk
**Approved composition:** `.impeccable/mocks/command-desk.png`

## Summary

TonieFi will become a batch-first preparation workspace for one household. A user can submit several audiobook links, let every source extract and Forge independently, review completed collections, and deliberately choose which Creative Tonies receive them. The interface will replace the current five-step wizard with a persistent application shell based on the approved Command Desk composition.

The visual system translates a contemporary library workroom into an operational web interface. A deep bottle-green service index anchors navigation. A cool utility-paper intake tray owns batch entry. A live work cart keeps background state visible. Full-color cover jackets make collections recognizable. Stamped labels communicate state without depending on color alone.

## Goals

- Accept up to 50 HTTP or HTTPS source URLs in one submission.
- Create one independent collection job per source.
- Run extraction and the default Forge sequence automatically.
- Normalize perceived loudness to −16 LUFS with a −1.5 dBTP ceiling.
- Preserve chapter markers, clean titles, and split oversized tracks by default.
- Keep successful sources moving when another source fails.
- Stop every prepared collection at review before any Tonie Cloud write.
- Make background progress, failures, retries, and review readiness visible across navigation.
- Give collection review, library management, Creative Tonie management, activity, and account settings clear homes.
- Work with keyboard, touch, narrow screens, reduced motion, and high zoom.

## Non-goals

- Multi-user or multi-household installations.
- Automatic Creative Tonie assignment or transfer.
- Tonie Cloud authentication changes, two-factor support, or an official API integration.
- A remote-access or hosted TonieFi service.
- Changing the 90-minute capacity, sequential packing rule, audio bitrate, or file ownership model.
- Persisting presentation preferences or adding a theme switcher.

## Product principles

1. Automation handles repeatable preparation. Human attention stays on review, assignment, and destructive actions.
2. Each collection keeps its own identity, progress, failure, and recovery path.
3. The next safe action remains visible.
4. TonieFi describes unsupported APIs, stored credentials, destructive writes, and errors plainly.
5. Local folders and MP3 files remain the durable source of truth.

## Information architecture

The application shell has six destinations:

1. **Desk:** batch intake and the live work cart.
2. **Review Shelf:** collections whose manifest stage is `forged`, plus focused collection review.
3. **Library:** every local collection, search, rescan, open, and delete.
4. **Creative Tonies:** remote Tonie contents and existing safe chapter management.
5. **Activity:** complete job history, progress, errors, and eligible retries.
6. **Settings:** account connection, credential source, connection test, credential replacement or removal, system tools, and product disclosures.

Desktop uses the persistent left service index from the approved comp. Mobile uses a bottom bar for Desk, Review Shelf, Library, and Creative Tonies. Activity and Settings live behind a clearly labeled More control. The current top tabs and stepper are removed.

## Desk

### Batch intake

The main intake accepts pasted text with one URL per line. The interface trims whitespace, removes exact duplicates, rejects non-HTTP schemes, preserves entry order, and shows each source as an editable row. The server repeats validation and enforces a maximum of 50 unique sources.

Shared defaults appear beneath the source list. On mobile, the count-aware Prepare action comes first so it remains in the initial viewport, followed by a compact defaults summary.

- Loudness: −16 LUFS with −1.5 dBTP ceiling.
- Clean source noise from titles.
- Preserve chapter markers.
- Split tracks that exceed usable Tonie capacity.
- No automatic trimming.

The primary action names the count, such as `Prepare 5 stories`. Invalid or duplicate rows keep the batch in the intake tray with inline explanations and no jobs are created. A valid submission creates one job per source and clears the submitted rows.

LibriVox search and local file upload remain available as secondary intake modes on Desk. A selected LibriVox book imports and then runs the same default Forge sequence. A local file collection uploads all chosen files, then runs the default Forge sequence. Both finish on Review Shelf.

### Live work cart

The right rail displays active and recent preparation work with cover art when available. Each row shows title or source, phase, progress message, chapter and duration facts when known, and its next action.

Visible phases are:

- Queued
- Extracting
- Forging
- Ready to review
- Failed

The jobs table remains unchanged. Phase is encoded into the existing `progress` field as a stable prefix and exposed by the API as a derived `phase` property. This avoids a SQLite migration. Existing jobs without a phase prefix continue to render using their kind and status.

A cart progress meter is determinate only when the server supplies a real percentage. Other active phases use an explicitly indeterminate treatment and a truthful text message. Reduced-motion mode keeps the phase visible without looping movement.

A failed preparation exposes its real error and a Retry action. Retrying creates a new job and preserves the failed attempt in Activity. Every intake persists a deterministic hidden stage identity before collection side effects. Retry resumes completed staged files or safely restarts from immutable input, then publishes exactly one complete final collection.

## Preparation API and worker

### Batch endpoint

`POST /api/prepare`

Request:

```json
{
  "sources": [
    {"url": "https://www.youtube.com/watch?v=example"}
  ],
  "options": {
    "use_chapters": true,
    "normalize": true,
    "clean_titles": true,
    "trim_head": 0,
    "trim_tail": 0,
    "split_oversized": true
  }
}
```

Response:

```json
{
  "jobs": [
    {"id": 42, "url": "https://www.youtube.com/watch?v=example"}
  ]
}
```

The endpoint returns `400` when there are no valid sources, more than 50 unique sources, or a source uses an unsupported scheme. Duplicate URLs are rejected explicitly instead of silently producing duplicate collections.

### Prepare job

The worker job kind is `prepare_url`. It performs these steps:

1. Persist a deterministic hidden collection stage identity in the job payload.
2. Import the source through `ingest.import_url` into that hidden stage.
3. Checkpoint extracted stage progress and durable extraction completion without exposing a visible collection.
4. Run Forge against a disposable copy of the immutable extracted stage.
5. Atomically publish the complete forged folder on the library filesystem.
6. Return the decorated forged collection as the job result.

URL, LibriVox, and upload preparation share this hidden staged-collection publication contract. Upload source staging defaults under `DATA_DIR`, outside the bounded `WORK_DIR` tmpfs, and retains its owned marker, active lease, heartbeat, 500-file limit, 20 GiB limit, and 24-hour retry window. Startup recovers ready publication stages and removes abandoned stages that no resumable job owns.

Progress callbacks write `extracting: <message>` and `forging: <message>`. The API hydrates these into `phase` and human-readable `progress` fields without changing stored job history.

The former `url` job kind and `POST /api/ingest/url` route are deleted with their browser caller and tests. `POST /api/probe` is also deleted because the batch workflow no longer has a separate look-before-download step. No alias or wrapper preserves either path.

### Retry endpoint

`POST /api/jobs/{job_id}/retry`

Only a failed job can be retried. The new job clones the original kind, label, and current payload. A valid hidden stage resumes without repeating completed file intake. A missing stage safely restarts from immutable source input. Publication identity prevents duplicate visible collections. The response contains the new job.

## Review Shelf and focused review

Review Shelf lists forged collections newest first. Each row shows cover, title, chapter count, duration, Tonies needed, and Forge state. Selecting a collection opens focused review without losing the application shell.

Focused review provides:

- Editable collection title.
- Cover art, source, uploader, duration, chapter count, and Forge state.
- Chapter playback through one persistent audio player.
- Inline chapter renaming.
- Pointer drag ordering plus keyboard Move up and Move down controls.
- Chapter removal with a confirmation naming the deleted local file.
- Sequential capacity plan with exact chapter groups and usable minutes.
- A clear `Choose Creative Tonies` action after the plan.

An extracted collection is never labeled ready and never exposes Creative Tonie assignment. Focused Review and Library show one `Finish preparation` action that enqueues the existing persisted Forge job. Queued and failed Forge states remain visible. The collection joins Review Shelf only after its manifest reaches `forged`.

Assignment reuses the existing safe push behavior. It refreshes Tonie data before showing available targets, distinguishes replace from append, and never sends until the user confirms the target and effect.

## Library

Library is the durable local collection view. It provides search by title, Rescan, Finish preparation for legacy extracted collections, open for review when forged, and delete. Delete confirmation states that the collection folder and its local audio files will be removed. The empty state links back to Desk.

The current collection list behavior moves into this screen. There is no second library implementation hidden inside Desk or Review Shelf.

## Creative Tonies

The current concurrency-safe Tonie chapter management remains authoritative. The redesigned screen preserves:

- Fresh remote reads before writes.
- Title-aware stale-write protection.
- Chapter rename, reorder, remove, and clear.
- Explicit irreversible-action confirmations.
- The guarantee that Tonie operations do not change the local library.

The presentation becomes a compact Tonie list with an expanded detail counter. Keyboard move controls accompany drag ordering. Saving state disables all competing edits until the remote response returns.

## Activity

Activity shows the 40 most recent jobs with kind, phase, status, progress, timestamp, source label, result link, error, and retry eligibility. Failed jobs remain historical records after retry. Selecting a successful preparation opens its collection review.

One application-level refresh loop updates jobs, history, collections, status, and review counts while any job is queued or running. Each resource publishes to subscribers as soon as it settles, so jobs and history continue updating while a collection lease delays collection refresh. Fulfilled slices remain cached, stale and error state stays resource-specific, polling slows when no job is active, and polling stops while the page is hidden.

## Settings and account management

Settings presents one account status surface:

- Configured, Connected, unconfigured, or connection failed.
- Credential source: environment, locally saved, or none.
- Configured username when available.
- Honest warning that locally saved credentials are plain text in SQLite.
- Honest warning that two-factor accounts are unsupported.
- Save or replace locally stored credentials.
- Test connection with an inline result and timestamp for the current session.
- Remove locally stored credentials after confirmation.

When environment credentials are active, local credential fields are disabled because saved values cannot override the environment. The UI explains where the active credentials come from. `DELETE /api/settings/credentials` removes only database values. It cannot remove environment variables.

Configured means a complete saved or environment credential pair is available. Connected is shown only after a successful connection test in the current browser session. The tested timestamp and an explicit failed-test state remain visible for that session. Credential reads, replacements, and deletions treat username and password as one atomic SQLite pair.

Settings also reports the 90-minute Tonie limit, usable headroom, library path, and required tool availability. Product affiliation and private-API disclosures remain visible.

## Visual system

### Direction contract

**THESIS:** TonieFi is a working circulation desk for stories. It refuses the generic dark dashboard and the linear setup wizard.
**OWN-WORLD:** Deep bottle-green bookcloth, cool utility paper, chartreuse action ink, periwinkle information ink, square status stamps, thin rules, full-color cover jackets, and compact service labels.
**STORY:** Add several sources, watch each move independently through preparation, review the ready jackets, and choose their Creative Tonies.
**FIRST VIEWPORT:** The service index occupies the left, batch intake owns the center, and the live work cart remains visible at right. The count-aware preparation action closes the intake tray.
**FORM:** Command Desk, selected from three Circulation Desk compositions. Seed key `17e3c753`.
**FINISH:** unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md

### Approved comp inventory

| Visible ingredient | Implementation medium | Commitment |
|---|---|---|
| Service index | Semantic navigation, CSS, inline SVG sprite | Persistent desktop rail; compact mobile bottom bar |
| Bookcloth field | CSS color and subtle repeating texture | Texture stays subordinate to labels and focus rings |
| Intake tray | Semantic form controls and list rows | Five or more sources remain scannable without nested cards |
| Intake-to-cart motion | CSS transform and opacity | One orchestrated submission motion; instant fallback for reduced motion |
| Live work cart | Semantic list with real API state | Active, failed, and ready states visible together |
| Cover jackets | Existing cover endpoint | Authored fallback with title initials when no cover exists |
| Status stamps | Text plus icon plus color | Square labels; never color alone |
| Forge summary | Definition list and edit disclosure | Exact defaults visible before submission |
| Account status | API-backed navigation status | No invented plan, domain, or connection claim |
| Bottom status strip | Real job and tool state only | Omit illustrative storage and sync values from the comp |

The generated book covers, URLs, counts, durations, storage values, and sync values in the comp are illustrative. Production renders only real API data and truthful empty states.

## Responsive behavior

- At 1200 pixels and wider, intake and work cart share the viewport as approved.
- From 760 to 1199 pixels, the work cart moves below intake while the service index remains compact.
- Below 760 pixels, the bottom navigation replaces the side rail. Intake rows stack, primary actions remain reachable, and all pointer drag actions gain explicit move buttons.
- Content remains usable at 200 percent browser zoom without horizontal page scrolling.
- Safe-area insets are respected for the mobile bottom bar.

## Accessibility

- Semantic landmarks, headings, lists, forms, buttons, and status regions.
- Visible focus for every interactive control.
- Skip link to the active workspace.
- `aria-current` for navigation and `aria-live` for job state and form results.
- Status conveyed by label, icon, and color.
- Minimum 44 by 44 pixel touch targets on narrow screens.
- Keyboard alternatives for every drag operation.
- Reduced-motion behavior for submission, progress, and navigation transitions.
- Persistent audio controls with an accessible track label.

## Error handling

- Batch validation keeps rejected sources editable and submits no ambiguous subset.
- Each background failure retains its source, last phase, real error, and retry action.
- Retrying never overwrites the historical failed job.
- Collection and Tonie writes redraw from server truth after failure.
- Network failures preserve the current view, mark remote figures stale, and offer an explicit retry.
- Empty states always name the next useful action.

## Testing and verification

Automated tests will cover:

- Batch URL validation, deduplication, source limit, and job creation.
- Independent jobs for partial batch failure.
- Extract-to-Forge chaining and slug checkpointing.
- Retry rules and Forge-only resume after extraction.
- Derived job phase without a schema migration.
- Credential removal and environment credential precedence.
- Existing Tonie concurrency and destructive-action protections.
- Route retirement for the removed single-URL endpoints.

Static and browser verification will cover:

- JavaScript syntax for every module.
- Desktop first viewport against the approved comp.
- Tablet and mobile navigation, stacking, safe areas, and touch targets.
- Keyboard navigation, focus order, move controls, dialogs, and audio controls.
- Empty, loading, queued, extracting, forging, failed, ready, disconnected, and stale states.
- Impeccable detector findings on changed UI targets.
- Final independent Impeccable review with desktop and mobile screenshots.

## Retirement and migration

This change deletes the current five-step wizard, its stepper, the wizard's manual Extract and Forge progression, the `url` worker kind, the `/api/ingest/url` route, the `/api/probe` route, and the single watched-job timer. Their callers, tests, copy, and documentation are migrated in the same branch. The existing persisted `/api/forge` route remains the single supported migration path for legacy extracted collections.

The SQLite schema does not change. Existing job rows and collection manifests remain readable. Existing extracted collections continue to appear in Library with Finish preparation, and existing forged collections appear on Review Shelf.

## Acceptance criteria

1. Submitting five valid URLs returns five job IDs and creates five independent preparation jobs.
2. Every successful preparation runs extraction and default Forge processing, then appears on Review Shelf.
3. One failed source does not block another source from reaching review.
4. A Forge failure can retry from its extracted slug without downloading again.
5. No Creative Tonie changes until the user reviews a collection and confirms a target.
6. Account status and credential source remain visible and truthful.
7. All existing Tonie chapter safety tests continue to pass.
8. Desktop and mobile implementations preserve the Command Desk hierarchy and Circulation Desk visual system.
9. The removed wizard and single-URL paths have no remaining callers, tests, styles, or documentation.
10. The full test suite, JavaScript syntax checks, detector, and final visual review complete before merge.
