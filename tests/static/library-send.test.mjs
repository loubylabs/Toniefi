import assert from "node:assert/strict";
import test from "node:test";

import { createLibraryScreen, createSelectionState, selectableCollections } from "../../app/static/library.js";

import { buttonWithText, flush, installDom } from "./mini-dom.mjs";

const forged = (slug) => ({
  slug,
  stage: "forged",
  title: slug,
  manifest_fingerprint: `f-${slug}`,
  tracks: [{ name: `${slug}.mp3`, title: slug, seconds: 300 }],
});

test("selectableCollections offers only forged collections", () => {
  const collections = [forged("a"), { slug: "b", stage: "extracted", title: "b" }];

  assert.deepEqual(selectableCollections(collections, []).map((item) => item.slug), ["a"]);
});

test("selection keeps Library order, not ticking order", () => {
  const state = createSelectionState();
  const collections = [forged("a"), forged("b"), forged("c")];

  state.toggle("c");
  state.toggle("a");

  assert.deepEqual(state.ordered(collections).map((item) => item.slug), ["a", "c"]);
});

test("selection drops a slug that left the index", () => {
  const state = createSelectionState();
  state.toggle("a");
  state.toggle("gone");

  assert.deepEqual(state.ordered([forged("a")]).map((item) => item.slug), ["a"]);
});

test("toggling twice deselects", () => {
  const state = createSelectionState();
  state.toggle("a");
  state.toggle("a");

  assert.equal(state.has("a"), false);
  assert.deepEqual(state.ordered([forged("a")]), []);
});

test("clear empties the selection", () => {
  const state = createSelectionState();
  state.toggle("a");
  state.toggle("b");
  state.clear();

  assert.deepEqual(state.ordered([forged("a"), forged("b")]), []);
});

const nightStory = () => ({
  slug: "night-story",
  stage: "forged",
  title: "Night Story",
  manifest_fingerprint: "f-night",
  track_count: 2,
  total_duration: "25m 00s",
  tonies_needed: 1,
  tracks: [
    { name: "01.mp3", title: "One", seconds: 600, duration: "10m 00s" },
    { name: "02.mp3", title: "Two", seconds: 900, duration: "15m 00s" },
  ],
});

const blueTonie = () => ({
  id: "t1",
  householdId: "h1",
  householdName: "Home",
  name: "Blue Tonie",
  time_free: "90m 00s",
  seconds_present: 0,
  chapters: [{ id: "c1", title: "Old chapter" }],
});

function mountLibrary({ collections, tonies = [blueTonie()] }) {
  const dom = installDom();
  const controller = new AbortController();
  const pushes = [];
  const refresh = {
    snapshot: { status: { usable_limit_seconds: 5400 }, collections, jobs: [], stale: [], errors: {} },
    subscribe: () => () => {},
    request: async () => refresh.snapshot,
  };
  const request = async (url, options = {}) => {
    if (url === "/api/tonies") return tonies;
    if (url === "/api/push/batch") {
      pushes.push(JSON.parse(options.body));
      return { job_id: 7 };
    }
    throw new Error(`Unexpected request ${url}`);
  };
  const cleanup = createLibraryScreen({ request, refresh })({
    workspace: dom.workspace,
    navigate() {},
    signal: controller.signal,
  });
  return {
    dom,
    pushes,
    node: (selector) => dom.workspace.querySelector(selector),
    byFocusKey: (key) => dom.workspace.querySelectorAll("[data-focus-key]").find((item) => item.getAttribute("data-focus-key") === key),
    focusKey: () => dom.document.activeElement?.getAttribute("data-focus-key") || "",
    stop() {
      cleanup();
      controller.abort();
      dom.restore();
    },
  };
}

test("only a forged row offers a labelled tick box", async () => {
  const screen = mountLibrary({
    collections: [nightStory(), { slug: "raw-story", stage: "extracted", title: "Raw Story" }],
  });
  try {
    await flush();
    const ticks = screen.dom.workspace.querySelectorAll(".library-select");
    assert.equal(ticks.length, 1);
    assert.equal(ticks[0].getAttribute("type"), "checkbox");
    assert.equal(ticks[0].id, "library-select-night-story");
    const label = screen.dom.workspace.querySelectorAll("label")
      .find((item) => item.getAttribute("for") === "library-select-night-story");
    assert.equal(label.textContent, "Select Night Story to send");
    assert.equal(label.className, "visually-hidden");
    assert.equal(screen.node(".library-send-bar").hidden, true);
  } finally {
    screen.stop();
  }
});

test("ticking a row opens the send bar, keeps focus on the tick and blocks a send with no target", async () => {
  const screen = mountLibrary({ collections: [nightStory()] });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();

    const bar = screen.node(".library-send-bar");
    assert.equal(bar.hidden, false);
    assert.match(bar.textContent, /1 story selected · 25m 00s/);
    // The fetch for targets settles after the tick, and must not take focus
    // off the checkbox the operator just used.
    assert.equal(screen.focusKey(), "library-night-story-select");
    assert.equal(screen.node(".library-send-submit").disabled, true);
    assert.match(screen.node(".library-send-validation").textContent, /Group 1 has no Creative Tonie chosen/);
    assert.deepEqual(
      screen.node(".library-send-target").childNodes.map((option) => option.getAttribute("value")),
      ["", "h1/t1"],
    );
    assert.match(screen.node(".library-send-target").textContent, /Blue Tonie · Home · 90m 00s free/);
    assert.deepEqual(
      screen.node(".library-send-membership").childNodes.map((item) => item.textContent),
      ["Night StoryOne10m 00s", "Night StoryTwo15m 00s"],
    );
  } finally {
    screen.stop();
  }
});

test("choosing a target sends one batch and clears the selection", async () => {
  const screen = mountLibrary({ collections: [nightStory()] });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();

    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();
    assert.equal(screen.focusKey(), "library-send-target-1");
    assert.equal(screen.node(".library-send-submit").disabled, false);
    assert.equal(screen.node(".library-send-submit").textContent, "Send 1 story");

    await screen.node(".library-send-submit").click();
    await flush();

    assert.equal(screen.pushes.length, 1);
    const payload = screen.pushes[0];
    assert.equal(typeof payload.operation_key, "string");
    assert.ok(payload.operation_key.length > 0);
    assert.deepEqual(payload.assignments, [{
      household_id: "h1",
      tonie_id: "t1",
      replace: false,
      remote_chapters: [{ id: "c1", title: "Old chapter" }],
      sources: [{ slug: "night-story", manifest_fingerprint: "f-night", files: ["01.mp3", "02.mp3"] }],
    }]);
    assert.equal(screen.node(".library-send-bar").hidden, true);
    assert.equal(screen.node(".library-select").checked, false);
    assert.equal(screen.focusKey(), "library-search");
  } finally {
    screen.stop();
  }
});

test("replacing everything asks first and only then sends", async () => {
  const screen = mountLibrary({ collections: [nightStory()] });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();

    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();

    await screen.byFocusKey("library-send-effect-1-replace").dispatchEvent({ type: "change" });
    await flush();
    assert.equal(screen.focusKey(), "library-send-effect-1-replace");
    assert.equal(screen.byFocusKey("library-send-effect-1-replace").checked, true);
    assert.equal(screen.byFocusKey("library-send-effect-1-append").checked, false);

    const sending = screen.node(".library-send-submit").click();
    await flush();
    const dialog = screen.dom.document.getElementById("dialogHost").querySelector(".confirmation-dialog");
    assert.match(dialog.textContent, /Blue Tonie · Home will lose every chapter currently stored/);
    assert.equal(screen.pushes.length, 0);
    await buttonWithText(dialog, "Replace and send").click();
    await sending;
    await flush();

    assert.equal(screen.pushes.length, 1);
    assert.equal(screen.pushes[0].assignments[0].replace, true);
    assert.equal(screen.node(".library-send-bar").hidden, true);
  } finally {
    screen.stop();
  }
});

test("declining the replacement dialog sends nothing and keeps the selection", async () => {
  const screen = mountLibrary({ collections: [nightStory()] });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();

    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();
    await screen.byFocusKey("library-send-effect-1-replace").dispatchEvent({ type: "change" });
    await flush();

    const sending = screen.node(".library-send-submit").click();
    await flush();
    const dialog = screen.dom.document.getElementById("dialogHost").querySelector(".confirmation-dialog");
    await buttonWithText(dialog, "Cancel").click();
    await sending;
    await flush();

    assert.equal(screen.pushes.length, 0);
    assert.equal(screen.node(".library-send-bar").hidden, false);
    assert.equal(screen.node(".library-select").checked, true);
    assert.equal(screen.node(".library-send-submit").disabled, false);
  } finally {
    screen.stop();
  }
});

test("a selection too long for one Tonie splits into groups that each need their own target", async () => {
  const long = nightStory();
  long.tracks = [
    { name: "01.mp3", title: "One", seconds: 3000, duration: "50m 00s" },
    { name: "02.mp3", title: "Two", seconds: 3000, duration: "50m 00s" },
  ];
  const screen = mountLibrary({ collections: [long] });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();

    const groups = screen.node(".library-send-groups").childNodes;
    assert.equal(groups.length, 2);
    assert.deepEqual(
      screen.dom.workspace.querySelectorAll("h3").map((item) => item.textContent),
      ["Group 1", "Group 2"],
    );
    assert.deepEqual(
      screen.dom.workspace.querySelectorAll(".library-send-target").map((item) => item.getAttribute("aria-label")),
      ["Creative Tonie for group 1", "Creative Tonie for group 2"],
    );
    assert.equal(screen.node(".library-send-submit").disabled, true);
  } finally {
    screen.stop();
  }
});
