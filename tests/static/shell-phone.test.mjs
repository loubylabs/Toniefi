import assert from "node:assert/strict";
import test from "node:test";

import { createActivityScreen } from "../../app/static/activity.js";

import { flush, installDom } from "./mini-dom.mjs";

function mountActivity(history = []) {
  const dom = installDom();
  const controller = new AbortController();
  const listeners = new Set();
  const refresh = {
    snapshot: { history, jobs: [], stale: [], errors: {} },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    request: async () => {
      for (const listener of listeners) listener(refresh.snapshot);
      return refresh.snapshot;
    },
  };
  const cleanup = createActivityScreen({ request: async () => ({}), refresh })({
    workspace: dom.workspace,
    signal: controller.signal,
  });
  return {
    dom,
    refresh,
    one: (selector) => dom.workspace.querySelector(selector),
    all: (selector) => dom.workspace.querySelectorAll(selector),
    stop() {
      cleanup();
      controller.abort();
      dom.restore();
    },
  };
}

test("the phone heading hides the action's words, it never removes them", async () => {
  const s = mountActivity();
  try {
    await flush();
    assert.equal(s.all("h1").length, 1);
    const label = s.one(".heading-action-label");
    assert.equal(label.textContent, "Refresh history");
  } finally {
    s.stop();
  }
});
