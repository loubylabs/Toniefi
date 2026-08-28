import assert from "node:assert/strict";
import test from "node:test";

import { createDeskScreen, createLiveWorkCart } from "../../app/static/desk.js";
import { createLibraryScreen, forgePreparationState } from "../../app/static/library.js";
import { createFocusedReview } from "../../app/static/review.js";

function textOf(node) {
  if (typeof node === "string") return node;
  return `${node._textContent || ""}${node.childNodes.map(textOf).join("")}`;
}

function matches(node, selector) {
  if (selector.endsWith(":last-child")) {
    const base = selector.slice(0, -":last-child".length);
    return matches(node, base) && node.parentNode?.childNodes.at(-1) === node;
  }
  if (selector.startsWith(".")) return node.className.split(/\s+/).includes(selector.slice(1));
  if (selector.startsWith("#")) return node.id === selector.slice(1);
  if (selector.startsWith("[") && selector.endsWith("]")) {
    return node.hasAttribute(selector.slice(1, -1));
  }
  return node.tagName === selector.toUpperCase();
}

class MiniElement {
  constructor(tagName, ownerDocument) {
    this.tagName = tagName.toUpperCase();
    this.ownerDocument = ownerDocument;
    this.attributes = new Map();
    this.childNodes = [];
    this.parentNode = null;
    this.listeners = new Map();
    this.className = "";
    this.id = "";
    this._textContent = "";
    this.dataset = {};
    this.disabled = false;
    this.hidden = false;
    this.checked = false;
    this.selected = false;
    this.value = "";
    this.name = "";
    this.type = "";
    this.tabIndex = 0;
    this.draggable = false;
  }

  set textContent(value) {
    this._textContent = String(value ?? "");
    this.childNodes = [];
  }

  get textContent() {
    return textOf(this);
  }

  get classList() {
    return {
      add: (...names) => {
        const values = new Set(this.className.split(/\s+/).filter(Boolean));
        names.forEach((name) => values.add(name));
        this.className = [...values].join(" ");
      },
      remove: (...names) => {
        const removed = new Set(names);
        this.className = this.className.split(/\s+/).filter((name) => name && !removed.has(name)).join(" ");
      },
      contains: (name) => this.className.split(/\s+/).includes(name),
      toggle: (name, force) => {
        if (force) this.classList.add(name);
        else this.classList.remove(name);
      },
    };
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
    if (name === "value") this.value = String(value);
    if (name === "name") this.name = String(value);
    if (name === "type") this.type = String(value);
    if (name === "disabled") this.disabled = true;
    if (name === "checked") this.checked = true;
    if (name === "selected") this.selected = true;
    if (name === "draggable") this.draggable = String(value) === "true";
    if (name.startsWith("data-")) {
      const key = name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      this.dataset[key] = String(value);
    }
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  toggleAttribute(name, force) {
    if (force) this.setAttribute(name, "");
    else this.removeAttribute(name);
  }

  append(...children) {
    for (const child of children) {
      if (child && typeof child !== "string") child.parentNode = this;
      this.childNodes.push(child);
    }
  }

  prepend(...children) {
    for (const child of children) {
      if (child && typeof child !== "string") child.parentNode = this;
    }
    this.childNodes.unshift(...children);
  }

  replaceChildren(...children) {
    this.childNodes = [];
    this.append(...children);
  }

  remove() {
    if (!this.parentNode) return;
    this.parentNode.childNodes = this.parentNode.childNodes.filter((child) => child !== this);
    this.parentNode = null;
  }

  contains(target) {
    if (target === this) return true;
    return this.childNodes.some((child) => typeof child !== "string" && child.contains(target));
  }

  querySelectorAll(selector) {
    const selectors = selector.split(",").map((item) => item.trim());
    const found = [];
    const visit = (node) => {
      if (typeof node === "string") return;
      if (selectors.some((item) => matches(node, item))) found.push(node);
      node.childNodes.forEach(visit);
    };
    this.childNodes.forEach(visit);
    return found;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  addEventListener(name, listener) {
    const listeners = this.listeners.get(name) || [];
    listeners.push(listener);
    this.listeners.set(name, listeners);
  }

  removeEventListener(name, listener) {
    const listeners = this.listeners.get(name) || [];
    this.listeners.set(name, listeners.filter((item) => item !== listener));
  }

  async dispatchEvent(event) {
    event.target ||= this;
    event.currentTarget = this;
    event.preventDefault ||= () => { event.defaultPrevented = true; };
    const results = (this.listeners.get(event.type) || []).map((listener) => listener(event));
    await Promise.all(results);
    return !event.defaultPrevented;
  }

  click() {
    return this.dispatchEvent({ type: "click" });
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  showModal() {
    this.setAttribute("open", "");
  }

  close() {
    this.removeAttribute("open");
  }

  setCustomValidity() {}
  reportValidity() {}
}

class MiniDocument {
  constructor() {
    this.body = new MiniElement("body", this);
    this.activeElement = null;
    this.hidden = false;
  }

  createElement(tagName) {
    return new MiniElement(tagName, this);
  }

  getElementById(id) {
    if (this.body.id === id) return this.body;
    return this.body.querySelectorAll(`#${id}`)[0] || null;
  }

  querySelectorAll(selector) {
    return this.body.querySelectorAll(selector);
  }
}

function buttonWithText(root, text) {
  return root.querySelectorAll("button").find((button) => button.textContent.includes(text));
}

function installDom() {
  const originalDocument = globalThis.document;
  const originalWindow = globalThis.window;
  const document = new MiniDocument();
  globalThis.document = document;
  globalThis.window = { requestAnimationFrame: (callback) => callback() };
  const workspace = document.createElement("main");
  workspace.id = "workspace";
  const dialogs = document.createElement("div");
  dialogs.id = "dialogHost";
  document.body.append(workspace, dialogs);
  return {
    document,
    workspace,
    restore() {
      globalThis.document = originalDocument;
      globalThis.window = originalWindow;
    },
  };
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

test("Desk gives every accepted source URL textbox a distinct accessible name", async () => {
  const dom = installDom();
  const controller = new AbortController();
  const refresh = {
    snapshot: { status: {}, jobs: [], collections: [], stale: [], errors: {} },
    subscribe: () => () => {},
    request: async () => refresh.snapshot,
  };

  try {
    createDeskScreen({
      request: async () => ({}),
      refresh,
    })({
      workspace: dom.workspace,
      navigate() {},
      signal: controller.signal,
    });
    const paste = dom.document.getElementById("source-paste");
    paste.value = "https://example.test/one\nhttps://example.test/two";
    await buttonWithText(dom.workspace, "Add to tray").click();
    await flush();

    const inputs = dom.workspace.querySelector(".source-row-list").querySelectorAll("input");
    assert.deepEqual(inputs.map((input) => input.getAttribute("aria-label")), [
      "Source URL 1",
      "Source URL 2",
    ]);
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("Desk renders forged truth instead of an obsolete failed Forge card", () => {
  const dom = installDom();
  const controller = new AbortController();
  try {
    const cart = createLiveWorkCart({
      request: async () => ({}),
      requestRefresh: async () => ({}),
      navigate() {},
      signal: controller.signal,
    });
    dom.workspace.append(cart.host);
    cart.onRefresh({
      jobs: [
        {
          id: 41,
          kind: "forge",
          status: "failed",
          phase: "failed",
          label: "Older Forge attempt",
          progress: "",
          error: "Worker stopped",
          retryable: false,
          payload: { slug: "night-story" },
          result: {},
        },
        {
          id: 42,
          kind: "forge",
          status: "done",
          phase: "ready",
          label: "Successful Forge attempt",
          progress: "Finished",
          error: "",
          retryable: false,
          payload: { slug: "night-story" },
          result: { slug: "night-story" },
        },
      ],
      collections: [{
        slug: "night-story",
        title: "Night Story",
        stage: "forged",
        track_count: 4,
        total_duration: "42m 00s",
      }],
      stale: [],
      errors: {},
    });

    const rows = dom.workspace.querySelectorAll(".work-cart-row");
    assert.equal(rows.length, 1);
    assert.match(rows[0].textContent, /Ready to review/);
    assert.match(rows[0].textContent, /Review/);
    assert.doesNotMatch(rows[0].textContent, /Failed|Retry|Worker stopped/);
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("focused Review owns assignment pending, failure, and recovered receipt DOM states", async () => {
  const dom = installDom();
  const controller = new AbortController();
  const bodies = [];
  let resolveRetry;
  const collection = {
    slug: "night-story",
    title: "Night Story",
    stage: "forged",
    manifest_fingerprint: "f".repeat(64),
    track_count: 1,
    total_duration: "16m 40s",
    tonies_needed: 1,
    tracks: [{ name: "one.mp3", title: "One", seconds: 1000, duration: "16m 40s" }],
    plan: [{ index: 1, seconds: 1000, duration: "16m 40s", tracks: [{ name: "one.mp3", title: "One", duration: "16m 40s" }] }],
  };
  const tonies = [{
    householdId: "house-1",
    householdName: "Home",
    id: "tonie-1",
    name: "Fox",
    chapters: [{ id: "old-1", title: "Old" }],
    chapter_count: 1,
    seconds_present: 100,
    seconds_free: 5300,
    time_free: "1h 28m",
  }];
  const request = async (url, options = {}) => {
    if (url === "/api/collections/night-story") return collection;
    if (url === "/api/tonies") return tonies;
    if (url === "/api/push/batch") {
      bodies.push(options.body);
      if (bodies.length === 1) throw new Error("response uncertain");
      return new Promise((resolve) => { resolveRetry = resolve; });
    }
    throw new Error(`Unexpected request ${url}`);
  };
  const refresh = {
    snapshot: { status: { usable_limit_seconds: 5370, tonie_limit_seconds: 5400 } },
    request: async () => ({ collections: [collection], stale: [], errors: {} }),
    subscribe: () => () => {},
  };

  try {
    createFocusedReview({
      workspace: dom.workspace,
      slug: collection.slug,
      request,
      refresh,
      player: { play() {} },
      signal: controller.signal,
    });
    await flush();
    await buttonWithText(dom.workspace, "Choose Creative Tonies").click();
    await flush();
    const form = dom.workspace.querySelector(".assignment-form");
    assert.match(form.querySelector("select").childNodes[1].textContent, /1h 27m free/);
    form.querySelector("select").value = "house-1:tonie-1";
    const firstSubmit = form.dispatchEvent({ type: "submit" });
    await flush();
    const assignment = dom.workspace.querySelector(".assignment-panel");
    assert.equal(assignment.hasAttribute("data-assignment-pending"), true);
    assert.equal(assignment.querySelectorAll("input, select, button").every((control) => control.disabled), true);
    const repeatedSubmit = form.dispatchEvent({ type: "submit" });
    await flush();
    assert.equal(dom.document.getElementById("dialogHost").querySelectorAll("dialog").length, 1);
    await repeatedSubmit;
    await buttonWithText(dom.document.getElementById("dialogHost"), "Confirm").click();
    await firstSubmit;
    await flush();
    assert.match(assignment.textContent, /response uncertain/);
    assert.equal(bodies.length, 1);

    const retry = buttonWithText(assignment, "Retry confirmed batch");
    const retrying = retry.click();
    await flush();
    assert.equal(assignment.hasAttribute("data-assignment-pending"), true);
    assert.equal(retry.disabled, true);
    resolveRetry({ operation_key: "recovered", job_ids: [41] });
    await retrying;
    await flush();
    assert.deepEqual(bodies, [bodies[0], bodies[0]]);
    assert.doesNotMatch(assignment.textContent, /response uncertain/);
    assert.match(assignment.textContent, /1 send is queued/);
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("Library rerenders every mutation control disabled while Rescan is pending", async () => {
  const dom = installDom();
  const controller = new AbortController();
  const collection = {
    slug: "night-story",
    title: "Night Story",
    stage: "forged",
    track_count: 1,
    total_duration: "16m 40s",
    tonies_needed: 1,
  };
  const listeners = new Set();
  let releaseDetail;
  let refreshCalls = 0;
  const pendingSnapshots = [];
  const refresh = {
    snapshot: { collections: [collection] },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    async request() {
      refreshCalls += 1;
      const snapshot = { collections: [collection], stale: [], errors: {} };
      listeners.forEach((listener) => listener(snapshot));
      const root = dom.workspace.querySelector(".library-screen");
      if (root?.getAttribute("aria-busy") === "true") {
        pendingSnapshots.push(root.querySelectorAll("[data-collection-mutation]").map((control) => ({
          tag: control.tagName,
          disabled: control.disabled,
          ariaDisabled: control.getAttribute("aria-disabled"),
        })));
      }
      return snapshot;
    },
  };
  const request = async (url) => {
    if (url.includes("refresh=true")) return new Promise((resolve) => { releaseDetail = resolve; });
    throw new Error(`Unexpected request ${url}`);
  };

  try {
    const cleanup = createLibraryScreen({ request, refresh })({ workspace: dom.workspace, signal: controller.signal });
    await flush();
    const rescan = buttonWithText(dom.workspace, "Rescan");
    const rescanning = rescan.click();
    await flush();
    listeners.forEach((listener) => listener({ collections: [collection], stale: [], errors: {} }));
    const root = dom.workspace.querySelector(".library-screen");
    const rerendered = root.querySelectorAll("[data-collection-mutation]");
    assert.equal(rerendered.every((control) => control.tagName === "A" ? control.getAttribute("aria-disabled") === "true" : control.disabled), true);
    const search = dom.workspace.querySelector("input");
    search.value = "Night";
    await search.dispatchEvent({ type: "input" });
    assert.equal(root.querySelectorAll("[data-collection-mutation]").every((control) => control.tagName === "A" ? control.getAttribute("aria-disabled") === "true" : control.disabled), true);
    releaseDetail(collection);
    await rescanning;
    assert.equal(refreshCalls, 2);
    assert.equal(pendingSnapshots.length, 1);
    assert.equal(pendingSnapshots[0].every((control) => control.tag === "A" ? control.ariaDisabled === "true" : control.disabled), true);
    assert.equal(root.querySelectorAll("[data-collection-mutation]").every((control) => control.tagName === "A" ? control.getAttribute("aria-disabled") === "false" : !control.disabled), true);
    cleanup();
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("Forge preparation state keeps extracted collections out of review and reports job truth", () => {
  const collection = { slug: "legacy-story", stage: "extracted" };
  assert.deepEqual(forgePreparationState(collection, []), { state: "incomplete", error: "" });
  assert.deepEqual(forgePreparationState(collection, [{
    id: 4,
    kind: "forge",
    status: "queued",
    payload: { slug: "legacy-story" },
  }]), { state: "pending", error: "" });
  assert.deepEqual(forgePreparationState(collection, [{
    id: 5,
    kind: "forge",
    status: "failed",
    error: "ffmpeg stopped",
    payload: { slug: "legacy-story" },
  }]), { state: "failed", error: "ffmpeg stopped" });
  assert.deepEqual(forgePreparationState({ ...collection, stage: "forged" }, []), { state: "ready", error: "" });
});

test("Library gives extracted collections one Finish preparation action and no review action", async () => {
  const dom = installDom();
  const controller = new AbortController();
  const collection = {
    slug: "legacy-story",
    title: "Legacy Story",
    stage: "extracted",
    track_count: 2,
    total_duration: "20m",
    tonies_needed: 1,
  };
  const calls = [];
  const listeners = new Set();
  let snapshot = { collections: [collection], jobs: [], stale: [], errors: {} };
  const refresh = {
    get snapshot() { return snapshot; },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    async request() {
      listeners.forEach((listener) => listener(snapshot));
      return snapshot;
    },
  };
  const request = async (url, options) => {
    calls.push([url, options]);
    snapshot = {
      ...snapshot,
      jobs: [{ id: 71, kind: "forge", status: "queued", payload: { slug: collection.slug } }],
    };
    return { id: 71, status: "queued" };
  };

  try {
    createLibraryScreen({ request, refresh })({ workspace: dom.workspace, signal: controller.signal });
    await flush();
    assert.equal(buttonWithText(dom.workspace, "Open for review"), undefined);
    const finish = buttonWithText(dom.workspace, "Finish preparation");
    assert.ok(finish);
    await finish.click();
    await flush();
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], "/api/forge");
    assert.match(dom.workspace.textContent, /Forge queued/);
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("focused Review stage-gates assignment and offers the same Finish preparation route", async () => {
  const dom = installDom();
  const controller = new AbortController();
  const collection = {
    slug: "legacy-story",
    title: "Legacy Story",
    stage: "extracted",
    track_count: 1,
    total_duration: "10m",
    tonies_needed: 1,
    tracks: [{ name: "one.mp3", title: "One", seconds: 600, duration: "10m" }],
    plan: [{ index: 1, seconds: 600, duration: "10m", tracks: [] }],
  };
  const calls = [];
  const listeners = new Set();
  let snapshot = {
    status: { usable_limit_seconds: 5370, tonie_limit_seconds: 5400 },
    collections: [collection],
    jobs: [],
    stale: [],
    errors: {},
  };
  const refresh = {
    get snapshot() { return snapshot; },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    async request() {
      listeners.forEach((listener) => listener(snapshot));
      return snapshot;
    },
  };
  const request = async (url, options = {}) => {
    calls.push([url, options]);
    if (url === "/api/collections/legacy-story") return collection;
    if (url === "/api/forge") {
      snapshot = {
        ...snapshot,
        jobs: [{ id: 91, kind: "forge", status: "queued", payload: { slug: collection.slug } }],
      };
      return { id: 91, status: "queued" };
    }
    throw new Error(`Unexpected request ${url}`);
  };

  try {
    createFocusedReview({
      workspace: dom.workspace,
      slug: collection.slug,
      request,
      refresh,
      player: { play() {} },
      signal: controller.signal,
    });
    await flush();
    assert.match(dom.workspace.textContent, /Forge incomplete/);
    assert.equal(buttonWithText(dom.workspace, "Choose Creative Tonies"), undefined);
    const finish = buttonWithText(dom.workspace, "Finish preparation");
    assert.ok(finish);
    await finish.click();
    await flush();
    assert.equal(calls.filter(([url]) => url === "/api/forge").length, 1);
    assert.match(dom.workspace.textContent, /Forge queued/);
  } finally {
    controller.abort();
    dom.restore();
  }
});
