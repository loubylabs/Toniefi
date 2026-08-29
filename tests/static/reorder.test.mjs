import assert from "node:assert/strict";
import test from "node:test";

import { filterCollectionsByTitle } from "../../app/static/library.js";
import {
  forgedCollectionsNewestFirst,
  moveControlFocusKey,
} from "../../app/static/review.js";
import { createMutationController, moveItem, snapshotRefreshOutcome } from "../../app/static/shared.js";
import { rescanCollections } from "../../app/static/library.js";

test("moveItem moves an item one position toward the start", () => {
  assert.deepEqual(moveItem(["chapter-a", "chapter-b", "chapter-c"], 1, -1), [
    "chapter-b",
    "chapter-a",
    "chapter-c",
  ]);
});

test("moveItem moves an item one position toward the end", () => {
  assert.deepEqual(moveItem(["chapter-a", "chapter-b", "chapter-c"], 1, 1), [
    "chapter-a",
    "chapter-c",
    "chapter-b",
  ]);
});

test("moveItem leaves either boundary unchanged", () => {
  const chapters = ["chapter-a", "chapter-b", "chapter-c"];

  assert.deepEqual(moveItem(chapters, 0, -1), chapters);
  assert.deepEqual(moveItem(chapters, 2, 1), chapters);
});

test("moveItem never mutates the input array", () => {
  const chapters = ["chapter-a", "chapter-b", "chapter-c"];

  moveItem(chapters, 1, -1);

  assert.deepEqual(chapters, ["chapter-a", "chapter-b", "chapter-c"]);
});

test("forgedCollectionsNewestFirst excludes unfinished collections and uses creation time", () => {
  const collections = [
    { slug: "older", stage: "forged", created_at: 100 },
    { slug: "unfinished", stage: "extracted", created_at: 300 },
    { slug: "newer", stage: "forged", created_at: 200 },
  ];

  assert.deepEqual(forgedCollectionsNewestFirst(collections).map((item) => item.slug), ["newer", "older"]);
  assert.deepEqual(collections.map((item) => item.slug), ["older", "unfinished", "newer"]);
});

test("mutation controller serializes controls and reloads truth after failure", async () => {
  const controls = [{ disabled: false }, { disabled: false }];
  const root = { querySelectorAll: () => controls, setAttribute() {}, removeAttribute() {} };
  let release;
  let reloads = 0;
  const controller = createMutationController({ root, reload: async () => { reloads += 1; } });
  const first = controller.run(async () => new Promise((_, reject) => { release = reject; }));
  assert.deepEqual(controls.map((control) => control.disabled), [true, true]);
  assert.equal(await controller.run(async () => "competing"), false);
  release(new Error("save failed"));
  await assert.rejects(first, /save failed/);
  assert.equal(reloads, 1);
  assert.deepEqual(controls.map((control) => control.disabled), [false, false]);
});

test("mutation controller marks stale when mutation and reload both fail", async () => {
  const root = { querySelectorAll: () => [], setAttribute() {}, removeAttribute() {} };
  let staleError = null;
  const controller = createMutationController({
    root,
    reload: async () => { throw new Error("reload failed"); },
    onStale: (error) => { staleError = error; },
  });
  await assert.rejects(controller.run(async () => { throw new Error("save failed"); }), /save failed/);
  assert.match(staleError.message, /reload failed/);
});

test("snapshot refresh outcome reports partial collection failure instead of success", () => {
  assert.deepEqual(snapshotRefreshOutcome({ stale: [] }, "collections"), { stale: false, error: null });
  const error = new Error("disk unavailable");
  assert.deepEqual(snapshotRefreshOutcome({ stale: ["collections"], errors: { collections: error } }, "collections"), {
    stale: true,
    error,
  });
});

test("Library Rescan uses mutation serialization and rehydrates one snapshot", async () => {
  let releaseRescan;
  const requestCalls = [];
  const controls = [{ tagName: "BUTTON", disabled: false }, { tagName: "BUTTON", disabled: false }];
  const root = {
    querySelectorAll: (selector) => selector === "[data-collection-mutation]" ? controls : [],
    setAttribute() {},
    removeAttribute() {},
  };
  const refresh = {
    request: async () => ({ collections: [{ slug: "one" }], stale: [], errors: {} }),
  };
  const controller = createMutationController({ root, reload: refresh.request });
  const first = controller.run(() => rescanCollections({
    collections: [{ slug: "one" }],
    request: async (...args) => {
      requestCalls.push(args);
      return new Promise((resolve) => { releaseRescan = resolve; });
    },
    refresh,
  }));
  assert.deepEqual(controls.map((control) => control.disabled), [true, true]);
  assert.equal(await controller.run(async () => "delete"), false);
  releaseRescan({ slug: "one" });
  const result = await first;
  assert.equal(result.failures.length, 0);
  assert.deepEqual(result.snapshot.collections, [{ slug: "one" }]);
  assert.equal(requestCalls.length, 1);
  assert.deepEqual(controls.map((control) => control.disabled), [false, false]);
});

test("moveControlFocusKey chooses an enabled control when a chapter reaches a boundary", () => {
  assert.equal(moveControlFocusKey("chapter-a.mp3", 0, 4, -1), "chapter-chapter-a.mp3-down");
  assert.equal(moveControlFocusKey("chapter-d.mp3", 3, 4, 1), "chapter-chapter-d.mp3-up");
  assert.equal(moveControlFocusKey("chapter-b.mp3", 1, 4, -1), "chapter-chapter-b.mp3-up");
});

test("filterCollectionsByTitle searches titles case-insensitively without changing order", () => {
  const collections = [
    { slug: "wind", title: "The Wind in the Willows" },
    { slug: "garden", title: "The Secret Garden" },
    { slug: "island", title: "Treasure Island" },
  ];

  assert.deepEqual(filterCollectionsByTitle(collections, "  THE  ").map((item) => item.slug), ["wind", "garden"]);
  assert.deepEqual(filterCollectionsByTitle(collections, "").map((item) => item.slug), ["wind", "garden", "island"]);
});
