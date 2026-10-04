import assert from "node:assert/strict";
import test from "node:test";

import { chapterMarks } from "../../app/static/shared.js";
import { installDom } from "./mini-dom.mjs";

const day = (seconds) => new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(seconds * 1000));

test("a chapter with neither a release date nor a send shows no marks", () => {
  const dom = installDom();
  try {
    assert.deepEqual(chapterMarks({ name: "one.mp3" }), []);
  } finally {
    dom.restore();
  }
});

test("a chapter shows its release date, then the Tonie and day it was sent", () => {
  const dom = installDom();
  try {
    const [released, sent] = chapterMarks({
      name: "one.mp3",
      published: 1704088800,
      sent: { tonie: "Bedtime", at: 1790645961 },
    });
    assert.equal(released.textContent, `Released ${day(1704088800)}`);
    assert.equal(sent.textContent, `Sent · Bedtime · ${day(1790645961)}`);
    assert.equal(sent.getAttribute("data-status"), "sent");
  } finally {
    dom.restore();
  }
});
