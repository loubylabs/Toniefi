import assert from "node:assert/strict";
import test from "node:test";

import { createToniesScreen } from "../../app/static/tonies.js";
import { buildTonieNamePayload } from "../../app/static/tonies.js";
import { tonieJacket, tonieMonogram } from "../../app/static/shared.js";
import { flush, installDom } from "./mini-dom.mjs";

const IMAGE = "https://cdn.tonies.de/thumbnails/50000072.png";

function mount({ tonies, onPatch = null } = {}) {
  const dom = installDom();
  const controller = new AbortController();
  const patches = [];
  let current = tonies;
  const request = async (url, options = {}) => {
    if (url === "/api/tonies") return current;
    const target = /^\/api\/tonies\/([^/]+)\/([^/]+)$/.exec(url);
    if (target && options.method === "PATCH") {
      const [, householdId, tonieId] = target;
      const body = JSON.parse(options.body);
      patches.push({ householdId, tonieId, body });
      if (onPatch) return onPatch(body);
      const updated = { ...current.find((t) => t.id === tonieId), name: body.name.trim() };
      current = current.map((t) => (t.id === tonieId ? updated : t));
      return updated;
    }
    throw new Error(`unexpected request ${url} ${options.method || "GET"}`);
  };
  const teardown = createToniesScreen({ request })({
    workspace: dom.workspace,
    signal: controller.signal,
  });
  return { dom, patches, teardown, controller, get tonies() { return current; } };
}

function aTonie(overrides = {}) {
  return {
    id: "t1",
    householdId: "h1",
    householdName: "Home",
    name: "Creative Tonie",
    imageUrl: IMAGE,
    chapter_count: 0,
    chapters: [],
    time_free: "1h 30m",
    ...overrides,
  };
}

test("a Tonie with artwork renders its real picture", () => {
  const dom = installDom();
  const node = tonieJacket({ name: "Bedtime Bear", imageUrl: IMAGE });
  assert.equal(node.tagName.toLowerCase(), "img");
  assert.equal(node.getAttribute("src"), IMAGE);
  assert.equal(node.getAttribute("alt"), "");
  dom.restore();
});

test("a Tonie without artwork falls back to a monogram", () => {
  const dom = installDom();
  const node = tonieJacket({ name: "Bedtime Bear" });
  assert.equal(node.tagName.toLowerCase(), "span");
  assert.equal(node.textContent, "BB");
  assert.equal(node.getAttribute("aria-hidden"), "true");
  assert.equal(tonieMonogram(""), "CT");
  dom.restore();
});

test("a rename carries the name that was on screen as its precondition", () => {
  const payload = buildTonieNamePayload({ name: "Creative Tonie" }, "  Bedtime Bear  ");
  assert.deepEqual(payload, { base_name: "Creative Tonie", name: "  Bedtime Bear  " });
});

test("the Tonie row shows the figure instead of a generic mark", async () => {
  const harness = mount({ tonies: [aTonie()] });
  await flush();
  const image = harness.dom.workspace.querySelectorAll("img")
    .find((node) => node.getAttribute("src") === IMAGE);
  assert.ok(image, "the row should render the Tonie's own artwork");
  harness.teardown();
  harness.dom.restore();
});

test("renaming a Tonie sends only a name and updates the row", async () => {
  const harness = mount({ tonies: [aTonie()] });
  await flush();
  await harness.dom.workspace.querySelectorAll("button")
    .find((button) => button.className.includes("tonie-summary"))
    .dispatchEvent({ type: "click" });
  await flush();
  const field = harness.dom.workspace.querySelectorAll("input")
    .find((node) => node.className.includes("tonie-name-input"));
  assert.ok(field, "an expanded Tonie should offer its name");
  field.value = "Bedtime Bear";
  await field.dispatchEvent({ type: "change" });
  await flush();
  assert.equal(harness.patches.length, 1);
  assert.deepEqual(Object.keys(harness.patches[0].body).sort(), ["base_name", "name"]);
  assert.equal(harness.patches[0].body.name, "Bedtime Bear");
  harness.teardown();
  harness.dom.restore();
});

test("a blank name is refused in the browser and never reaches the network", async () => {
  const harness = mount({ tonies: [aTonie()] });
  await flush();
  await harness.dom.workspace.querySelectorAll("button")
    .find((button) => button.className.includes("tonie-summary"))
    .dispatchEvent({ type: "click" });
  await flush();
  const field = harness.dom.workspace.querySelectorAll("input")
    .find((node) => node.className.includes("tonie-name-input"));
  field.value = "   ";
  await field.dispatchEvent({ type: "change" });
  await flush();
  assert.equal(harness.patches.length, 0);
  assert.equal(field.value, "Creative Tonie");
  harness.teardown();
  harness.dom.restore();
});

test("a destructive dialog shows the figure it is about to change", async () => {
  const dom = installDom();
  const { showConfirmDialog } = await import("../../app/static/shared.js");
  const pending = showConfirmDialog({
    title: "Clear this Tonie?",
    message: "This cannot be undone.",
    confirmLabel: "Clear",
    destructive: true,
    subject: { imageUrl: IMAGE, name: "Bedtime Bear", detail: "13 chapters" },
  });
  const dialog = dom.document.querySelectorAll("dialog")[0];
  assert.ok(dialog.querySelectorAll("img").length, "the dialog should show the figure");
  assert.match(dialog.textContent, /Bedtime Bear/);
  assert.match(dialog.textContent, /13 chapters/);
  await dialog.querySelectorAll("button")[0].dispatchEvent({ type: "click" });
  assert.equal(await pending, false);
  dom.restore();
});

test("the clear-all confirmation names and shows its Tonie", async () => {
  const harness = mount({ tonies: [aTonie({ chapter_count: 2, chapters: [
    { id: "c1", title: "One", duration: "1m 00s" },
    { id: "c2", title: "Two", duration: "1m 00s" },
  ] })] });
  await flush();
  await harness.dom.workspace.querySelectorAll("button")
    .find((button) => button.className.includes("tonie-summary"))
    .dispatchEvent({ type: "click" });
  await flush();
  const clear = harness.dom.workspace.querySelectorAll("button")
    .find((button) => button.textContent.includes("Clear all chapters"));
  clear.dispatchEvent({ type: "click" });
  await flush();
  const dialog = harness.dom.document.querySelectorAll("dialog")[0];
  assert.ok(dialog.querySelectorAll("img").length, "a no-undo dialog must show the figure");
  await dialog.querySelectorAll("button")[0].dispatchEvent({ type: "click" });
  harness.teardown();
  harness.dom.restore();
});

function mountWithJobs(tonies, jobs) {
  const dom = installDom();
  const controller = new AbortController();
  const listeners = new Set();
  const refresh = {
    snapshot: { jobs, stale: [], errors: {} },
    request: () => {
      for (const listener of listeners) listener(refresh.snapshot);
      return Promise.resolve(refresh.snapshot);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
  const request = async (url) => {
    if (url === "/api/tonies") return tonies;
    throw new Error(`unexpected request ${url}`);
  };
  const teardown = createToniesScreen({ request, refresh })({
    workspace: dom.workspace,
    signal: controller.signal,
  });
  return { dom, teardown, refresh };
}

test("a running send appears on the Tonie it is sending to, and nowhere else", async () => {
  const target = aTonie({ id: "t1", name: "Bedtime Bear" });
  const other = aTonie({ id: "t2", name: "Morning Bear", imageUrl: "" });
  const harness = mountWithJobs([target, other], [{
    id: 7,
    kind: "push",
    status: "running",
    phase: "sending",
    progress: "Uploading 7/30: Whale Shark Rescue",
    progress_percent: 22.5,
    payload: { household_id: "h1", tonie_id: "t1" },
  }]);
  await flush();
  const panels = harness.dom.workspace.querySelectorAll(".tonie-send-panel");
  assert.equal(panels.length, 1, "only the target Tonie gets a panel");
  assert.match(panels[0].textContent, /Uploading 7\/30: Whale Shark Rescue/);
  assert.match(panels[0].textContent, /23%/);
  const meter = panels[0].querySelector(".work-cart-progress-track");
  assert.equal(meter.getAttribute("data-mode"), "determinate");
  assert.equal(meter.getAttribute("aria-valuenow"), "23");
  harness.teardown();
  harness.dom.restore();
});

test("a send with no measurable phase shows an indeterminate meter, not zero", async () => {
  const harness = mountWithJobs([aTonie()], [{
    id: 8,
    kind: "push",
    status: "running",
    phase: "sending",
    progress: "Signing in to myTonies",
    progress_percent: null,
    payload: { household_id: "h1", tonie_id: "t1" },
  }]);
  await flush();
  const meter = harness.dom.workspace.querySelector(".work-cart-progress-track");
  assert.equal(meter.getAttribute("data-mode"), "indeterminate");
  assert.equal(meter.getAttribute("aria-valuenow"), null);
  assert.ok(!harness.dom.workspace.querySelector(".tonie-send-percent"));
  harness.teardown();
  harness.dom.restore();
});
