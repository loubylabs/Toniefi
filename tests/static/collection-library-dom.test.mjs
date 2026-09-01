import assert from "node:assert/strict";
import test from "node:test";

import { createDeskScreen, createLiveWorkCart } from "../../app/static/desk.js";
import { createLibraryScreen, forgePreparationState } from "../../app/static/library.js";
import { createCollectionDetail } from "../../app/static/collection.js";

import { buttonWithText, flush, installDom } from "./mini-dom.mjs";

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function forgeSettings(useChapters) {
  return {
    use_chapters: useChapters,
    normalize: true,
    clean_titles: true,
    trim_head: 0,
    trim_tail: 0,
    split_oversized: true,
  };
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

test("Desk keeps a populated source tray immediately before its preparation action", async () => {
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
    paste.value = Array.from(
      { length: 5 },
      (_, index) => `https://example.test/story-${index + 1}`,
    ).join("\n");
    await buttonWithText(dom.workspace, "Add to tray").click();
    await flush();

    const form = dom.workspace.querySelector(".source-intake-form");
    const tray = dom.workspace.querySelector(".source-row-list");
    const prepare = dom.workspace.querySelector(".desk-prepare-button");
    const forge = dom.workspace.querySelector(".forge-summary");
    const formChildren = form.childNodes.filter((child) => typeof child !== "string");
    const trayIndex = formChildren.indexOf(tray);
    const inputs = tray.querySelectorAll("input");

    assert.equal(formChildren[trayIndex + 1], prepare);
    assert.ok(formChildren.indexOf(forge) > formChildren.indexOf(prepare));
    assert.equal(prepare.textContent, "Prepare 5 stories");
    assert.equal(inputs.length, 5);
    assert.equal(inputs.at(-1).getAttribute("aria-label"), "Source URL 5");
    inputs.at(-1).focus();
    assert.equal(dom.document.activeElement, inputs.at(-1));
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("Desk loads saved Forge defaults and automatically persists later edits", async () => {
  const dom = installDom();
  const controller = new AbortController();
  const refresh = {
    snapshot: { status: {}, jobs: [], collections: [], stale: [], errors: {} },
    subscribe: () => () => {},
    request: async () => refresh.snapshot,
  };
  const writes = [];
  const saved = {
    use_chapters: false,
    normalize: true,
    clean_titles: true,
    trim_head: 0,
    trim_tail: 0,
    split_oversized: true,
  };
  const request = async (path, options = {}) => {
    if (path === "/api/settings/forge-defaults" && options.method === "PUT") {
      writes.push(JSON.parse(options.body));
      return JSON.parse(options.body);
    }
    if (path === "/api/settings/forge-defaults") return saved;
    return {};
  };

  try {
    createDeskScreen({ request, refresh })({
      workspace: dom.workspace,
      navigate() {},
      signal: controller.signal,
    });
    await flush();

    const forgeInputs = dom.workspace.querySelector(".forge-option-controls").querySelectorAll("input");
    const chapterMarkers = forgeInputs.find((input) => input.name === "use_chapters");
    assert.equal(chapterMarkers.checked, false);
    assert.match(dom.workspace.querySelector(".forge-definition-list").textContent, /Chapter markersIgnored/);

    const normalize = forgeInputs.find((input) => input.name === "normalize");
    normalize.checked = false;
    await normalize.dispatchEvent({ type: "change" });
    await flush();

    assert.deepEqual(writes, [{
      use_chapters: false,
      normalize: false,
      clean_titles: true,
      trim_head: 0,
      trim_tail: 0,
      split_oversized: true,
    }]);
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("Forge default saves finish after the Desk route is abandoned", async () => {
  const dom = installDom();
  const controller = new AbortController();
  const refresh = {
    snapshot: { status: {}, jobs: [], collections: [], stale: [], errors: {} },
    subscribe: () => () => {},
    request: async () => refresh.snapshot,
  };
  let releaseSave;
  const savedBodies = [];
  const request = async (path, options = {}) => {
    if (path === "/api/settings/forge-defaults" && options.method === "PUT") {
      throw new Error("A preference write must outlive the route request scope.");
    }
    if (path === "/api/settings/forge-defaults") {
      return {
        use_chapters: true,
        normalize: true,
        clean_titles: true,
        trim_head: 0,
        trim_tail: 0,
        split_oversized: true,
      };
    }
    return {};
  };
  const persistentRequest = async (path, options = {}) => {
    savedBodies.push({ path, options });
    await new Promise((resolve) => { releaseSave = resolve; });
    return JSON.parse(options.body);
  };

  try {
    createDeskScreen({ request, persistentRequest, refresh })({
      workspace: dom.workspace,
      navigate() {},
      signal: controller.signal,
    });
    await flush();

    const chapterMarkers = dom.workspace.querySelector(".forge-option-controls")
      .querySelectorAll("input")
      .find((input) => input.name === "use_chapters");
    chapterMarkers.checked = false;
    const changing = chapterMarkers.dispatchEvent({ type: "change" });
    await flush();
    controller.abort();
    releaseSave?.();
    await changing;

    assert.equal(savedBodies.length, 1);
    assert.equal(savedBodies[0].path, "/api/settings/forge-defaults");
    assert.equal(savedBodies[0].options.signal, undefined);
    assert.equal(JSON.parse(savedBodies[0].options.body).use_chapters, false);
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("a Forge default save that fails after navigation still reports the failure", async () => {
  const dom = installDom();
  const controller = new AbortController();
  const refresh = {
    snapshot: { status: {}, jobs: [], collections: [], stale: [], errors: {} },
    subscribe: () => () => {},
    request: async () => refresh.snapshot,
  };
  let rejectSave;
  const persistentRequest = async () => new Promise((_, reject) => { rejectSave = reject; });

  try {
    createDeskScreen({
      request: async (path) => path === "/api/settings/forge-defaults" ? forgeSettings(true) : {},
      persistentRequest,
      refresh,
    })({
      workspace: dom.workspace,
      navigate() {},
      signal: controller.signal,
    });
    await flush();

    const chapterMarkers = dom.workspace.querySelector(".forge-option-controls")
      .querySelectorAll("input")
      .find((input) => input.name === "use_chapters");
    chapterMarkers.checked = false;
    const changing = chapterMarkers.dispatchEvent({ type: "change" });
    await flush();
    controller.abort();
    rejectSave(new Error("The settings database is read-only."));
    await changing;

    assert.match(dom.spoken.at(-1), /Forge defaults were not saved.*settings database is read-only/);
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("URL preparation waits for saved Forge defaults before building its payload", async () => {
  const dom = installDom();
  globalThis.window.matchMedia = () => ({ matches: true });
  const controller = new AbortController();
  const loading = deferred();
  const prepared = [];
  const refresh = {
    snapshot: { status: {}, jobs: [], collections: [], stale: [], errors: {} },
    subscribe: () => () => {},
    request: async () => refresh.snapshot,
  };
  const request = async (path, options = {}) => {
    if (path === "/api/settings/forge-defaults") return loading.promise;
    if (path === "/api/prepare") prepared.push(JSON.parse(options.body));
    return {};
  };

  try {
    createDeskScreen({ request, refresh })({
      workspace: dom.workspace,
      navigate() {},
      signal: controller.signal,
    });
    dom.document.getElementById("source-paste").value = "https://example.test/story";
    await buttonWithText(dom.workspace, "Add to tray").click();

    const submitting = dom.workspace.querySelector(".source-intake-form").dispatchEvent({ type: "submit" });
    await flush();
    assert.equal(prepared.length, 0);

    loading.resolve(forgeSettings(false));
    await submitting;
    assert.equal(prepared[0].options.use_chapters, false);
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("LibriVox import waits for saved Forge defaults before building its payload", async () => {
  const dom = installDom();
  const controller = new AbortController();
  const loading = deferred();
  const imported = [];
  const refresh = {
    snapshot: { status: {}, jobs: [], collections: [], stale: [], errors: {} },
    subscribe: () => () => {},
    request: async () => refresh.snapshot,
  };
  const request = async (path, options = {}) => {
    if (path === "/api/settings/forge-defaults") return loading.promise;
    if (path.startsWith("/api/librivox/search")) {
      return [{ id: "42", title: "Peter Pan", authors: "J. M. Barrie", num_sections: 3, total_duration: "1h 00m" }];
    }
    if (path === "/api/librivox/import") imported.push(JSON.parse(options.body));
    return {};
  };

  try {
    createDeskScreen({ request, refresh })({
      workspace: dom.workspace,
      navigate() {},
      signal: controller.signal,
    });
    dom.document.getElementById("librivox-query").value = "Peter Pan";
    await dom.workspace.querySelector(".librivox-search").dispatchEvent({ type: "submit" });

    const importing = buttonWithText(dom.workspace, "Import and prepare").click();
    await flush();
    assert.equal(imported.length, 0);

    loading.resolve(forgeSettings(false));
    await importing;
    assert.equal(imported[0].options.use_chapters, false);
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("upload preparation waits for saved Forge defaults before building its payload", async () => {
  const dom = installDom();
  const controller = new AbortController();
  const loading = deferred();
  const uploaded = [];
  const refresh = {
    snapshot: { status: {}, jobs: [], collections: [], stale: [], errors: {} },
    subscribe: () => () => {},
    request: async () => refresh.snapshot,
  };
  const request = async (path, options = {}) => {
    if (path === "/api/settings/forge-defaults") return loading.promise;
    if (path === "/api/uploads/prepare") uploaded.push(JSON.parse(options.body.get("options")));
    return {};
  };

  try {
    createDeskScreen({ request, refresh })({
      workspace: dom.workspace,
      navigate() {},
      signal: controller.signal,
    });
    const file = new Blob(["audio"], { type: "audio/mpeg" });
    file.name = "story.mp3";
    dom.document.getElementById("upload-files").files = [file];

    const uploading = dom.workspace.querySelector(".local-upload-form").dispatchEvent({ type: "submit" });
    await flush();
    assert.equal(uploaded.length, 0);

    loading.resolve(forgeSettings(false));
    await uploading;
    assert.equal(uploaded[0].use_chapters, false);
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("Desk reports Forge default storage failures without blocking later saves", async () => {
  const dom = installDom();
  const controller = new AbortController();
  const refresh = {
    snapshot: { status: {}, jobs: [], collections: [], stale: [], errors: {} },
    subscribe: () => () => {},
    request: async () => refresh.snapshot,
  };
  let writes = 0;
  const request = async (path, options = {}) => {
    if (path === "/api/settings/forge-defaults" && options.method === "PUT") {
      writes += 1;
      if (writes === 1) throw new Error("The settings database is read-only.");
      return JSON.parse(options.body);
    }
    if (path === "/api/settings/forge-defaults") {
      return {
        use_chapters: true,
        normalize: true,
        clean_titles: true,
        trim_head: 0,
        trim_tail: 0,
        split_oversized: true,
      };
    }
    return {};
  };

  try {
    createDeskScreen({ request, refresh })({
      workspace: dom.workspace,
      navigate() {},
      signal: controller.signal,
    });
    await flush();

    const normalize = dom.workspace.querySelector(".forge-option-controls")
      .querySelectorAll("input")
      .find((input) => input.name === "normalize");
    normalize.checked = false;
    await normalize.dispatchEvent({ type: "change" });

    assert.match(dom.spoken.at(-1), /Forge defaults were not saved.*settings database is read-only/);

    normalize.checked = true;
    await normalize.dispatchEvent({ type: "change" });
    assert.equal(writes, 2);
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("Desk reports when saved Forge defaults cannot be loaded", async () => {
  const dom = installDom();
  const controller = new AbortController();
  const refresh = {
    snapshot: { status: {}, jobs: [], collections: [], stale: [], errors: {} },
    subscribe: () => () => {},
    request: async () => refresh.snapshot,
  };

  try {
    createDeskScreen({
      request: async (path) => {
        if (path === "/api/settings/forge-defaults") throw new Error("Settings are unavailable.");
        return {};
      },
      refresh,
    })({
      workspace: dom.workspace,
      navigate() {},
      signal: controller.signal,
    });
    await flush();

    assert.match(dom.spoken.at(-1), /Forge defaults could not be loaded.*Settings are unavailable/);
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
    assert.match(rows[0].textContent, /Ready to send/);
    assert.match(rows[0].textContent, /View details/);
    assert.doesNotMatch(rows[0].textContent, /Failed|Retry|Worker stopped/);
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

test("Forge preparation state keeps extracted collections unfinished and reports job truth", () => {
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

test("Library gives extracted collections one Finish preparation action and no link into the collection", async () => {
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
    assert.deepEqual(dom.workspace.querySelectorAll("a")
      .filter((link) => link.getAttribute("data-route") === "collection"), []);
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

test("Library offers every collection a download, whether or not Forge has finished", async () => {
  const dom = installDom();
  const controller = new AbortController();
  const collection = {
    slug: "peter pan & wendy",
    title: "Peter Pan",
    stage: "extracted",
    track_count: 2,
    total_duration: "1h 4m",
    tonies_needed: 1,
  };
  const refresh = {
    snapshot: { collections: [collection], jobs: [] },
    subscribe() {
      return () => {};
    },
    async request() {
      return { collections: [collection], stale: [], errors: {} };
    },
  };
  const request = async (url) => {
    throw new Error(`Unexpected request ${url}`);
  };

  try {
    createLibraryScreen({ request, refresh })({ workspace: dom.workspace, signal: controller.signal });
    await flush();
    const download = dom.workspace.querySelector(".library-download");
    assert.ok(download, "every collection row offers a download");
    assert.equal(download.tagName, "A");
    assert.equal(download.getAttribute("href"), "/api/collections/peter%20pan%20%26%20wendy/download");
    assert.equal(download.hasAttribute("download"), true);
    assert.equal(download.hasAttribute("data-collection-mutation"), true);
    assert.match(download.textContent, /Download/);
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("the collection screen stage-gates the capacity plan and offers the same Finish preparation route", async () => {
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
    createCollectionDetail({
      workspace: dom.workspace,
      slug: collection.slug,
      request,
      refresh,
      player: { play() {} },
      signal: controller.signal,
    });
    await flush();
    assert.match(dom.workspace.textContent, /Forge incomplete/);
    assert.equal(dom.workspace.querySelector(".capacity-plan"), null);
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

test("Desk sends only the playlist videos left ticked", async () => {
  const dom = installDom();
  globalThis.window.matchMedia = () => ({ matches: true });
  const controller = new AbortController();
  const refresh = {
    snapshot: { status: {}, jobs: [], collections: [], stale: [], errors: {} },
    subscribe: () => () => {},
    request: async () => refresh.snapshot,
  };
  const posted = [];
  const request = async (path, options = {}) => {
    if (path === "/api/playlist/preview") {
      return {
        title: "Story Time",
        entries: [
          { index: 1, id: "aaa", title: "One", available: true },
          { index: 2, id: "bbb", title: "Two", available: true },
          { index: 3, id: "ccc", title: "Three", available: true },
        ],
      };
    }
    if (path === "/api/prepare") posted.push(JSON.parse(options.body));
    return {};
  };

  try {
    createDeskScreen({ request, refresh })({
      workspace: dom.workspace,
      navigate() {},
      signal: controller.signal,
    });
    dom.document.getElementById("source-paste").value = "https://www.youtube.com/playlist?list=PL1";
    await buttonWithText(dom.workspace, "Add to tray").click();
    await flush();

    await buttonWithText(dom.workspace, "Pick videos").click();
    await flush();

    const boxes = dom.workspace.querySelector(".playlist-picker").querySelectorAll("input");
    assert.deepEqual(boxes.map((box) => box.getAttribute("aria-label")), ["1. One", "2. Two", "3. Three"]);
    assert.equal(boxes.every((box) => box.checked), true);

    boxes[1].checked = false;
    await boxes[1].dispatchEvent({ type: "change" });
    await flush();

    await dom.workspace.querySelector(".source-intake-form").dispatchEvent({ type: "submit" });
    await flush();

    assert.deepEqual(posted[0].sources, [
      { url: "https://www.youtube.com/playlist?list=PL1", playlist_items: [1, 3] },
    ]);
  } finally {
    controller.abort();
    dom.restore();
  }
});

test("Desk holds back a playlist row with nothing ticked instead of sending it", async () => {
  // Unticking everything used to submit an empty list, which the download read
  // as "nobody picked", and a bare playlist link brings every entry under that
  // reading. The one gesture meaning stop cost the most.
  const dom = installDom();
  globalThis.window.matchMedia = () => ({ matches: true });
  const controller = new AbortController();
  const refresh = {
    snapshot: { status: {}, jobs: [], collections: [], stale: [], errors: {} },
    subscribe: () => () => {},
    request: async () => refresh.snapshot,
  };
  const posted = [];
  const request = async (path, options = {}) => {
    if (path === "/api/playlist/preview") {
      return {
        title: "Story Time",
        entries: [
          { index: 1, id: "aaa", title: "One", available: true },
          { index: 2, id: "bbb", title: "Two", available: true },
        ],
      };
    }
    if (path === "/api/prepare") posted.push(JSON.parse(options.body));
    return {};
  };

  try {
    createDeskScreen({ request, refresh })({
      workspace: dom.workspace,
      navigate() {},
      signal: controller.signal,
    });
    dom.document.getElementById("source-paste").value = "https://www.youtube.com/playlist?list=PL1";
    await buttonWithText(dom.workspace, "Add to tray").click();
    await flush();
    await buttonWithText(dom.workspace, "Pick videos").click();
    await flush();

    await buttonWithText(dom.workspace, "Untick all").click();
    await flush();

    assert.match(dom.workspace.querySelector(".inline-error").textContent, /Pick at least one video/);
    assert.equal(buttonWithText(dom.workspace, "Prepare 1 story").disabled, true);

    await dom.workspace.querySelector(".source-intake-form").dispatchEvent({ type: "submit" });
    await flush();

    assert.deepEqual(posted, []);
  } finally {
    controller.abort();
    dom.restore();
  }
});
