import assert from "node:assert/strict";
import test from "node:test";

import { createRouter } from "../../app/static/router.js";
import { createFocusedReview } from "../../app/static/review.js";
import { createPersistentAudioPlayer, setBusy, showConfirmDialog } from "../../app/static/shared.js";

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.attributes = new Map();
    this.childNodes = [];
    this.listeners = new Map();
    this.className = "";
    this.id = "";
    this.hidden = false;
    this.textContent = "";
    this.dataset = {};
    this.disabled = false;
    this.currentTime = 0;
    this.classList = {
      values: new Set(),
      add: (...names) => names.forEach((name) => this.classList.values.add(name)),
      remove: (...names) => names.forEach((name) => this.classList.values.delete(name)),
      toggle: (name, force) => force ? this.classList.values.add(name) : this.classList.values.delete(name),
      contains: (name) => this.classList.values.has(name),
    };
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "id") this.id = String(value);
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  toggleAttribute(name, force) {
    if (force) this.setAttribute(name, "");
    else this.removeAttribute(name);
  }

  append(...children) {
    this.childNodes.push(...children);
  }

  querySelectorAll(selector) {
    const tag = selector.toUpperCase();
    const found = [];
    const visit = (node) => {
      if (node.tagName === tag) found.push(node);
      node.childNodes.forEach(visit);
    };
    this.childNodes.forEach(visit);
    return found;
  }

  replaceChildren(...children) {
    this.childNodes = [...children];
  }

  addEventListener(name, listener) {
    this.listeners.set(name, listener);
  }

  removeEventListener(name) {
    this.listeners.delete(name);
  }

  focus() {}
  pause() { this.paused = true; }
  play() { this.paused = false; return Promise.resolve(); }
  load() { this.loaded = true; }
  showModal() {}
  close() {}
  remove() {}
}

test("persistent player creates one labeled audio element and dismisses it", async () => {
  const host = new FakeElement("section");
  host.hidden = true;
  const body = new FakeElement("body");
  const originalDocument = globalThis.document;
  globalThis.document = {
    body,
    createElement: (tagName) => new FakeElement(tagName),
    getElementById: () => null,
  };
  try {
    const player = createPersistentAudioPlayer({ host });
    player.play({ src: "/one.mp3", label: "Chapter One" });
    player.play({ src: "/two.mp3", label: "Chapter Two" });
    assert.equal(host.querySelectorAll("audio").length, 1);
    const audio = host.querySelectorAll("audio")[0];
    assert.equal(audio.getAttribute("aria-label"), "Chapter preview: Chapter Two");
    assert.equal(body.classList.contains("audio-player-visible"), true);
    player.dismiss();
    assert.equal(host.hidden, true);
    assert.equal(audio.getAttribute("src"), null);
    assert.equal(body.classList.contains("audio-player-visible"), false);
  } finally {
    globalThis.document = originalDocument;
  }
});

function textOf(node) {
  if (typeof node === "string") return node;
  return `${node.textContent}${node.childNodes.map(textOf).join("")}`;
}

test("setBusy emits explicit ARIA boolean values", () => {
  const host = new FakeElement();

  setBusy(host, true, "Loading collections");
  assert.equal(host.getAttribute("aria-busy"), "true");
  assert.equal(host.getAttribute("aria-label"), "Loading collections");

  setBusy(host, false);
  assert.equal(host.getAttribute("aria-busy"), "false");
  assert.equal(host.getAttribute("aria-label"), null);
});

test("confirmation dialog names itself from its heading", () => {
  const host = new FakeElement("div");
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement: (tagName) => new FakeElement(tagName),
    getElementById: (id) => id === "dialogHost" ? host : null,
  };

  try {
    showConfirmDialog({
      title: "Remove collection?",
      message: "The local folder and audio files will be removed.",
      confirmLabel: "Remove collection",
      destructive: true,
    });
    const dialog = host.childNodes[0];
    const heading = dialog.childNodes[0];
    assert.match(heading.id, /^confirmation-title-/);
    assert.equal(dialog.getAttribute("aria-labelledby"), heading.id);
  } finally {
    globalThis.document = originalDocument;
  }
});

test("router commits only the latest isolated async render", async () => {
  const originalDocument = globalThis.document;
  const originalWindow = globalThis.window;
  const originalCustomEvent = globalThis.CustomEvent;
  const workspace = new FakeElement("main");
  workspace.replaceChildren("initial");
  let resolveSlow;
  let slowSignal;
  const disposed = [];

  globalThis.document = {
    createElement: (tagName) => new FakeElement(tagName),
    getElementById: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {},
  };
  globalThis.window = {
    location: { pathname: "/slow", search: "", origin: "http://example.test" },
    history: {
      state: null,
      pushState(state, _title, path) {
        this.state = state;
        globalThis.window.location.pathname = path;
      },
      replaceState(state, _title, path) {
        this.state = state;
        globalThis.window.location.pathname = path;
      },
    },
    addEventListener() {},
    removeEventListener() {},
  };
  globalThis.CustomEvent = class {
    constructor(name, options) {
      this.name = name;
      this.detail = options.detail;
    }
  };

  const router = createRouter([
    { name: "slow", path: "/slow" },
    { name: "fast", path: "/fast" },
  ], { workspace });
  router.register("slow", async ({ workspace: target, signal }) => {
    slowSignal = signal;
    target.replaceChildren("slow start");
    await new Promise((resolve) => { resolveSlow = resolve; });
    target.replaceChildren("slow late");
    return () => disposed.push("slow");
  });
  router.register("fast", ({ workspace: target }) => {
    target.replaceChildren("fast");
    return () => disposed.push("fast");
  });

  try {
    router.start();
    await router.navigate("fast");
    assert.equal(textOf(workspace), "fast");
    assert.equal(slowSignal.aborted, true);

    resolveSlow();
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(textOf(workspace), "fast");
    assert.deepEqual(disposed, ["slow"]);
  } finally {
    router.destroy();
    globalThis.document = originalDocument;
    globalThis.window = originalWindow;
    globalThis.CustomEvent = originalCustomEvent;
  }
});

test("router retains and aborts a mounted route before cleanup", async () => {
  const originalDocument = globalThis.document;
  const originalWindow = globalThis.window;
  const originalCustomEvent = globalThis.CustomEvent;
  const workspace = new FakeElement("main");
  const order = [];
  let mountedSignal;

  globalThis.document = {
    createElement: (tagName) => new FakeElement(tagName),
    getElementById: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {},
  };
  globalThis.window = {
    location: { pathname: "/first", search: "", origin: "http://example.test" },
    history: {
      state: null,
      pushState(state, _title, path) {
        this.state = state;
        globalThis.window.location.pathname = path;
      },
      replaceState() {},
    },
    addEventListener() {},
    removeEventListener() {},
  };
  globalThis.CustomEvent = class {
    constructor(name, options) {
      this.name = name;
      this.detail = options.detail;
    }
  };

  const router = createRouter([
    { name: "first", path: "/first" },
    { name: "second", path: "/second" },
  ], { workspace });
  router.register("first", ({ signal }) => {
    mountedSignal = signal;
    signal.addEventListener("abort", () => order.push("abort"));
    return () => order.push(signal.aborted ? "cleanup-after-abort" : "cleanup-before-abort");
  });
  router.register("second", () => {});

  try {
    router.start();
    await Promise.resolve();
    assert.equal(mountedSignal.aborted, false);
    await router.navigate("second");
    assert.equal(mountedSignal.aborted, true);
    assert.deepEqual(order, ["abort", "cleanup-after-abort"]);
  } finally {
    router.destroy();
    globalThis.document = originalDocument;
    globalThis.window = originalWindow;
    globalThis.CustomEvent = originalCustomEvent;
  }
});

test("focused review renders loading synchronously and ignores hydration after cleanup", async () => {
  const originalDocument = globalThis.document;
  let resolveCollection;
  const workspace = new FakeElement("main");
  const controller = new AbortController();
  globalThis.document = {
    createElement: (tagName) => new FakeElement(tagName),
    getElementById: () => null,
    activeElement: null,
  };
  try {
    const cleanup = createFocusedReview({
      workspace,
      slug: "night-stories",
      request: async () => new Promise((resolve) => { resolveCollection = resolve; }),
      refresh: {
        snapshot: { status: { usable_limit_seconds: 5000 }, jobs: [] },
        request: async () => ({}),
        subscribe: () => () => {},
      },
      player: { play() {} },
      signal: controller.signal,
    });
    assert.equal(typeof cleanup, "function");
    assert.match(textOf(workspace), /Opening collection review/);
    cleanup();
    controller.abort();
    resolveCollection({ slug: "night-stories", title: "Late", tracks: [], plan: [] });
    await Promise.resolve();
    await Promise.resolve();
    assert.match(textOf(workspace), /Opening collection review/);
    assert.doesNotMatch(textOf(workspace), /Late/);
  } finally {
    globalThis.document = originalDocument;
  }
});
