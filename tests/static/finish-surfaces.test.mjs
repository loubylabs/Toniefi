import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  buildTonieChapterPayload,
  createTonieMutation,
  tonieLoadView,
} from "../../app/static/tonies.js";
import {
  activityAction,
  activityFacts,
  activityHistory,
  retryActivityJob,
} from "../../app/static/activity.js";
import {
  createSettingsScreen,
  credentialView,
  settingsFacts,
  toolPresentation,
} from "../../app/static/settings.js";


class TinyElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.childNodes = [];
    this.parentNode = null;
    this.attributes = new Map();
    this.listeners = new Map();
    this.className = "";
    this.dataset = {};
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

  querySelector(selector) {
    if (selector === "span:last-child") {
      return [...this.childNodes].reverse().find((node) => node?.tagName === "SPAN") || null;
    }
    return null;
  }

  focus() {}
}


function descendants(root) {
  return [root, ...(root?.childNodes || []).flatMap(descendants)];
}


function textOf(node) {
  return [node?.textContent || "", ...(node?.childNodes || []).map(textOf)].join("");
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


test("Tonie mutation reports whether remote truth was actually reloaded", async () => {
  const tonie = { householdId: "house-1", id: "fox-1", chapters: [] };
  const reloaded = createTonieMutation({
    request: async () => { throw new Error("save refused"); },
    reload: async () => [],
  });
  await assert.rejects(reloaded.save(tonie, []), (error) => {
    assert.equal(error.message, "save refused");
    assert.equal(error.remoteReloaded, true);
    assert.equal(error.reloadError, null);
    return true;
  });

  const stale = createTonieMutation({
    request: async () => { throw new Error("save refused"); },
    reload: async () => { throw new Error("read refused"); },
  });
  await assert.rejects(stale.save(tonie, []), (error) => {
    assert.equal(error.message, "save refused");
    assert.equal(error.remoteReloaded, false);
    assert.equal(error.reloadError.message, "read refused");
    return true;
  });
});


test("Tonie load presentation distinguishes loading, empty, failed, and stale data", () => {
  assert.deepEqual(tonieLoadView({ state: "loading", tonies: [], error: "" }), { kind: "loading", stale: false });
  assert.deepEqual(tonieLoadView({ state: "loaded", tonies: [], error: "" }), { kind: "empty", stale: false });
  assert.deepEqual(tonieLoadView({ state: "failed", tonies: [], error: "Login failed" }), { kind: "failed", stale: false });
  assert.deepEqual(tonieLoadView({ state: "failed", tonies: [{ id: "fox-1" }], error: "Read failed" }), { kind: "loaded", stale: true });
});


test("Activity opens the collection after preparation and after a failed push", () => {
  assert.deepEqual(activityAction({
    id: 11,
    kind: "prepare_url",
    status: "done",
    result: { slug: "night-stories" },
    retryable: false,
  }), {
    kind: "collection",
    href: "/collection/night-stories",
    label: "Open collection",
    guidance: "",
  });
  assert.deepEqual(activityAction({
    id: 12,
    kind: "push",
    status: "failed",
    payload: { sources: [{ slug: "night-stories", files: ["one.mp3"] }] },
    retryable: false,
  }), {
    kind: "collection",
    href: "/collection/night-stories",
    label: "Open collection",
    guidance: "Some chapters may already be on the Tonie. Open the Tonie, check what landed, then send only the rest.",
  });
});


test("a failed single-collection push still links to its collection", () => {
  const action = activityAction({
    kind: "push",
    status: "failed",
    payload: { sources: [{ slug: "night-stories", files: ["one.mp3"] }] },
  });

  assert.equal(action.href, "/collection/night-stories");
});


test("a failed multi-collection push offers no single collection link", () => {
  const several = activityAction({
    kind: "push",
    status: "failed",
    payload: { sources: [{ slug: "night-stories" }, { slug: "sea-tales" }] },
  });

  assert.equal(several.kind, "none");

  // The positive control belongs in the same test. Without it this passes just
  // as well against an implementation that never reads `payload.sources` and so
  // offers no link for any push at all.
  const one = activityAction({
    kind: "push",
    status: "failed",
    payload: { sources: [{ slug: "night-stories" }] },
  });

  assert.equal(one.kind, "collection");
});


test("Activity only offers a collection link for LibriVox work whose collection is forged", () => {
  assert.equal(activityAction({
    kind: "librivox",
    status: "done",
    collection_stage: "forged",
    result: { slug: "ready-book" },
  }).kind, "collection");
  assert.deepEqual(activityAction({
    kind: "librivox",
    status: "done",
    collection_stage: "extracted",
    result: { slug: "legacy-book" },
  }), { kind: "none", href: "", label: "", guidance: "" });
});


test("Activity consumes only chronological history from the refresh snapshot", () => {
  const snapshot = {
    jobs: [{ id: 3 }, { id: 1 }],
    history: [{ id: 50 }, { id: 49 }],
  };
  assert.deepEqual(activityHistory(snapshot), [{ id: 50 }, { id: 49 }]);

  const refreshSource = readFileSync(new URL("../../app/static/refresh.js", import.meta.url), "utf8");
  assert.match(refreshSource, /api\/jobs\/history/);
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
    state: "configured",
    label: "Configured",
    sourceLabel: "Environment variables",
    username: "family@example.com",
    fieldsDisabled: true,
    saveLabel: "Save local credentials",
    explanation: "Environment credentials are active. Local values cannot override them.",
  });
  assert.equal(credentialView({ configured: false, source: "none", username: "" }).state, "unconfigured");
  assert.equal(credentialView({ configured: true, source: "saved", username: "saved@example.com" }).saveLabel, "Replace local credentials");
  assert.equal(credentialView({ configured: true }, { state: "connected" }).label, "Connected");
  assert.equal(credentialView({ configured: true }, { state: "failed" }).label, "Connection failed");
  assert.deepEqual(credentialView({
    configured: false,
    source: "environment",
    username: "partial@example.com",
  }), {
    state: "unconfigured",
    label: "Unconfigured",
    sourceLabel: "Environment variables",
    username: "partial@example.com",
    fieldsDisabled: true,
    saveLabel: "Save local credentials",
    explanation: "Environment credentials are incomplete. Set both TONIES_USERNAME and TONIES_PASSWORD before TonieFi can connect.",
  });
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
  assert.deepEqual(toolPresentation({ name: "ffmpeg", available: true }), {
    icon: "check",
    label: "Available",
    state: "available",
  });
  assert.deepEqual(toolPresentation({ name: "ffprobe", available: false }), {
    icon: "alert",
    label: "Missing",
    state: "missing",
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
  let onRefresh;
  const refresh = {
    snapshot: { status: null },
    subscribe: (listener) => { onRefresh = listener; return () => {}; },
    request: () => new Promise(() => {}),
  };

  try {
    createSettingsScreen({ request: async () => ({}), refresh })({
      workspace,
      signal: new AbortController().signal,
    });
    const root = workspace.childNodes[0];
    const disclosure = root.childNodes.find((node) => node.className === "settings-section disclosure-settings");
    const textOf = (node) => [node?.textContent || "", ...(node?.childNodes || []).map(textOf)].join("");
    const exactDisclosure = "Service disclosuresImportant limits of the Creative Tonie connection.Private API. TonieFi uses the same private, unsupported myTonies API used by the web app. Its endpoints can change without notice.No affiliation. TonieFi is not affiliated with, endorsed by, or supported by tonies or Boxine.";
    assert.equal(textOf(disclosure), exactDisclosure);
    onRefresh({ status: null, stale: ["status"], errors: { status: new Error("offline") } });
    assert.equal(textOf(disclosure), exactDisclosure);
  } finally {
    globalThis.document = originalDocument;
  }
});


test("Replacing credentials clears a successful session test back to Configured", async () => {
  const originalDocument = globalThis.document;
  const document = {
    activeElement: null,
    createElement: (tagName) => new TinyElement(tagName),
    getElementById: () => null,
  };
  globalThis.document = document;
  const workspace = new TinyElement("main");
  const status = {
    credentials: { configured: true, source: "saved", username: "old@example.com" },
    tonie_limit_seconds: 5400,
    usable_limit_seconds: 5370,
    tools: {},
  };
  const refreshedStatus = {
    ...status,
    credentials: { configured: true, source: "saved", username: "new@example.com" },
  };
  const refresh = {
    snapshot: { status },
    subscribe: () => () => {},
    request: async () => ({ status: refreshedStatus, stale: [], errors: {} }),
  };
  const request = async (path) => path === "/api/settings/test"
    ? { email: "old@example.com" }
    : { configured: true, source: "saved", username: "new@example.com" };

  try {
    createSettingsScreen({ request, refresh })({
      workspace,
      signal: new AbortController().signal,
    });
    const nodes = descendants(workspace);
    const testConnection = nodes.find((node) => textOf(node) === "Test connection");
    await testConnection.listeners.get("click")();
    assert.match(textOf(descendants(workspace).find((node) => node.className === "account-connection-status")), /Connected/);

    const username = nodes.find((node) => node.attributes?.get("id") === "settings-username");
    const password = nodes.find((node) => node.attributes?.get("id") === "settings-password");
    const form = nodes.find((node) => node.className === "credential-form");
    username.value = "new@example.com";
    password.value = "new-password";
    await form.listeners.get("submit")({ preventDefault() {} });

    const connectionStatus = descendants(workspace).find((node) => node.className === "account-connection-status");
    assert.match(textOf(connectionStatus), /Configured/);
    assert.doesNotMatch(textOf(connectionStatus), /Connected/);
  } finally {
    globalThis.document = originalDocument;
  }
});
