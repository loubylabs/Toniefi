import assert from "node:assert/strict";
import test from "node:test";

import { filterCollectionsByTitle } from "../../app/static/library.js";
import {
  buildPushPayload,
  forgedCollectionsNewestFirst,
  moveControlFocusKey,
  tonieCapacity,
} from "../../app/static/review.js";
import { moveItem } from "../../app/static/shared.js";

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

test("buildPushPayload sends one reviewed capacity group to its selected target", () => {
  assert.deepEqual(buildPushPayload("wind-in-the-willows", 2, {
    householdId: "household-1",
    id: "tonie-3",
  }, false), {
    slug: "wind-in-the-willows",
    household_id: "household-1",
    tonie_id: "tonie-3",
    group_index: 2,
    replace: false,
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
