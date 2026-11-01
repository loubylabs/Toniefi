import assert from "node:assert/strict";
import test from "node:test";

import { createCollectionDetail } from "../../app/static/collection.js";

import { flush, installDom } from "./mini-dom.mjs";

function viewport(matches) {
  const listeners = new Set();
  return {
    matches,
    addEventListener: (_, listener) => listeners.add(listener),
    removeEventListener: (_, listener) => listeners.delete(listener),
    resize(next) {
      this.matches = next;
      for (const listener of listeners) listener({ matches: next });
    },
    get listening() { return listeners.size; },
  };
}

const collection = {
  slug: "story",
  title: "Story",
  stage: "forged",
  forge: { normalized: true },
  track_count: 2,
  total_duration: "20m",
  tracks: [
    { name: "one.mp3", title: "One", seconds: 600, duration: "10m" },
    { name: "two.mp3", title: "Two", seconds: 600, duration: "10m" },
  ],
  plan: [{ index: 1, seconds: 1200, duration: "20m", tracks: [{ title: "One", duration: "10m" }, { title: "Two", duration: "10m" }] }],
};

async function mount({ phone = true } = {}) {
  const dom = installDom();
  const media = viewport(phone);
  globalThis.window.matchMedia = () => media;
  const controller = new AbortController();
  const patches = [];
  const request = async (url, options = {}) => {
    if (options.method === "PATCH") {
      patches.push([url, JSON.parse(options.body)]);
      return {};
    }
    return collection;
  };
  const listeners = new Set();
  const refresh = {
    snapshot: { status: { usable_limit_seconds: 5370 }, jobs: [], collections: [], stale: [], errors: {} },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    request: async () => refresh.snapshot,
  };
  createCollectionDetail({
    workspace: dom.workspace,
    slug: collection.slug,
    request,
    refresh,
    player: { play() {} },
    signal: controller.signal,
  });
  await flush();
  const poll = async () => {
    for (const listener of listeners) listener(refresh.snapshot);
    await flush();
  };
  return { dom, media, patches, controller, poll };
}

const rowFor = (dom, name) => dom.workspace.querySelectorAll(".collection-chapter-row")
  .find((row) => row.getAttribute("data-track-name") === name);

test("a chapter's More stays open across a re-render and closes on its own button", async () => {
  const { dom, controller, poll } = await mount();
  try {
    const more = () => rowFor(dom, "one.mp3").querySelector(".chapter-more");
    assert.equal(rowFor(dom, "one.mp3").hasAttribute("data-more-open"), false);
    await more().click();
    assert.equal(rowFor(dom, "one.mp3").hasAttribute("data-more-open"), true);
    assert.equal(more().getAttribute("aria-expanded"), "true");
    assert.equal(rowFor(dom, "two.mp3").hasAttribute("data-more-open"), false);
    // A refresh poll rebuilds the page; the open row is drawn back in.
    await poll();
    assert.equal(rowFor(dom, "one.mp3").hasAttribute("data-more-open"), true);
    await more().click();
    assert.equal(rowFor(dom, "one.mp3").hasAttribute("data-more-open"), false);
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("capacity groups are closed details on a phone, open ones survive a re-render, a desktop has none", async () => {
  const { dom, media, controller } = await mount();
  try {
    let details = dom.workspace.querySelectorAll(".capacity-group-details");
    assert.equal(details.length, 1);
    assert.equal(details[0].hasAttribute("open"), false);
    assert.match(details[0].querySelector("summary").textContent, /Tonie 1 · 20m · 2 chapters/);
    details[0].open = true;
    await details[0].dispatchEvent({ type: "toggle" });
    media.resize(true);
    details = dom.workspace.querySelectorAll(".capacity-group-details");
    assert.equal(details[0].hasAttribute("open"), true);
    media.resize(false);
    assert.equal(dom.workspace.querySelectorAll(".capacity-group-details").length, 0);
    assert.match(dom.workspace.textContent, /Creative Tonie 1/);
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("Rename opens the field, Enter saves, Escape restores, and nothing else opens it", async () => {
  const { dom, patches, controller } = await mount();
  try {
    const row = () => rowFor(dom, "one.mp3");
    assert.equal(row().hasAttribute("data-renaming"), false);
    await row().querySelector(".chapter-rename").click();
    assert.equal(row().hasAttribute("data-renaming"), true);
    let input = row().querySelector("input");
    assert.equal(dom.document.activeElement, input);
    input.value = "Typed";
    await input.dispatchEvent({ type: "keydown", key: "Escape" });
    assert.equal(input.value, "One");
    assert.equal(row().hasAttribute("data-renaming"), false);
    assert.equal(patches.length, 0);

    await row().querySelector(".chapter-rename").click();
    input = row().querySelector("input");
    input.value = "Renamed";
    await input.dispatchEvent({ type: "keydown", key: "Enter" });
    await flush();
    assert.deepEqual(patches, [["/api/collections/story/tracks/one.mp3", { title: "Renamed" }]]);
  } finally {
    controller.abort();
    dom.restore();
  }
});
