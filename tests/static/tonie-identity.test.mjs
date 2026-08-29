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
