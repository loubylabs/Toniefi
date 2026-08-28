import assert from "node:assert/strict";
import test from "node:test";

import { filterCollectionsByTitle } from "../../app/static/library.js";
import {
  buildPushBatchPayload,
  confirmPushBatch,
  forgedCollectionsNewestFirst,
  moveControlFocusKey,
  tonieCapacity,
} from "../../app/static/review.js";
import { createMutationController, moveItem, snapshotRefreshOutcome } from "../../app/static/shared.js";

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

test("tonieCapacity distinguishes replace capacity from append capacity", () => {
  const tonie = { seconds_present: 4200, seconds_free: 1200 };

  assert.deepEqual(tonieCapacity(tonie, 1500, true, 5400), {
    availableSeconds: 5400,
    projectedSeconds: 1500,
    fits: true,
  });
  assert.deepEqual(tonieCapacity(tonie, 1500, false, 5400), {
    availableSeconds: 1200,
    projectedSeconds: 5700,
    fits: false,
  });
});

test("buildPushBatchPayload carries exact reviewed files, remote state, and one operation key", () => {
  const collection = {
    slug: "wind-in-the-willows",
    manifest_fingerprint: "fingerprint-1",
  };
  const group = { tracks: [{ name: "001.mp3" }, { name: "002.mp3" }] };
  const tonie = {
    householdId: "household-1",
    id: "tonie-3",
    chapters: [{ id: "remote-1", title: "Existing" }],
  };

  assert.deepEqual(buildPushBatchPayload(collection, [{ group, tonie, replaceExisting: false }], "operation-1"), {
    operation_key: "operation-1",
    slug: "wind-in-the-willows",
    manifest_fingerprint: "fingerprint-1",
    assignments: [{
      household_id: "household-1",
      tonie_id: "tonie-3",
      files: ["001.mp3", "002.mp3"],
      replace: false,
      remote_chapters: [{ id: "remote-1", title: "Existing" }],
    }],
  });
});

test("confirmPushBatch never enqueues before final confirmation and posts one exact batch", async () => {
  const payload = { operation_key: "operation-1", assignments: [{ files: ["one.mp3"] }] };
  const calls = [];
  assert.equal(await confirmPushBatch({ confirm: async () => false, request: async (...args) => calls.push(args), payload }), null);
  assert.deepEqual(calls, []);

  const receipt = await confirmPushBatch({
    confirm: async () => true,
    request: async (...args) => {
      calls.push(args);
      return { job_ids: [7] };
    },
    payload,
  });
  assert.deepEqual(receipt, { job_ids: [7] });
  assert.deepEqual(calls, [["/api/push/batch", {
    method: "POST",
    body: JSON.stringify(payload),
  }]]);
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
