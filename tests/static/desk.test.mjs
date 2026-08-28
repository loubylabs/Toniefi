import assert from "node:assert/strict";
import test from "node:test";

import {
  buildLibrivoxPayload,
  buildPreparePayload,
  buildWorkCartItems,
  deskRefreshNotice,
  forgeDefinitionValues,
  forgeProfileStatus,
  moveSourceEntries,
  parseSourceLines,
  removeSourceEntry,
  submitUploadBatch,
  staleRefreshAnnouncement,
} from "../../app/static/desk.js";

test("parseSourceLines trims source URLs and preserves their entered order", () => {
  const parsed = parseSourceLines("  https://example.com/first  \n\nhttps://example.com/second\n https://example.com/third ");

  assert.deepEqual(parsed, {
    rows: [
      { value: "https://example.com/first", error: "" },
      { value: "https://example.com/second", error: "" },
      { value: "https://example.com/third", error: "" },
    ],
    uniqueCount: 3,
    valid: true,
  });
});

test("parseSourceLines reports each exact duplicate without hiding either row", () => {
  const parsed = parseSourceLines("https://example.com/story\nhttps://example.com/other\nhttps://example.com/story");

  assert.deepEqual(parsed, {
    rows: [
      { value: "https://example.com/story", error: "" },
      { value: "https://example.com/other", error: "" },
      { value: "https://example.com/story", error: "This source duplicates row 1." },
    ],
    uniqueCount: 2,
    valid: false,
  });
});

test("parseSourceLines rejects schemes other than HTTP and HTTPS", () => {
  const parsed = parseSourceLines("ftp://example.com/story\nfile:///tmp/story.mp3\nhttps://example.com/allowed");

  assert.deepEqual(parsed, {
    rows: [
      { value: "ftp://example.com/story", error: "Use an HTTP or HTTPS source URL." },
      { value: "file:///tmp/story.mp3", error: "Use an HTTP or HTTPS source URL." },
      { value: "https://example.com/allowed", error: "" },
    ],
    uniqueCount: 3,
    valid: false,
  });
});

test("parseSourceLines accepts 50 unique URLs and rejects the 51st", () => {
  const fifty = Array.from({ length: 50 }, (_, index) => `https://example.com/story-${index + 1}`).join("\n");
  const accepted = parseSourceLines(fifty);
  const rejected = parseSourceLines(`${fifty}\nhttps://example.com/story-51`);

  assert.equal(accepted.rows.length, 50);
  assert.equal(accepted.uniqueCount, 50);
  assert.equal(accepted.valid, true);
  assert.deepEqual(rejected.rows[50], {
    value: "https://example.com/story-51",
    error: "Only 50 unique source URLs can be prepared at once.",
  });
  assert.equal(rejected.uniqueCount, 51);
  assert.equal(rejected.valid, false);
});

test("buildPreparePayload creates the exact safe-default request for a valid batch", () => {
  const payload = buildPreparePayload(" https://example.com/one\nhttps://example.com/two ");

  assert.deepEqual(payload, {
    sources: [
      { url: "https://example.com/one" },
      { url: "https://example.com/two" },
    ],
    options: {
      use_chapters: true,
      normalize: true,
      clean_titles: true,
      trim_head: 0,
      trim_tail: 0,
      split_oversized: true,
    },
  });
});

test("buildPreparePayload refuses to build a partial request from invalid rows", () => {
  assert.throws(
    () => buildPreparePayload("https://example.com/valid\nftp://example.com/rejected"),
    { message: "Fix every source before preparing this batch." },
  );
});

test("buildLibrivoxPayload carries the selected Forge options", () => {
  assert.deepEqual(buildLibrivoxPayload("42", { normalize: false, trim_head: 1.5 }), {
    book_id: "42",
    options: {
      use_chapters: true,
      normalize: false,
      clean_titles: true,
      trim_head: 1.5,
      trim_tail: 0,
      split_oversized: true,
    },
  });
});

test("forgeDefinitionValues reflects every edited setting", () => {
  assert.deepEqual(forgeDefinitionValues({
    normalize: false,
    clean_titles: false,
    use_chapters: false,
    split_oversized: false,
    trim_head: 1.5,
    trim_tail: 2.5,
  }), {
    loudness: "Off",
    titleCleanup: "Off",
    chapters: "Ignored",
    oversized: "Kept whole",
    trimming: "1.5 sec start, 2.5 sec end",
  });
});

test("forgeProfileStatus reserves the safe badge for the complete default profile", () => {
  assert.deepEqual(forgeProfileStatus({}), { label: "Safe maximum", status: "success" });
  assert.deepEqual(
    forgeProfileStatus({ normalize: false }),
    { label: "Custom settings", status: "warning" },
  );
  assert.deepEqual(
    forgeProfileStatus({ trim_tail: 0.5 }),
    { label: "Custom settings", status: "warning" },
  );
});

test("source move and removal preserve stable identities for focus restoration", () => {
  const entries = [
    { id: "source-a", value: "https://example.com/a" },
    { id: "source-b", value: "https://example.com/b" },
    { id: "source-c", value: "https://example.com/c" },
  ];

  assert.deepEqual(moveSourceEntries(entries, "source-b", 1), [entries[0], entries[2], entries[1]]);
  assert.deepEqual(removeSourceEntry(entries, "source-b"), {
    entries: [entries[0], entries[2]],
    nextFocusId: "source-c",
  });
});

test("submitUploadBatch sends the whole selection to one persisted operation", async () => {
  const calls = [];
  const files = [
    new Blob(["one"], { type: "audio/mpeg" }),
    new Blob(["two"], { type: "audio/mpeg" }),
  ];
  files[0].name = "one.mp3";
  files[1].name = "two.mp3";

  await submitUploadBatch({
    files,
    title: "Family Stories",
    options: { normalize: false },
    request: async (...args) => calls.push(args) || { job_id: 51 },
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/api/uploads/prepare");
  assert.equal(calls[0][1].method, "POST");
  assert.deepEqual(calls[0][1].body.getAll("files").map((file) => file.name), ["one.mp3", "two.mp3"]);
  assert.equal(calls[0][1].body.get("title"), "Family Stories");
  assert.deepEqual(JSON.parse(calls[0][1].body.get("options")), {
    use_chapters: true,
    normalize: false,
    clean_titles: true,
    trim_head: 0,
    trim_tail: 0,
    split_oversized: true,
  });
});

test("deskRefreshNotice preserves cached work and exposes a retry for partial failure", () => {
  const error = new Error("Jobs could not refresh.");
  assert.deepEqual(deskRefreshNotice({ stale: ["jobs"], errors: { jobs: error } }), {
    stale: true,
    label: "Work cart may be out of date",
    message: "Jobs could not refresh. Showing the last available information.",
  });
  assert.deepEqual(deskRefreshNotice({ stale: [], errors: {} }), { stale: false, label: "", message: "" });
});

test("staleRefreshAnnouncement announces a stale transition once and then recovery", () => {
  const notice = {
    stale: true,
    label: "Work cart may be out of date",
    message: "Jobs could not refresh. Showing the last available information.",
  };
  const first = staleRefreshAnnouncement("", notice);
  const repeated = staleRefreshAnnouncement(first.key, notice);
  const recovered = staleRefreshAnnouncement(repeated.key, { stale: false, label: "", message: "" });

  assert.deepEqual(first, {
    key: "Work cart may be out of date|Jobs could not refresh. Showing the last available information.",
    message: "Work cart may be out of date. Jobs could not refresh. Showing the last available information.",
  });
  assert.deepEqual(repeated, { key: first.key, message: "" });
  assert.deepEqual(recovered, { key: "", message: "Work cart information is current again." });
});

test("buildWorkCartItems merges preparation jobs with collection facts and keeps real states", () => {
  const items = buildWorkCartItems([
    {
      id: 14,
      kind: "prepare_url",
      status: "failed",
      phase: "forging",
      label: "https://example.com/failed",
      progress: "Levelling 2/8",
      error: "Audio processing stopped.",
      retryable: true,
      payload: { url: "https://example.com/failed", slug: "failed-book" },
      result: {},
    },
    {
      id: 13,
      kind: "prepare_url",
      status: "done",
      phase: "ready",
      label: "https://example.com/ready",
      progress: "Finished",
      error: "",
      payload: { url: "https://example.com/ready" },
      result: { slug: "ready-book" },
    },
    {
      id: 12,
      kind: "push",
      status: "running",
      phase: "running",
      label: "Send elsewhere",
      progress: "Uploading",
      error: "",
      payload: {},
      result: {},
    },
  ], [
    { slug: "failed-book", title: "Failed Book", stage: "extracted", track_count: 8, total_duration: "1h 12m", cover: "cover.jpg" },
    { slug: "ready-book", title: "Ready Book", stage: "forged", track_count: 12, total_duration: "1h 45m" },
  ]);

  assert.deepEqual(items.map((item) => ({
    key: item.key,
    phase: item.phase,
    title: item.title,
    source: item.source,
    slug: item.slug,
    canRetry: item.canRetry,
    trackCount: item.trackCount,
    duration: item.duration,
    hasCover: item.hasCover,
  })), [
    {
      key: "job-14",
      phase: "failed",
      title: "Failed Book",
      source: "https://example.com/failed",
      slug: "failed-book",
      canRetry: true,
      trackCount: 8,
      duration: "1h 12m",
      hasCover: true,
    },
    {
      key: "job-13",
      phase: "ready",
      title: "Ready Book",
      source: "https://example.com/ready",
      slug: "ready-book",
      canRetry: false,
      trackCount: 12,
      duration: "1h 45m",
      hasCover: false,
    },
  ]);
});

test("buildWorkCartItems keeps every active job ahead of failed and completed work", () => {
  const job = (id, status, kind = "prepare_url") => ({
    id,
    kind,
    status,
    phase: status === "done" ? "ready" : status,
    label: `Story ${id}`,
    progress: "",
    error: status === "failed" ? "Stopped" : "",
    retryable: status === "failed",
    payload: { url: `https://example.com/${id}`, slug: `story-${id}` },
    result: status === "done" ? { slug: `story-${id}` } : {},
  });
  const jobs = [job(9, "done"), job(8, "done"), job(7, "failed"), job(6, "running"), job(5, "queued"), job(4, "running")];
  const collections = [9, 8].map((id) => ({ slug: `story-${id}`, title: `Story ${id}`, stage: "forged" }));

  assert.deepEqual(
    buildWorkCartItems(jobs, collections, 5).map((item) => item.jobId),
    [6, 5, 4, 7, 9],
  );
  assert.deepEqual(
    buildWorkCartItems([job(6, "running"), job(5, "queued"), job(4, "running")], [], 2).map((item) => item.jobId),
    [6, 5, 4],
  );
});

test("buildWorkCartItems honors the server retryable contract", () => {
  const [item] = buildWorkCartItems([{
    id: 31,
    kind: "prepare_url",
    status: "failed",
    phase: "failed",
    label: "Reviewed elsewhere",
    progress: "",
    error: "Return to Review",
    retryable: false,
    payload: { url: "https://example.com/reviewed" },
    result: {},
  }], []);

  assert.equal(item.canRetry, false);
});

test("buildWorkCartItems does not mark a legacy import-only LibriVox job ready", () => {
  const items = buildWorkCartItems([{
    id: 31,
    kind: "librivox",
    status: "done",
    phase: "done",
    label: "LibriVox import 31",
    progress: "Finished",
    error: "",
    payload: { book_id: "31", slug: "old-import" },
    result: { slug: "old-import" },
  }], [{ slug: "old-import", title: "Old Import", stage: "extracted" }]);

  assert.deepEqual(items, []);
});
