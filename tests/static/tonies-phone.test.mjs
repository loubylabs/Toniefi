import assert from "node:assert/strict";
import test from "node:test";

import { createToniesScreen } from "../../app/static/tonies.js";
import { buttonWithText, flush, installDom } from "./mini-dom.mjs";

const chapters = ["One", "Two", "Three"].map((title, i) => ({ id: `c${i + 1}`, title, duration: "5m 00s" }));
const tonie = (id, list) => ({
  id, householdId: "h1", householdName: "Home", name: `Tonie ${id}`,
  chapter_count: list.length, time_free: "10m 00s", chapters: list,
});

async function mount({ phone = true, tonies = [tonie("t1", chapters)] } = {}) {
  const dom = installDom();
  if (phone) globalThis.window.matchMedia = () => ({ matches: true });
  const controller = new AbortController();
  const writes = [];
  const request = async (url, options = {}) => {
    if (url === "/api/tonies") return tonies;
    if (url.endsWith("/versions")) return [];
    writes.push([options.method, url, options.body && JSON.parse(options.body)]);
    return tonies[0];
  };
  const cleanup = createToniesScreen({ request })({ workspace: dom.workspace, signal: controller.signal });
  await flush();
  const all = (sel) => dom.workspace.querySelectorAll(sel);
  const screen = {
    dom, writes, all,
    one: (sel) => dom.workspace.querySelector(sel),
    async open(i = 0) { await all(".tonie-summary")[i].click(); await flush(); },
    async tick(i) {
      const box = all(".tonie-chapter-select")[i];
      box.checked = !box.checked;
      await box.dispatchEvent({ type: "change" });
      await flush();
    },
    async toggle(details, open) {
      if (open) details.setAttribute("open", ""); else details.removeAttribute("open");
      await details.dispatchEvent({ type: "toggle" });
      await flush();
    },
    stop() { cleanup(); controller.abort(); dom.restore(); },
  };
  return screen;
}

test("rename and history are closed details on a phone, and stay open across a re-render", async () => {
  const s = await mount();
  try {
    await s.open();
    const folds = () => s.all("details");
    assert.equal(s.all(".tonie-fold").length, 2);
    assert.equal(folds().length, 2);
    assert.ok(folds().every((fold) => !fold.hasAttribute("open")));
    assert.equal(s.one(".tonie-history-note"), null, "closed history loads nothing");

    await s.toggle(folds()[0], true);
    await s.toggle(folds()[1], true);
    await s.tick(0); // a re-render
    assert.ok(folds().every((fold) => fold.hasAttribute("open")), "both folds redrawn open");
    assert.match(s.one(".tonie-history-note").textContent, /No versions recorded yet/);

    await s.toggle(folds()[1], false);
    await s.tick(1);
    assert.equal(folds()[0].hasAttribute("open"), true);
    assert.equal(folds()[1].hasAttribute("open"), false, "a closed history stays closed");
  } finally { s.stop(); }
});

test("a desktop draws rename and history flat, with no details", async () => {
  const s = await mount({ phone: false });
  try {
    await s.open();
    assert.equal(s.all("details").length, 0);
    assert.ok(s.one(".tonie-name-input"));
  } finally { s.stop(); }
});

test("the sticky bar shows Remove N and Clear ticks only while chapters are ticked", async () => {
  const s = await mount();
  try {
    await s.open();
    assert.equal(s.one(".tonie-selection-bar").hidden, true);
    await s.tick(0);
    await s.tick(2);
    const bar = s.one(".tonie-selection-bar");
    assert.equal(bar.hidden, false);
    assert.equal(bar.querySelector(".tonie-bar-remove").textContent, "Remove 2");
    await bar.querySelector(".tonie-clear-ticks").click();
    await flush();
    assert.equal(s.one(".tonie-selection-bar").hidden, true);
    assert.equal(s.all(".tonie-chapter-select").some((box) => box.checked), false);
    assert.equal(s.writes.length, 0, "clearing ticks writes nothing");
  } finally { s.stop(); }
});

test("the bar's Remove opens the irreversible dialog and sends the same whole-list save", async () => {
  const s = await mount();
  try {
    await s.open();
    await s.tick(0);
    await s.tick(2);
    const clicking = s.one(".tonie-bar-remove").click();
    await flush();
    const dialog = s.dom.document.getElementById("dialogHost").querySelector(".confirmation-dialog");
    assert.match(dialog.textContent, /Remove 2 chapters\?/);
    assert.match(dialog.textContent, /This cannot be undone/);
    assert.equal(s.writes.length, 0, "nothing is written before confirming");
    await buttonWithText(dialog, "Remove 2 chapters").click();
    await clicking;
    await flush();
    assert.equal(s.writes.length, 1);
    const [method, , body] = s.writes[0];
    assert.equal(method, "PUT");
    assert.deepEqual(body.base.map((c) => c.id), ["c1", "c2", "c3"]);
    assert.deepEqual(body.chapters.map((c) => c.id), ["c2"]);
  } finally { s.stop(); }
});

test("cancelling the bar's dialog writes nothing", async () => {
  const s = await mount();
  try {
    await s.open();
    await s.tick(1);
    const clicking = s.one(".tonie-bar-remove").click();
    await flush();
    const dialog = s.dom.document.getElementById("dialogHost").querySelector(".confirmation-dialog");
    await buttonWithText(dialog, "Cancel").click();
    await clicking;
    await flush();
    assert.equal(s.writes.length, 0);
  } finally { s.stop(); }
});

test("a chapter's More and Rename live in JS state and survive a re-render", async () => {
  const s = await mount();
  try {
    await s.open();
    const row = (i) => s.all(".tonie-chapter-row")[i];
    assert.equal(row(1).hasAttribute("data-more-open"), false);
    await row(1).querySelector(".tonie-chapter-more").click();
    await flush();
    assert.equal(row(1).hasAttribute("data-more-open"), true);
    assert.equal(row(1).querySelector(".tonie-chapter-more").getAttribute("aria-expanded"), "true");
    await s.tick(0);
    assert.equal(row(1).hasAttribute("data-more-open"), true, "More survives the redraw");

    await row(1).querySelector(".tonie-chapter-rename").click();
    await flush();
    assert.equal(row(1).hasAttribute("data-renaming"), true);
    assert.equal(row(1).hasAttribute("data-more-open"), false);
    await s.tick(2);
    assert.equal(row(1).hasAttribute("data-renaming"), true, "the open field survives the redraw");

    await row(1).querySelector("[data-tonie-title]").dispatchEvent({ type: "keydown", key: "Escape" });
    assert.equal(row(1).hasAttribute("data-renaming"), false);
    assert.equal(s.writes.length, 0);
  } finally { s.stop(); }
});

test("an empty Tonie name is still refused, and an empty Tonie still offers rename", async () => {
  const s = await mount({ tonies: [tonie("t9", [])] });
  try {
    await s.open();
    assert.equal(s.all(".tonie-chapter-row").length, 0);
    const input = s.one(".tonie-name-input");
    assert.ok(input, "rename is offered with no chapters");
    input.value = "   ";
    await input.dispatchEvent({ type: "blur" });
    await flush();
    assert.equal(s.writes.length, 0);
    assert.equal(input.value, "Tonie t9", "the old name is put back");
  } finally { s.stop(); }
});
