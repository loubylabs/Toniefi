import assert from "node:assert/strict";
import test from "node:test";

import {
  buildTonieChapterPayload,
  createTonieMutation,
} from "../../app/static/tonies.js";
import {
  activityAction,
  activityFacts,
  retryActivityJob,
} from "../../app/static/activity.js";
import {
  createSettingsScreen,
  credentialView,
  settingsFacts,
} from "../../app/static/settings.js";


class TinyElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.childNodes = [];
    this.parentNode = null;
    this.attributes = new Map();
    this.listeners = new Map();
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.value = "";
  }

  append(...children) {
    for (const child of children) {
      if (child?.parentNode) {
        child.parentNode.childNodes = child.parentNode.childNodes.filter((item) => item !== child);
      }
      if (child && typeof child !== "string") child.parentNode = this;
      this.childNodes.push(child);
    }
  }

  replaceChildren(...children) {
    this.childNodes.forEach((child) => {
      if (child && typeof child !== "string") child.parentNode = null;
    });
    this.childNodes = [];
    this.append(...children);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  addEventListener(name, listener) {
    this.listeners.set(name, listener);
  }
}


test("Tonie chapter payload carries the title-aware canonical base", () => {
  const tonie = {
    householdId: "house-1",
    id: "tonie-1",
    chapters: [
      { id: "chapter-a", title: "One", seconds: 60 },
      { id: "chapter-b", title: "Two", seconds: 70 },
    ],
  };

  assert.deepEqual(buildTonieChapterPayload(tonie, [
    { id: "chapter-b", title: "Renamed" },
    { id: "chapter-a", title: "One" },
  ]), {
    base: [
      { id: "chapter-a", title: "One" },
      { id: "chapter-b", title: "Two" },
    ],
    chapters: [
      { id: "chapter-b", title: "Renamed" },
      { id: "chapter-a", title: "One" },
    ],
  });
});


test("Tonie mutation locks competing saves and reloads remote truth after failure", async () => {
  const pending = [];
  const reloads = [];
  let rejectSave;
  const tonie = { householdId: "house 1", id: "fox/1", chapters: [{ id: "a", title: "One" }] };
  const mutation = createTonieMutation({
    request: async () => new Promise((resolve, reject) => { rejectSave = reject; }),
    reload: async () => reloads.push("truth") || [{ id: "current" }],
    setPending: (value) => pending.push(value),
  });

  const first = mutation.save(tonie, [{ id: "a", title: "Renamed" }]);
  assert.equal(await mutation.save(tonie, []), false);
  rejectSave(new Error("stale write"));
  await assert.rejects(first, /stale write/);

  assert.deepEqual(pending, [true, false]);
  assert.deepEqual(reloads, ["truth"]);
});


test("Activity sends successful preparation to review and failed push back to Review guidance", () => {
  assert.deepEqual(activityAction({
    id: 11,
    kind: "prepare_url",
    status: "done",
    result: { slug: "night-stories" },
    retryable: false,
  }), {
    kind: "review",
    href: "/review/night-stories",
    label: "Open review",
    guidance: "",
  });
  assert.deepEqual(activityAction({
    id: 12,
    kind: "push",
    status: "failed",
    payload: { slug: "night-stories" },
    retryable: false,
  }), {
    kind: "review",
    href: "/review/night-stories",
    label: "Review assignment",
    guidance: "Creative Tonie sends must be reviewed and confirmed again.",
  });
});


test("Activity presents job type, phase, status, and update time as separate facts", () => {
  assert.deepEqual(activityFacts({
    kind: "prepare_url",
    phase: "forging",
    status: "running",
    created_at: 100,
    updated_at: 200,
  }, () => "Jan 1, 1970, 12:03 AM"), [
    ["Type", "URL preparation"],
    ["Phase", "Forging"],
    ["Status", "Running"],
    ["Updated", "Jan 1, 1970, 12:03 AM"],
  ]);
});


test("Activity retry appends through the server and refreshes history", async () => {
  const calls = [];
  const refreshed = [];
  const created = await retryActivityJob(31, {
    request: async (...args) => {
      calls.push(args);
      return { id: 47, status: "queued" };
    },
    refresh: { request: async () => refreshed.push("refresh") || { jobs: [{ id: 47 }, { id: 31 }] } },
  });

  assert.deepEqual(calls, [["/api/jobs/31/retry", { method: "POST" }]]);
  assert.deepEqual(refreshed, ["refresh"]);
  assert.deepEqual(created, { id: 47, status: "queued" });
});


test("Settings credential view makes environment precedence explicit", () => {
  assert.deepEqual(credentialView({
    configured: true,
    source: "environment",
    username: "family@example.com",
  }), {
    state: "connected",
    label: "Connected",
    sourceLabel: "Environment variables",
    username: "family@example.com",
    fieldsDisabled: true,
    saveLabel: "Save local credentials",
    explanation: "Environment credentials are active. Local values cannot override them.",
  });
  assert.equal(credentialView({ configured: false, source: "none", username: "" }).state, "unconfigured");
  assert.equal(credentialView({ configured: true, source: "saved", username: "saved@example.com" }).saveLabel, "Replace local credentials");
});


test("Settings facts derive usable headroom and tool status from server truth", () => {
  assert.deepEqual(settingsFacts({
    library_dir: "/library",
    tonie_limit_seconds: 5400,
    usable_limit_seconds: 5370,
    tonie_limit_human: "1h 30m",
    tools: { ffmpeg: true, ffprobe: false },
  }), {
    limit: "1h 30m",
    usable: "1h 29m 30s",
    headroom: "30s",
    libraryPath: "/library",
    tools: [
      { name: "ffmpeg", available: true },
      { name: "ffprobe", available: false },
    ],
  });
});


test("Settings keeps service disclosures mounted while status is loading", () => {
  const originalDocument = globalThis.document;
  const document = {
    activeElement: null,
    createElement: (tagName) => new TinyElement(tagName),
    getElementById: () => null,
  };
  globalThis.document = document;
  const workspace = new TinyElement("main");
  const refresh = {
    snapshot: { status: null },
    subscribe: () => () => {},
    request: () => new Promise(() => {}),
  };

  try {
    createSettingsScreen({ request: async () => ({}), refresh })({
      workspace,
      signal: new AbortController().signal,
    });
    const root = workspace.childNodes[0];
    assert.equal(root.childNodes.some((node) => node.className === "settings-section disclosure-settings"), true);
  } finally {
    globalThis.document = originalDocument;
  }
});
