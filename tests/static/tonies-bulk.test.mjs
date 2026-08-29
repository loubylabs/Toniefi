import assert from "node:assert/strict";
import test from "node:test";

import { buildTonieChapterPayload, chapterDrafts, survivingChapters } from "../../app/static/tonies.js";

const chapters = [
  { id: "c1", title: "One" },
  { id: "c2", title: "Two" },
  { id: "c3", title: "Three" },
];

test("survivingChapters drops every selected chapter in one pass", () => {
  assert.deepEqual(survivingChapters(chapters, new Set(["c1", "c3"])), [{ id: "c2", title: "Two" }]);
});

test("survivingChapters keeps order for the chapters that stay", () => {
  assert.deepEqual(
    survivingChapters(chapters, new Set(["c2"])).map((chapter) => chapter.id),
    ["c1", "c3"],
  );
});

test("survivingChapters with nothing selected changes nothing", () => {
  assert.deepEqual(survivingChapters(chapters, new Set()), chapters);
});

test("survivingChapters with everything selected clears the Tonie", () => {
  assert.deepEqual(survivingChapters(chapters, new Set(["c1", "c2", "c3"])), []);
});

test("a bulk removal saves one payload carrying the full base and the survivors", () => {
  const tonie = { chapters };
  const payload = buildTonieChapterPayload(tonie, survivingChapters(chapters, new Set(["c1", "c3"])));

  assert.deepEqual(payload.base.map((chapter) => chapter.id), ["c1", "c2", "c3"]);
  assert.deepEqual(payload.chapters.map((chapter) => chapter.id), ["c2"]);
});

test("chapterDrafts reads the title input, not whatever input comes first", () => {
  // The row gains a selection checkbox before the title field. A drafts helper
  // that takes the FIRST input would read "on" (a checked box's value) as the
  // chapter title and write it to the Tonie, where there is no undo.
  const row = {
    dataset: { tonieChapter: "c1" },
    querySelector: (selector) => {
      if (selector === "input") return { value: "on" };
      if (selector === "[data-tonie-title]") return { value: "One" };
      return null;
    },
  };
  const list = { querySelectorAll: () => [row] };

  assert.deepEqual(chapterDrafts(list), [{ id: "c1", title: "One" }]);
});
