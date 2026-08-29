import assert from "node:assert/strict";
import test from "node:test";

import { createSelectionState, selectableCollections } from "../../app/static/library.js";

const forged = (slug) => ({
  slug,
  stage: "forged",
  title: slug,
  manifest_fingerprint: `f-${slug}`,
  tracks: [{ name: `${slug}.mp3`, title: slug, seconds: 300 }],
});

test("selectableCollections offers only forged collections", () => {
  const collections = [forged("a"), { slug: "b", stage: "extracted", title: "b" }];

  assert.deepEqual(selectableCollections(collections, []).map((item) => item.slug), ["a"]);
});

test("selection keeps Library order, not ticking order", () => {
  const state = createSelectionState();
  const collections = [forged("a"), forged("b"), forged("c")];

  state.toggle("c");
  state.toggle("a");

  assert.deepEqual(state.ordered(collections).map((item) => item.slug), ["a", "c"]);
});

test("selection drops a slug that left the index", () => {
  const state = createSelectionState();
  state.toggle("a");
  state.toggle("gone");

  assert.deepEqual(state.ordered([forged("a")]).map((item) => item.slug), ["a"]);
});

test("toggling twice deselects", () => {
  const state = createSelectionState();
  state.toggle("a");
  state.toggle("a");

  assert.equal(state.has("a"), false);
  assert.deepEqual(state.ordered([forged("a")]), []);
});

test("clear empties the selection", () => {
  const state = createSelectionState();
  state.toggle("a");
  state.toggle("b");
  state.clear();

  assert.deepEqual(state.ordered([forged("a"), forged("b")]), []);
});
