import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createDeskScreen, forgeFoldOpen, workStripSummary } from "../../app/static/desk.js";

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
  };
}

class FakeObserver {
  static instances = [];

  constructor(callback) {
    this.callback = callback;
    this.targets = [];
    this.disconnected = false;
    FakeObserver.instances.push(this);
  }

  observe(target) { this.targets.push(target); }

  disconnect() { this.disconnected = true; }

  report(isIntersecting) { this.callback([{ isIntersecting }]); }
}

const job = (id, status, extra = {}) => ({
  id,
  kind: "prepare_url",
  status,
  label: `Story ${id}`,
  payload: { url: `https://example.test/${id}` },
  created_at: 1000,
  ...extra,
});

async function mount({ phone = true, coarse = false, jobs = [] } = {}) {
  const dom = installDom();
  const media = viewport(phone);
  const pointer = viewport(coarse);
  globalThis.window.matchMedia = (query) => {
    if (query.includes("max-width")) return media;
    if (query.includes("pointer: coarse")) return pointer;
    return viewport(false);
  };
  FakeObserver.instances.length = 0;
  globalThis.IntersectionObserver = FakeObserver;
  const controller = new AbortController();
  const listeners = new Set();
  const refresh = {
    snapshot: { status: {}, jobs, collections: [], stale: [], errors: {}, dismissals: {} },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    request: async () => refresh.snapshot,
  };
  createDeskScreen({
    request: async () => ({
      use_chapters: true, normalize: true, clean_titles: true, trim_head: 0, trim_tail: 0, split_oversized: true,
    }),
    refresh,
  })({ workspace: dom.workspace, navigate() {}, signal: controller.signal });
  // Forge defaults load asynchronously; Prepare waits for them.
  await new Promise((resolve) => setTimeout(resolve, 0));
  await flush();
  return {
    dom,
    media,
    refresh,
    controller,
    strip: dom.workspace.querySelector(".work-strip"),
    fold: dom.workspace.querySelector(".forge-fold"),
    observer: FakeObserver.instances[0],
    poll(next) {
      refresh.snapshot = { ...refresh.snapshot, jobs: next };
      for (const listener of listeners) listener(refresh.snapshot);
    },
    done() {
      controller.abort();
      delete globalThis.IntersectionObserver;
      dom.restore();
    },
  };
}

test("the Forge summary is closed on a phone and always open on a desktop", () => {
  assert.equal(forgeFoldOpen(true, false), false);
  assert.equal(forgeFoldOpen(true, true), true);
  assert.equal(forgeFoldOpen(false, false), true, "a desktop has no fold to close");
});

test("opening the phone Forge fold is remembered across a turn of the screen", async () => {
  const screen = await mount();
  try {
    assert.equal(screen.fold.open, false, "closed by default on a phone");
    screen.fold.open = true;
    await screen.fold.dispatchEvent({ type: "toggle" });
    screen.media.resize(false);
    assert.equal(screen.fold.open, true, "a desktop shows it open");
    await screen.fold.dispatchEvent({ type: "toggle" });
    screen.media.resize(true);
    assert.equal(screen.fold.open, true, "the phone's own choice comes back");
    screen.fold.open = false;
    await screen.fold.dispatchEvent({ type: "toggle" });
    screen.media.resize(false);
    screen.media.resize(true);
    assert.equal(screen.fold.open, false);
  } finally {
    screen.done();
  }
});

test("the work strip counts only running and failed work", () => {
  const phase = (name) => ({ phase: name });
  assert.deepEqual(workStripSummary([]), { working: 0, failed: 0, label: "" });
  assert.deepEqual(
    workStripSummary([phase("queued"), phase("extracting"), phase("forging"), phase("failed"), phase("ready"), phase("sent")]),
    { working: 3, failed: 1, label: "3 working · 1 failed" },
  );
  assert.equal(workStripSummary([phase("sending")]).label, "1 working");
  assert.equal(workStripSummary([phase("failed"), phase("failed")]).label, "2 failed");
  assert.equal(workStripSummary([phase("ready"), phase("sent")]).label, "");
});

test("the work strip shows only on a phone, with live work, while the cart is off screen", async () => {
  const screen = await mount({ jobs: [job(1, "running", { phase: "extracting" }), job(2, "queued"), job(3, "failed", { retryable: true })] });
  try {
    assert.equal(screen.strip.hidden, false);
    assert.equal(screen.strip.textContent, "2 working · 1 failed");
    assert.equal(screen.strip.getAttribute("aria-label"), "2 working · 1 failed. Go to the work cart");
    assert.deepEqual(screen.observer.targets, [screen.dom.workspace.querySelector(".live-work-cart")]);

    screen.observer.report(true);
    assert.equal(screen.strip.hidden, true, "the cart is in view");
    screen.observer.report(false);
    assert.equal(screen.strip.hidden, false);

    screen.poll([job(1, "running", { phase: "extracting" })]);
    assert.equal(screen.strip.textContent, "1 working", "counts follow the poll");

    screen.poll([]);
    assert.equal(screen.strip.hidden, true, "no work, no strip");

    screen.poll([job(4, "queued")]);
    screen.media.resize(false);
    assert.equal(screen.strip.hidden, true, "a desktop never shows it");
  } finally {
    screen.done();
  }
});

test("the work strip stays away for finished work and disconnects with the screen", async () => {
  const done = { id: 5, kind: "push", status: "done", label: "Send", payload: { sources: [] }, created_at: 1 };
  const screen = await mount({ jobs: [done] });
  try {
    assert.equal(screen.strip.hidden, true);
    screen.controller.abort();
    assert.equal(screen.observer.disconnected, true);
  } finally {
    screen.done();
  }
});

test("tapping the work strip lands on the cart heading", async () => {
  const screen = await mount({ jobs: [job(1, "queued")] });
  try {
    const cart = screen.dom.workspace.querySelector(".live-work-cart");
    const scrolls = [];
    cart.scrollIntoView = (options) => scrolls.push(options);
    await screen.strip.click();
    assert.deepEqual(scrolls, [{ block: "start", behavior: "smooth" }]);
    assert.equal(screen.dom.document.activeElement.id, "work-cart-title");
  } finally {
    screen.done();
  }
});

async function addToTray(screen, lines) {
  screen.dom.document.getElementById("source-paste").value = lines.join("\n");
  await [...screen.dom.workspace.querySelectorAll("button")].find((button) => button.textContent.includes("Add to tray")).click();
  await flush();
}

test("Add to tray focuses Prepare under a coarse pointer, so no keyboard rises", async () => {
  const screen = await mount({ coarse: true });
  try {
    await addToTray(screen, ["https://example.test/one", "https://example.test/two"]);
    const prepare = screen.dom.workspace.querySelector(".desk-prepare-button");
    assert.equal(prepare.disabled, false);
    assert.equal(screen.dom.document.activeElement, prepare);
  } finally {
    screen.done();
  }
});

test("Add to tray under a coarse pointer never lands in a URL field, even when Prepare must wait", async () => {
  const screen = await mount({ coarse: true });
  try {
    await addToTray(screen, ["https://example.test/one", "not a url"]);
    assert.equal(screen.dom.workspace.querySelector(".desk-prepare-button").disabled, true);
    assert.notEqual(screen.dom.document.activeElement?.tagName, "INPUT");
  } finally {
    screen.done();
  }
});

test("Add to tray keeps focus on the first new row with a mouse or keyboard", async () => {
  const screen = await mount({ coarse: false });
  try {
    await addToTray(screen, ["https://example.test/one", "https://example.test/two"]);
    const [first] = screen.dom.workspace.querySelector(".source-row-list").querySelectorAll("input");
    assert.equal(screen.dom.document.activeElement.getAttribute("data-focus-key"), "source-1-input");
    assert.equal(first.getAttribute("aria-label"), "Source URL 1");
  } finally {
    screen.done();
  }
});

test("the phone source tray holds a row on one line and shows more than a row and a half", () => {
  const css = readFileSync(new URL("../../app/static/style.css", import.meta.url), "utf8");
  const phone = css.slice(css.indexOf("@media (max-width: 759.98px)"));
  assert.match(phone, /\.source-row-list \{\s*max-block-size: 16rem;/);
  assert.match(phone, /\.source-row \{[^}]*grid-template-columns: minmax\(0, 1fr\) auto;/);
  assert.match(phone, /\.source-row-field input \{\s*min-height: 2\.75rem;\s*font-size: 1rem;/);
});
