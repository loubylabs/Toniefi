import assert from "node:assert/strict";
import test from "node:test";

import { createLiveWorkCart } from "../../app/static/desk.js";
import { scopeRequest } from "../../app/static/api.js";
import { createRefreshCoordinator, scopeRefresh, updateShell } from "../../app/static/refresh.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((accept, refuse) => {
    resolve = accept;
    reject = refuse;
  });
  return { promise, resolve, reject };
}

function textOf(node) {
  if (typeof node === "string") return node;
  return `${node._text || ""}${node.childNodes.map(textOf).join("")}`;
}

function matches(node, selector) {
  if (selector.startsWith(".")) return node.className.split(/\s+/).includes(selector.slice(1));
  if (selector.startsWith("#")) return node.id === selector.slice(1);
  return node.tagName === selector.toUpperCase();
}

class TestElement {
  constructor(tagName, ownerDocument) {
    this.tagName = tagName.toUpperCase();
    this.ownerDocument = ownerDocument;
    this.childNodes = [];
    this.attributes = new Map();
    this.className = "";
    this.id = "";
    this._text = "";
    this.hidden = false;
    this.dataset = {};
  }

  set textContent(value) {
    this._text = String(value ?? "");
    this.childNodes = [];
  }

  get textContent() {
    return textOf(this);
  }

  append(...children) {
    this.childNodes.push(...children);
  }

  replaceChildren(...children) {
    this.childNodes = [...children];
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "id") this.id = String(value);
    if (name.startsWith("data-")) {
      const key = name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      this.dataset[key] = String(value);
    }
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  addEventListener() {}

  contains() {
    return false;
  }

  querySelectorAll(selector) {
    const found = [];
    const visit = (node) => {
      if (typeof node === "string") return;
      if (matches(node, selector)) found.push(node);
      node.childNodes.forEach(visit);
    };
    this.childNodes.forEach(visit);
    return found;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

class TestDocument {
  constructor() {
    this.body = new TestElement("body", this);
    this.activeElement = null;
    this.hidden = false;
    this.listeners = new Map();
  }

  createElement(tagName) {
    return new TestElement(tagName, this);
  }

  getElementById(id) {
    return this.body.querySelector(`#${id}`);
  }

  addEventListener(name, listener) {
    this.listeners.set(name, listener);
  }
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

test("refresh publishes jobs to the live shell and cart while collections is unresolved", async () => {
  const originalDocument = globalThis.document;
  const originalWindow = globalThis.window;
  const document = new TestDocument();
  const activityStatus = document.createElement("span");
  activityStatus.id = "activityStatus";
  const activityCount = document.createElement("span");
  activityCount.id = "activityCount";
  document.body.append(activityStatus, activityCount);
  globalThis.document = document;
  globalThis.window = { requestAnimationFrame: (callback) => callback() };

  const collections = deferred();
  const resources = {
    "/api/status": Promise.resolve({ credentials: { configured: false } }),
    "/api/jobs": Promise.resolve([{
      id: 73,
      kind: "prepare_url",
      status: "running",
      phase: "forging",
      label: "Moon Story",
      progress: "Levelling chapter 2 of 8",
      payload: { url: "https://story.test/moon" },
      result: {},
    }]),
    "/api/jobs/history": Promise.resolve([]),
    "/api/collections": collections.promise,
    "/api/desk/dismissals": Promise.resolve({}),
  };
  const coordinator = createRefreshCoordinator({
    request: (path) => resources[path],
    documentObject: document,
    windowObject: { setTimeout: () => 1, clearTimeout() {} },
    notifyFailure() {},
  });
  const cart = createLiveWorkCart({
    request: async () => ({}),
    requestRefresh: () => coordinator.request(),
    navigate() {},
    signal: new AbortController().signal,
  });
  document.body.append(cart.host);
  coordinator.subscribe(updateShell);
  coordinator.subscribe(cart.onRefresh);

  try {
    const pending = coordinator.request();
    await flush();

    assert.equal(activityStatus.hidden, false);
    assert.equal(activityCount.textContent, "1");
    assert.match(cart.host.textContent, /Moon Story/);
    assert.match(cart.host.textContent, /Levelling chapter 2 of 8/);

    collections.resolve([]);
    await pending;
  } finally {
    coordinator.stop();
    globalThis.document = originalDocument;
    globalThis.window = originalWindow;
  }
});

test("refresh retains fulfilled slices and records stale errors per resource", async () => {
  const documentObject = { hidden: false, addEventListener() {} };
  const windowObject = { setTimeout: () => 1, clearTimeout() {} };
  let collectionsFail = false;
  const coordinator = createRefreshCoordinator({
    request: async (path) => {
      if (path === "/api/status") return { configured: true };
      if (path === "/api/jobs") return [{ id: 1 }];
      if (path === "/api/jobs/history") return [{ id: 2 }];
      if (path === "/api/desk/dismissals") return {};
      if (collectionsFail) throw new Error("collection lease busy");
      return [{ slug: "story" }];
    },
    documentObject,
    windowObject,
    notifyFailure() {},
  });

  await coordinator.request();
  collectionsFail = true;
  const snapshot = await coordinator.request();

  assert.deepEqual(snapshot.collections, [{ slug: "story" }]);
  assert.deepEqual(snapshot.stale, ["collections"]);
  assert.equal(snapshot.errors.collections.message, "collection lease busy");
  coordinator.stop();
});

test("route request and refresh scopes carry one mounted signal and silence subscribers after abort", async () => {
  const controller = new AbortController();
  const requestCalls = [];
  const request = scopeRequest(async (path, options) => {
    requestCalls.push([path, options]);
    return { ok: true };
  }, controller.signal);
  let listener;
  let unsubscribed = 0;
  const refreshCalls = [];
  const refresh = scopeRefresh({
    snapshot: { jobs: [] },
    request: async (options) => refreshCalls.push(options) || { jobs: [] },
    subscribe(next) {
      listener = next;
      return () => { unsubscribed += 1; };
    },
  }, controller.signal);
  const notifications = [];
  refresh.subscribe((snapshot) => notifications.push(snapshot));

  await request("/api/route-action", { method: "POST" });
  await refresh.request();
  assert.equal(requestCalls[0][1].signal, controller.signal);
  assert.equal(refreshCalls[0].signal, controller.signal);

  controller.abort();
  listener({ jobs: [{ id: 1 }] });
  assert.equal(unsubscribed, 1);
  assert.deepEqual(notifications, []);
});

test("the phone navigation shows the active job count too", () => {
  // The badge markup lived only in the desktop sidebar, which is hidden below
  // 759.98px, so a phone showed no sign at all that a send was running.
  const originalDocument = globalThis.document;
  const document = new TestDocument();
  const made = {};
  for (const id of [
    "activityStatus", "activityCount",
    "mobileActivityStatus", "mobileActivityCount", "mobileMoreStatus",
  ]) {
    const node = document.createElement("span");
    node.id = id;
    node.hidden = true;
    made[id] = node;
    document.body.append(node);
  }
  globalThis.document = document;
  try {
    updateShell({
      status: null,
      jobs: [{ status: "running" }, { status: "queued" }, { status: "done" }],
      stale: [],
      errors: {},
    });
    assert.equal(made.mobileActivityCount.textContent, "2");
    assert.equal(made.mobileActivityStatus.hidden, false);
    assert.equal(made.mobileActivityStatus.getAttribute("aria-label"), "2 jobs active");
    assert.equal(made.mobileMoreStatus.hidden, false);
    assert.equal(made.activityCount.textContent, "2");

    updateShell({ status: null, jobs: [], stale: [], errors: {} });
    assert.equal(made.mobileActivityStatus.hidden, true);
    assert.equal(made.mobileMoreStatus.hidden, true);
  } finally {
    globalThis.document = originalDocument;
  }
});
