import assert from "node:assert/strict";
import test from "node:test";

import { filterCollectionsByTitle } from "../../app/static/library.js";
import {
  buildPushBatchPayload,
  createAssignmentAttempt,
  forgedCollectionsNewestFirst,
  moveControlFocusKey,
  tonieCapacity,
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

test("tonieCapacity uses browser usable headroom for replace and append", () => {
  const tonie = { seconds_present: 4200, seconds_free: 1200 };

  assert.deepEqual(tonieCapacity(tonie, 1180, true, 5370), {
    availableSeconds: 5370,
    projectedSeconds: 1180,
    fits: true,
  });
  assert.deepEqual(tonieCapacity(tonie, 1180, false, 5370), {
    availableSeconds: 1170,
    projectedSeconds: 5380,
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

test("assignment attempt disables before confirmation and rejects repeated submit", async () => {
  let releaseConfirmation;
  let confirmationCount = 0;
  let postCount = 0;
  let posted;
  const pendingStates = [];
  const payload = { operation_key: "stable-operation", assignments: [{ files: ["one.mp3"] }] };
  const attempt = createAssignmentAttempt({
    payload,
    confirm: async () => {
      confirmationCount += 1;
      return new Promise((resolve) => { releaseConfirmation = resolve; });
    },
    request: async (...args) => {
      postCount += 1;
      posted = args;
      return { job_ids: [11] };
    },
    setPending: (pending) => pendingStates.push(pending),
  });

  const first = attempt.submit();
  const repeated = await attempt.submit();
  assert.equal(repeated, false);
  assert.deepEqual(pendingStates, [true]);
  assert.equal(confirmationCount, 1);
  assert.equal(postCount, 0);
  releaseConfirmation(true);
  assert.deepEqual(await first, { job_ids: [11] });
  assert.equal(postCount, 1);
  assert.equal(posted[0], "/api/push/batch");
  assert.equal(posted[1].method, "POST");
  assert.equal(posted[1].body, JSON.stringify(payload));
  assert.deepEqual(pendingStates, [true, false]);
});

test("assignment retry retains one payload and replaces failure with recovered receipt", async () => {
  const payload = { operation_key: "stable-operation", assignments: [{ files: ["one.mp3"] }] };
  const bodies = [];
  const states = [];
  const attempt = createAssignmentAttempt({
    payload,
    confirm: async () => true,
    request: async (_url, options) => {
      bodies.push(options.body);
      if (bodies.length === 1) throw new Error("response uncertain");
      return { operation_key: "stable-operation", job_ids: [11] };
    },
    onFailure: (error) => states.push(`failure:${error.message}`),
    onReceipt: (receipt) => states.push(`success:${receipt.job_ids.join(",")}`),
  });

  assert.equal(await attempt.submit(), null);
  assert.equal(attempt.payload.operation_key, "stable-operation");
  assert.deepEqual(await attempt.retry(), { operation_key: "stable-operation", job_ids: [11] });
  assert.deepEqual(bodies, [JSON.stringify(payload), JSON.stringify(payload)]);
  assert.deepEqual(states, ["failure:response uncertain", "success:11"]);
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
