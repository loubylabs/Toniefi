import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  buildLibrivoxPayload,
  buildPreparePayload,
  buildWorkCartItems,
  createLiveWorkCart,
  deskRefreshNotice,
  forgeDefinitionValues,
  forgeProfileStatus,
  moveSourceEntries,
  looksLikePlaylist,
  parseSourceLines,
  playlistPickLabel,
  removeSourceEntry,
  submitUploadBatch,
  staleRefreshAnnouncement,
  truthfulWorkProgress,
  uploadPolicyText,
} from "../../app/static/desk.js";

import { installDom } from "./mini-dom.mjs";

function relativeLuminance(hex) {
  const channels = hex.match(/[a-f0-9]{2}/gi).map((value) => Number.parseInt(value, 16) / 255);
  const linear = channels.map((value) => value <= 0.04045
    ? value / 12.92
    : ((value + 0.055) / 1.055) ** 2.4);
  return (0.2126 * linear[0]) + (0.7152 * linear[1]) + (0.0722 * linear[2]);
}

function contrastRatio(foreground, background) {
  const light = Math.max(relativeLuminance(foreground), relativeLuminance(background));
  const dark = Math.min(relativeLuminance(foreground), relativeLuminance(background));
  return (light + 0.05) / (dark + 0.05);
}

function cssToken(styles, name) {
  return styles.match(new RegExp(`--${name}:\\s*(#[a-fA-F0-9]{6})`))?.[1];
}

function simpleComputedDeclarations(styles, tagName, classes) {
  const classSet = new Set(classes);
  const computed = new Map();
  let order = 0;
  for (const match of styles.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const body = match[2];
    for (const branch of match[1].split(",")) {
      const selector = branch.trim();
      if (!selector || /[\s>:+~\[\]#@]/.test(selector)) continue;
      const requiredClasses = [...selector.matchAll(/\.([a-zA-Z0-9_-]+)/g)].map((item) => item[1]);
      const requiredTag = selector.replace(/\.[a-zA-Z0-9_-]+/g, "").toLowerCase();
      if (requiredTag && requiredTag !== tagName.toLowerCase()) continue;
      if (!requiredClasses.every((name) => classSet.has(name))) continue;
      const specificity = (requiredClasses.length * 10) + (requiredTag ? 1 : 0);
      for (const declaration of body.split(";")) {
        const separator = declaration.indexOf(":");
        if (separator < 0) continue;
        let property = declaration.slice(0, separator).trim();
        const value = declaration.slice(separator + 1).trim();
        if (property === "background" && /^var\(--[^)]+\)$/.test(value)) property = "background-color";
        if (!["display", "place-items", "background-color", "color", "text-align"].includes(property)) continue;
        const prior = computed.get(property);
        if (!prior || specificity > prior.specificity || (specificity === prior.specificity && order > prior.order)) {
          computed.set(property, { value, specificity, order });
        }
      }
      order += 1;
    }
  }
  return Object.fromEntries([...computed].map(([property, entry]) => [property, entry.value]));
}

function exactRuleDeclarations(styles, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const body = styles.match(new RegExp(`${escaped}\\s*\\{([^{}]*)\\}`))?.[1] || "";
  return Object.fromEntries(body.split(";").flatMap((declaration) => {
    const separator = declaration.indexOf(":");
    if (separator < 0) return [];
    return [[declaration.slice(0, separator).trim(), declaration.slice(separator + 1).trim()]];
  }));
}

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
      { url: "https://example.com/one", playlist_items: null },
      { url: "https://example.com/two", playlist_items: null },
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

test("populated source tray has a bounded internal scroll region", () => {
  const css = readFileSync(new URL("../../app/static/style.css", import.meta.url), "utf8");
  const tray = exactRuleDeclarations(css, ".source-row-list");

  assert.match(tray["max-block-size"], /^clamp\(/);
  assert.equal(tray["overflow-x"], "hidden");
  assert.equal(tray["overflow-y"], "auto");
  assert.equal(tray["overscroll-behavior"], "contain");
  assert.equal(tray["scrollbar-gutter"], "stable");
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
    {
      slug: "ready-book",
      title: "Ready Book",
      stage: "forged",
      url: "https://example.com/ready",
      track_count: 12,
      total_duration: "1h 45m",
    },
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
      // The running send now leads the cart. It used to be excluded by
      // DESK_JOB_KINDS, which is exactly why a transfer was invisible here.
      key: "job-12",
      phase: "sending",
      title: "Send elsewhere",
      source: "Send elsewhere",
      slug: "",
      canRetry: false,
      trackCount: 0,
      duration: "",
      hasCover: false,
    },
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
      key: "collection-ready-book",
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
    [6, 5, 4, 7, null],
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
    label: "Prepared elsewhere",
    progress: "",
    error: "Extraction stopped",
    retryable: false,
    payload: { url: "https://example.com/prepared" },
    result: {},
  }], []);

  assert.equal(item.canRetry, false);
});

test("forged collection truth replaces obsolete completed and failed Forge rows", () => {
  const jobs = [
    {
      id: 41,
      kind: "forge",
      status: "failed",
      phase: "failed",
      label: "Older Forge attempt",
      progress: "",
      error: "Worker stopped",
      retryable: false,
      payload: { slug: "night-story" },
      result: {},
    },
    {
      id: 42,
      kind: "forge",
      status: "done",
      phase: "ready",
      label: "Completed Forge attempt",
      progress: "Finished",
      error: "",
      retryable: false,
      payload: { slug: "night-story" },
      result: { slug: "night-story" },
    },
  ];
  const collections = [{
    slug: "night-story",
    title: "Night Story",
    stage: "forged",
    track_count: 4,
    total_duration: "42m 00s",
  }];

  assert.deepEqual(buildWorkCartItems(jobs, collections).map((item) => ({
    key: item.key,
    phase: item.phase,
    canRetry: item.canRetry,
  })), [{
    key: "collection-night-story",
    phase: "ready",
    canRetry: false,
  }]);
});

test("active work leads authoritative collection truth until it stops", () => {
  const [item] = buildWorkCartItems([{
    id: 43,
    kind: "forge",
    status: "running",
    phase: "forging",
    label: "Active Forge",
    progress: "Levelling 2/4",
    error: "",
    retryable: false,
    payload: { slug: "night-story" },
    result: {},
  }], [{ slug: "night-story", title: "Night Story", stage: "forged" }]);

  assert.equal(item.key, "job-43");
  assert.equal(item.phase, "forging");
});

test("retryable extracted Forge failure remains useful recovery work", () => {
  const [item] = buildWorkCartItems([{
    id: 44,
    kind: "forge",
    status: "failed",
    phase: "failed",
    label: "Extracted Forge",
    progress: "",
    error: "Audio processing stopped",
    retryable: true,
    payload: { slug: "unfinished-story" },
    result: {},
  }], [{ slug: "unfinished-story", title: "Unfinished Story", stage: "extracted" }]);

  assert.equal(item.key, "job-44");
  assert.equal(item.phase, "failed");
  assert.equal(item.canRetry, true);
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

test("Desk upload help is derived from server status", () => {
  assert.equal(uploadPolicyText(null), "Upload limits are loading from TonieFi.");
  assert.equal(uploadPolicyText({
    upload_max_files: 500,
    upload_max_human: "20 GiB",
    upload_stage_retention_seconds: 86400,
  }), "Up to 500 files and 20 GiB total become one collection. Failed uploads remain available for retry for 24 hours.");
});

test("work cart progress is determinate only when the server supplies a real value", () => {
  assert.deepEqual(truthfulWorkProgress({ progress_percent: 42.5 }), {
    mode: "determinate",
    percent: 42.5,
  });
  assert.deepEqual(truthfulWorkProgress({ progress: "Levelling chapter 2" }), {
    mode: "indeterminate",
    percent: null,
  });
  const css = readFileSync(new URL("../../app/static/style.css", import.meta.url), "utf8");
  assert.doesNotMatch(css, /work-cart-progress-fill[^}]*width:\s*(?:38|72)%/s);
});

test("queued and extracting stamps meet small-text contrast on paper surfaces", () => {
  const css = readFileSync(new URL("../../app/static/style.css", import.meta.url), "utf8");
  const info = cssToken(css, "info");
  const paper = cssToken(css, "paper");

  assert.ok(contrastRatio(info, "#ffffff") >= 4.5);
  assert.ok(contrastRatio(info, paper) >= 4.5);
});

test("collection fallback jackets compute to the shared bookcloth treatment", () => {
  const css = readFileSync(new URL("../../app/static/style.css", import.meta.url), "utf8");
  for (const componentClass of ["library-cover", "collection-detail-cover"]) {
    const style = simpleComputedDeclarations(css, "span", [componentClass, "collection-cover-fallback"]);
    assert.equal(style.display, "grid");
    assert.equal(style["place-items"], "center");
    assert.equal(style["background-color"], "var(--bookcloth)");
    assert.equal(style.color, "var(--action)");
    assert.equal(style["text-align"], "center");
  }
});

test("a finished work cart offers one Library action, not one per story", () => {
  const dom = installDom();
  try {
    const cart = createLiveWorkCart({ request: async () => ({}), requestRefresh: async () => {}, navigate: () => {} });
    dom.workspace.append(cart.host);
    cart.onRefresh({
      jobs: [
        { id: 1, kind: "prepare_url", status: "done", result: { slug: "a" }, payload: { url: "https://example.com/a" } },
        { id: 2, kind: "prepare_url", status: "done", result: { slug: "b" }, payload: { url: "https://example.com/b" } },
      ],
      collections: [
        { slug: "a", stage: "forged", title: "A" },
        { slug: "b", stage: "forged", title: "B" },
      ],
    });

    const libraryLinks = cart.host.querySelectorAll(".work-cart-library-link");
    assert.equal(libraryLinks.length, 1);
    assert.equal(libraryLinks[0].hidden, false);
    assert.match(libraryLinks[0].textContent, /Open Library to send 2 stories/);
  } finally {
    dom.restore();
  }
});

test("a link that names a playlist is offered for picking", () => {
  assert.equal(looksLikePlaylist("https://www.youtube.com/playlist?list=PL1"), true);
  assert.equal(looksLikePlaylist("https://www.youtube.com/watch?v=aaa&list=PL1"), true);
  assert.equal(looksLikePlaylist("https://www.youtube.com/watch?v=aaa"), false);
  assert.equal(looksLikePlaylist("not a url"), false);
});

test("the pick label says how much of a playlist is chosen", () => {
  assert.equal(playlistPickLabel({}), "Pick videos");
  assert.equal(playlistPickLabel({ total: 12, picked: null }), "Pick videos");
  assert.equal(playlistPickLabel({ total: 12, picked: [] }), "No videos picked");
  assert.equal(playlistPickLabel({ total: 12, picked: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] }), "All 12 videos");
  assert.equal(playlistPickLabel({ total: 12, picked: [1, 3, 5] }), "3 of 12 videos");
  assert.equal(playlistPickLabel({ total: 12, picked: [4] }), "1 of 12 videos");
});

test("prepare payload carries the picked playlist numbers", () => {
  const payload = buildPreparePayload([
    { value: "https://www.youtube.com/playlist?list=PL1", picked: [1, 3] },
    { value: "https://example.test/story" },
  ]);

  assert.deepEqual(payload.sources, [
    { url: "https://www.youtube.com/playlist?list=PL1", playlist_items: [1, 3] },
    { url: "https://example.test/story", playlist_items: null },
  ]);
});

test("a row nobody picked from sends no pick, not an empty one", () => {
  // The two used to share the empty list, and the download read that as "the
  // link speaks for itself". A bare playlist link ignores --no-playlist, so
  // unticking every entry brought every entry.
  const payload = buildPreparePayload([{ value: "https://www.youtube.com/playlist?list=PL1" }]);

  assert.equal(payload.sources[0].playlist_items, null);
});

test("a playlist row with every entry unticked blocks the whole batch", () => {
  const parsed = parseSourceLines([
    { value: "https://www.youtube.com/playlist?list=PL1", picked: [] },
    { value: "https://example.test/story" },
  ]);

  assert.equal(parsed.valid, false);
  assert.match(parsed.rows[0].error, /Pick at least one video/);
  assert.equal(parsed.rows[1].error, "");
  assert.throws(() => buildPreparePayload([
    { value: "https://www.youtube.com/playlist?list=PL1", picked: [] },
  ]), /Fix every source/);
});

test("an expanded playlist picker scrolls inside its own bounded region", () => {
  const css = readFileSync(new URL("../../app/static/style.css", import.meta.url), "utf8");
  const picker = exactRuleDeclarations(css, ".playlist-picker-list");

  assert.match(picker["max-block-size"], /^clamp\(/);
  assert.equal(picker["overflow-y"], "auto");
  assert.equal(picker["overscroll-behavior"], "contain");
});

test("a null percentage is not a measured zero", () => {
  // Number(null) is 0, so an unguarded check would draw a solid 0% bar for
  // every job the worker has not reported a figure for.
  assert.deepEqual(truthfulWorkProgress({ progress_percent: null }), {
    mode: "indeterminate",
    percent: null,
  });
  assert.deepEqual(truthfulWorkProgress({}), { mode: "indeterminate", percent: null });
  assert.deepEqual(truthfulWorkProgress({ progress_percent: 0 }), {
    mode: "determinate",
    percent: 0,
  });
});

test("a running send appears in the work cart with a real percentage", () => {
  const items = buildWorkCartItems([
    {
      id: 9,
      kind: "push",
      status: "running",
      phase: "sending",
      progress: "Uploading 7/30: Whale Shark Rescue",
      progress_percent: 22.5,
      payload: { household_id: "h1", tonie_id: "t1", sources: [{ slug: "sleepy-sophie" }] },
      label: "Send Sleepy Sophie to a Creative Tonie",
    },
  ], []);
  assert.equal(items.length, 1);
  assert.equal(items[0].phase, "sending");
  assert.equal(items[0].progressMode, "determinate");
  assert.equal(items[0].progressPercent, 22.5);
  assert.equal(items[0].progress, "Uploading 7/30: Whale Shark Rescue");
});

test("a send never hides its own collection's row", () => {
  const collection = {
    slug: "sleepy-sophie",
    title: "Sleepy Sophie",
    stage: "forged",
    track_count: 1,
    total_duration: "6m 19s",
  };
  const items = buildWorkCartItems([
    {
      id: 9,
      kind: "push",
      status: "running",
      phase: "sending",
      progress: "Uploading 1/1: Sleepy Sophie",
      progress_percent: 5,
      payload: { household_id: "h1", tonie_id: "t1", sources: [{ slug: "sleepy-sophie" }] },
      label: "Send Sleepy Sophie to a Creative Tonie",
    },
  ], [collection]);
  assert.deepEqual(items.map((item) => item.kind).sort(), ["collection", "push"]);
});
