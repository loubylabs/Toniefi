import assert from "node:assert/strict";
import test from "node:test";

import { createActivityScreen } from "../../app/static/activity.js";
import { createSettingsScreen } from "../../app/static/settings.js";
import { createPersistentAudioPlayer } from "../../app/static/shared.js";

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

const longError = "ERROR: [youtube] Sign in to confirm you are not a bot. ".repeat(6);
const failed = (id, error) => ({ id, kind: "forge", status: "failed", phase: "failed", label: `Job ${id}`, payload: {}, error });

test("a long error opens and closes, and a refresh re-render keeps it open", async () => {
  const s = mountActivity([failed(1, longError), failed(2, "Short.")]);
  try {
    await flush();
    assert.equal(s.all(".activity-error-toggle").length, 1, "a short error gets no toggle");
    const toggle = () => s.one(".activity-error-toggle");
    assert.equal(toggle().getAttribute("aria-expanded"), "false");
    assert.equal(toggle().textContent, "Show full error");
    toggle().click();
    assert.equal(toggle().getAttribute("aria-expanded"), "true");
    assert.equal(s.one(".activity-error").getAttribute("data-open"), "true");
    await s.refresh.request(); // a poll rebuilds every row
    assert.equal(toggle().getAttribute("aria-expanded"), "true", "still open after the rebuild");
    assert.equal(s.one(".activity-error").textContent, longError, "the real error is never cut");
    toggle().click();
    assert.equal(s.one(".activity-error").getAttribute("data-open"), "false");
  } finally {
    s.stop();
  }
});

for (const [name, credentials, expected] of [
  ["unconfigured", { configured: false, source: "none", username: "" }, "false"],
  ["configured", { configured: true, source: "saved", username: "me@example.com" }, "true"],
]) {
  test(`Settings marks its source and username rows ${name}, so a phone hides them only while unconfigured`, async () => {
    const dom = installDom();
    const controller = new AbortController();
    const status = { credentials, tonie_limit_seconds: 5400, usable_limit_seconds: 5370, tools: {} };
    const refresh = {
      snapshot: { status },
      subscribe: () => () => {},
      request: async () => ({ status, stale: [], errors: {} }),
    };
    try {
      const cleanup = createSettingsScreen({ request: async () => ({}), refresh })({
        workspace: dom.workspace,
        signal: controller.signal,
      });
      await flush();
      const facts = dom.workspace.querySelector(".account-facts");
      assert.equal(facts.getAttribute("data-configured"), expected);
      assert.equal(facts.querySelectorAll("dt").length, 2, "the rows are always in the DOM");
      cleanup();
    } finally {
      controller.abort();
      dom.restore();
    }
  });
}

test("the player's Dismiss keeps its name and its words for a phone's icon-only button", () => {
  const dom = installDom();
  try {
    const host = dom.document.createElement("section");
    createPersistentAudioPlayer({ host });
    const dismiss = host.querySelector(".audio-dismiss");
    assert.equal(dismiss.getAttribute("aria-label"), "Dismiss chapter player");
    assert.equal(dismiss.querySelector(".audio-dismiss-label").textContent, "Dismiss");
    assert.ok(dismiss.querySelector(".audio-dismiss-mark"));
  } finally {
    dom.restore();
  }
});
