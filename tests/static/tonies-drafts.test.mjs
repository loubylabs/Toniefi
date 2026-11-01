import assert from "node:assert/strict";
import test from "node:test";

import { createToniesScreen } from "../../app/static/tonies.js";
import { flush, installDom } from "./mini-dom.mjs";

const chapters = ["One", "Two", "Three"].map((title, i) => ({ id: `c${i + 1}`, title, duration: "5m 00s" }));
const aTonie = () => ({
  id: "t1", householdId: "h1", householdName: "Home", name: "Bear",
  chapter_count: 3, time_free: "10m 00s", chapters: chapters.map((chapter) => ({ ...chapter })),
});

async function mount({ phone = true } = {}) {
  const dom = installDom();
  globalThis.window.matchMedia = () => ({ matches: phone });
  const controller = new AbortController();
  const writes = [];
  const held = { release: null };
  const request = async (url, options = {}) => {
    if (url === "/api/tonies") return [aTonie()];
    if (url.endsWith("/versions")) return [];
    writes.push([options.method, JSON.parse(options.body)]);
    if (held.hold) await new Promise((resolve) => { held.release = resolve; });
    const body = JSON.parse(options.body);
    return { ...aTonie(), ...(body.name ? { name: body.name } : {}), ...(body.chapters ? { chapters: body.chapters.map((chapter) => ({ duration: "5m 00s", ...chapter })) } : {}) };
  };
  let publish = () => {};
  const refresh = {
    subscribe(listener) { publish = listener; return () => {}; },
    request: async () => {},
  };
  const cleanup = createToniesScreen({ request, refresh })({ workspace: dom.workspace, signal: controller.signal });
  await flush();
  await dom.workspace.querySelector(".tonie-summary").click();
  await flush();
  const s = {
    dom, writes, held,
    one: (selector) => dom.workspace.querySelector(selector),
    rows: () => dom.workspace.querySelectorAll(".tonie-chapter-row"),
    field: (i = 1) => s.rows()[i].querySelector("[data-tonie-title]"),
    active: () => dom.document.activeElement,
    focusKey: () => dom.document.activeElement?.getAttribute("data-focus-key") || "",
    poll: async (jobs = []) => { publish({ jobs, stale: [] }); await flush(); },
    async rename(i = 1) {
      await s.rows()[i].querySelector(".tonie-chapter-more").click();
      await s.rows()[i].querySelector(".tonie-chapter-rename").click();
      await flush();
      return s.field(i);
    },
    // A real leave: by the time blur fires the field is no longer active.
    async leave(input, relatedTarget) {
      dom.document.activeElement = null;
      await input.dispatchEvent({ type: "blur", relatedTarget });
      await flush();
    },
    stop() { cleanup(); controller.abort(); dom.restore(); },
  };
  return s;
}

for (const phone of [true, false]) {
  const width = phone ? "phone" : "desktop";

  test(`${width}: a poll carries a half-typed chapter title and saves nothing; leaving the field saves it once`, async () => {
    const s = await mount({ phone });
    try {
      const input = phone ? await s.rename() : s.field();
      input.focus();
      input.value = "Half typ";
      await s.poll();
      const redrawn = s.field();
      assert.notEqual(redrawn, input, "the poll rebuilt the row");
      assert.equal(redrawn.value, "Half typ");
      assert.equal(s.active(), redrawn);
      assert.deepEqual(s.writes, [], "a poll writes nothing");
      if (phone) assert.equal(s.rows()[1].hasAttribute("data-renaming"), true);
      await s.leave(redrawn);
      assert.equal(s.writes.length, 1);
      assert.equal(s.writes[0][0], "PUT");
      assert.equal(s.writes[0][1].chapters[1].title, "Half typ");
    } finally { s.stop(); }
  });

  test(`${width}: a field removed by a poll may fire blur and change; neither saves`, async () => {
    const s = await mount({ phone });
    try {
      const input = phone ? await s.rename() : s.field();
      input.focus();
      input.value = "Half typ";
      // What Chromium does while a redraw removes a focused, edited field.
      const list = s.one(".tonie-list");
      const removal = list.replaceChildren;
      list.replaceChildren = function replaceChildren(...children) {
        s.dom.document.activeElement = null;
        input.dispatchEvent({ type: "change" });
        input.dispatchEvent({ type: "blur" });
        return removal.apply(this, children);
      };
      await s.poll();
      list.replaceChildren = removal;
      assert.deepEqual(s.writes, []);
      assert.equal(s.field().value, "Half typ");
      assert.equal(s.active(), s.field());
    } finally { s.stop(); }
  });

  test(`${width}: a poll carries a half-typed Tonie name; Enter saves it once`, async () => {
    const s = await mount({ phone });
    try {
      const input = s.one(".tonie-name-input");
      input.focus();
      input.value = "Half na";
      await s.poll();
      const redrawn = s.one(".tonie-name-input");
      assert.notEqual(redrawn, input);
      assert.equal(redrawn.value, "Half na");
      assert.equal(s.active(), redrawn);
      assert.deepEqual(s.writes, []);
      await redrawn.dispatchEvent({ type: "keydown", key: "Enter" });
      await flush();
      assert.deepEqual(s.writes, [["PATCH", { base_name: "Bear", name: "Half na" }]]);
    } finally { s.stop(); }
  });

  test(`${width}: Enter on a title carried through a poll saves it once`, async () => {
    const s = await mount({ phone });
    try {
      const input = phone ? await s.rename() : s.field();
      input.focus();
      input.value = "Half typ";
      await s.poll();
      await s.field().dispatchEvent({ type: "keydown", key: "Enter" });
      await flush();
      assert.equal(s.writes.length, 1);
      assert.equal(s.writes[0][1].chapters[1].title, "Half typ");
    } finally { s.stop(); }
  });

  test(`${width}: a window or app switch blurs the field without leaving it, and saves nothing`, async () => {
    const s = await mount({ phone });
    try {
      const input = phone ? await s.rename() : s.field();
      input.focus();
      input.value = "Half typ";
      await input.dispatchEvent({ type: "blur" });
      await flush();
      assert.deepEqual(s.writes, []);
      const name = s.one(".tonie-name-input");
      name.focus();
      name.value = "Half na";
      await name.dispatchEvent({ type: "blur" });
      await flush();
      assert.deepEqual(s.writes, []);
    } finally { s.stop(); }
  });

  test(`${width}: a send finishing re-reads the Tonies without losing the draft or closing the field`, async () => {
    const s = await mount({ phone });
    try {
      const sending = [{ kind: "push", status: "running", payload: { household_id: "h1", tonie_id: "t1" } }];
      await s.poll(sending);
      const input = phone ? await s.rename() : s.field();
      input.focus();
      input.value = "Half typ";
      await s.poll([]);
      assert.equal(s.field().value, "Half typ");
      assert.equal(s.active(), s.field());
      if (phone) assert.equal(s.rows()[1].hasAttribute("data-renaming"), true);
      assert.deepEqual(s.writes, []);
    } finally { s.stop(); }
  });
}

test("phone: Enter saves and focus goes to that row's More, not to Refresh", async () => {
  const s = await mount();
  try {
    const input = await s.rename();
    input.focus();
    input.value = "Renamed";
    await input.dispatchEvent({ type: "keydown", key: "Enter" });
    await flush();
    assert.equal(s.writes.length, 1);
    assert.equal(s.focusKey(), "tonie-c2-more");
    assert.equal(s.rows()[1].hasAttribute("data-renaming"), false);
  } finally { s.stop(); }
});

test("phone: Enter on an unchanged title closes the field onto More and writes nothing", async () => {
  const s = await mount();
  try {
    const input = await s.rename();
    input.focus();
    await input.dispatchEvent({ type: "keydown", key: "Enter" });
    await flush();
    assert.deepEqual(s.writes, []);
    assert.equal(s.focusKey(), "tonie-c2-more");
    assert.equal(s.rows()[1].hasAttribute("data-renaming"), false);
  } finally { s.stop(); }
});

test("desktop: Enter saves and focus stays in the title", async () => {
  const s = await mount({ phone: false });
  try {
    const input = s.field();
    input.focus();
    input.value = "Renamed";
    await input.dispatchEvent({ type: "keydown", key: "Enter" });
    await flush();
    assert.equal(s.writes.length, 1);
    assert.equal(s.focusKey(), "tonie-c2-title");
  } finally { s.stop(); }
});

test("desktop: tabbing out saves and focus lands where the Tab went", async () => {
  const s = await mount({ phone: false });
  try {
    const input = s.field();
    input.focus();
    input.value = "Renamed";
    const next = s.field(2);
    await s.leave(input, next);
    assert.equal(s.writes.length, 1);
    assert.equal(s.focusKey(), "tonie-c3-title");
  } finally { s.stop(); }
});

test("phone: a More tap mid-rename saves and still opens that More, with focus on it", async () => {
  const s = await mount();
  try {
    const input = await s.rename();
    input.focus();
    input.value = "Renamed";
    s.held.hold = true;
    const tapped = s.rows()[2].querySelector(".tonie-chapter-more");
    // The tap: mousedown blurs the field, then the click lands on the same node.
    await s.leave(input);
    assert.equal(s.writes.length, 1);
    assert.equal(s.rows()[2].querySelector(".tonie-chapter-more"), tapped, "a save in flight rebuilds nothing under the finger");
    assert.equal(tapped.disabled, false);
    await tapped.click();
    await flush();
    assert.equal(s.rows()[2].hasAttribute("data-more-open"), true);

    s.held.hold = false;
    s.held.release();
    await flush();
    assert.equal(s.rows()[2].hasAttribute("data-more-open"), true);
    assert.equal(s.focusKey(), "tonie-c3-more");
  } finally { s.stop(); }
});

test("a save in flight disables every write and keeps focus for when it lands", async () => {
  const s = await mount({ phone: false });
  try {
    const down = s.rows()[0].querySelectorAll(".tonie-move")[1];
    down.focus();
    s.held.hold = true;
    down.click();
    await flush();
    assert.equal(s.writes.length, 1);
    assert.equal(s.field(1).disabled, true);
    assert.equal(s.one(".tonie-summary").disabled, true);
    assert.equal(s.one(".tonie-list").querySelectorAll(".tonie-move")[1], down, "nothing rebuilt yet");
    s.held.hold = false;
    s.held.release();
    await flush();
    assert.equal(s.field(1).disabled, false);
    assert.equal(s.focusKey(), "tonie-c1-down");
  } finally { s.stop(); }
});

test("leaving a title unchanged writes nothing", async () => {
  const s = await mount();
  try {
    const input = await s.rename();
    await s.leave(input);
    assert.deepEqual(s.writes, []);
    assert.equal(s.rows()[1].hasAttribute("data-renaming"), false);
  } finally { s.stop(); }
});

test("leaving a Tonie name unchanged writes nothing, and a real leave saves it", async () => {
  const s = await mount();
  try {
    const name = s.one(".tonie-name-input");
    await s.leave(name);
    assert.deepEqual(s.writes, []);
    name.value = "Honey";
    await s.leave(name);
    assert.deepEqual(s.writes, [["PATCH", { base_name: "Bear", name: "Honey" }]]);
  } finally { s.stop(); }
});
