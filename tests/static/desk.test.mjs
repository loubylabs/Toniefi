import assert from "node:assert/strict";
import test from "node:test";

import {
  buildForgePayload,
  buildPreparePayload,
  buildWorkCartItems,
  parseSourceLines,
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

test("buildForgePayload keeps the safe Forge defaults for secondary intake", () => {
  assert.deepEqual(buildForgePayload("the-secret-garden"), {
    slug: "the-secret-garden",
    normalize: true,
    clean_titles: true,
    trim_head: 0,
    trim_tail: 0,
    split_oversized: true,
  });
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
