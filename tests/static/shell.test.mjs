import assert from "node:assert/strict";
import test from "node:test";

import { createRouter } from "../../app/static/router.js";
import { setBusy, showConfirmDialog } from "../../app/static/shared.js";

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
  showModal() {}
  close() {}
  remove() {}
}

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
