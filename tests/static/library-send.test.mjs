import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { ApiError } from "../../app/static/api.js";
import { createLibraryScreen, createSelectionState, selectableCollections } from "../../app/static/library.js";

import { buttonWithText, flush, installDom } from "./mini-dom.mjs";

// The phone breakpoint appears more than once in the stylesheet, and every
// rule in it also exists at desktop width, so a rule is read out of the block
// that holds it rather than out of the file.
function phoneDeclarations(selector) {
  const styles = readFileSync(new URL("../../app/static/style.css", import.meta.url), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "");
  const query = "@media (max-width: 759.98px)";
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  for (let at = styles.indexOf(query); at >= 0; at = styles.indexOf(query, at + 1)) {
    const open = styles.indexOf("{", at);
    let depth = 0;
    for (let index = open; index < styles.length; index += 1) {
      if (styles[index] === "{") depth += 1;
      else if (styles[index] === "}" && (depth -= 1) === 0) {
        const block = styles.slice(open + 1, index);
        const body = block.match(new RegExp(`${escaped}\\s*\\{([^{}]*)\\}`))?.[1];
        if (body) {
          return Object.fromEntries(body.split(";").flatMap((declaration) => {
            const separator = declaration.indexOf(":");
            if (separator < 0) return [];
            return [[declaration.slice(0, separator).trim(), declaration.slice(separator + 1).trim()]];
          }));
        }
        break;
      }
    }
  }
  return {};
}

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

const dawnStory = () => ({
  slug: "dawn-story",
  stage: "forged",
  title: "Dawn Story",
  manifest_fingerprint: "f-dawn",
  track_count: 1,
  total_duration: "10m 00s",
  tonies_needed: 1,
  tracks: [{ name: "dawn.mp3", title: "Dawn", seconds: 600, duration: "10m 00s" }],
});

const filledTonie = () => ({
  ...blueTonie(),
  time_free: "20m 00s",
  seconds_present: 4200,
  chapters: [{ id: "c9", title: "Added from the phone" }],
});

// toniesQueue answers each /api/tonies call in turn, holding on the last entry,
// so a test can move a Tonie underneath a chosen target. pushOutcomes answers
// each POST /api/push/batch in turn: an Error rejects, anything else succeeds.
// Either may hold a function, which is called for its answer instead, so a
// test can hold one response open while a later one lands.
function mountLibrary({
  collections,
  tonies = [blueTonie()],
  toniesQueue = null,
  pushOutcomes = [],
  limitSeconds = 5400,
}) {
  const dom = installDom();
  const controller = new AbortController();
  const pushes = [];
  let tonieCalls = 0;
  const refresh = {
    snapshot: { status: { usable_limit_seconds: limitSeconds }, collections, jobs: [], stale: [], errors: {} },
    subscribe: () => () => {},
    request: async () => refresh.snapshot,
  };
  const request = async (url, options = {}) => {
    if (url === "/api/tonies") {
      if (!toniesQueue) return tonies;
      const answer = toniesQueue[Math.min(tonieCalls, toniesQueue.length - 1)];
      tonieCalls += 1;
      return typeof answer === "function" ? answer() : answer;
    }
    if (url === "/api/push/batch") {
      pushes.push(JSON.parse(options.body));
      const outcome = pushOutcomes[pushes.length - 1];
      if (typeof outcome === "function") return outcome();
      if (outcome instanceof Error) throw outcome;
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
    assert.match(screen.node(".library-send-target").textContent, /Blue Tonie · Home · 1h 30m free/);
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
    // The dialog now identifies its target by figure and name in a subject
    // list rather than in prose, because two Creative Tonies with the same
    // name read identically in a sentence.
    assert.match(dialog.textContent, /will be lost, and this cannot be undone/);
    assert.match(dialog.querySelector(".dialog-subjects").textContent, /Blue Tonie · Home/);
    assert.ok(dialog.querySelector(".dialog-subject-jacket"), "the dialog must show the figure");
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

test("adding a story to the selection clears the chosen target and never reuses the operation key", async () => {
  const screen = mountLibrary({
    collections: [nightStory(), dawnStory()],
    pushOutcomes: [new Error("The Tonie Cloud refused the batch.")],
  });
  try {
    await flush();
    await screen.dom.workspace.querySelectorAll(".library-select")[0].dispatchEvent({ type: "change" });
    await flush();
    let picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();
    assert.deepEqual(screen.node(".library-send-target").childNodes.map((option) => option.selected), [false, true]);

    // A refused send keeps its operation key so a retry of the identical
    // payload is recognised rather than duplicated.
    await screen.node(".library-send-submit").click();
    await flush();
    assert.equal(screen.pushes.length, 1);
    const refusedKey = screen.pushes[0].operation_key;

    // Ticking a second story rebuilds the payload, so the target and the key
    // that described the old one must both go.
    await screen.dom.workspace.querySelectorAll(".library-select")[1].dispatchEvent({ type: "change" });
    await flush();
    picker = screen.node(".library-send-target");
    assert.deepEqual(picker.childNodes.map((option) => option.selected), [false, false]);
    assert.equal(screen.node(".library-send-submit").disabled, true);
    assert.match(screen.node(".library-send-validation").textContent, /Group 1 has no Creative Tonie chosen/);
    assert.equal(screen.node(".library-send-groups").childNodes.length, 1);
    assert.equal(screen.node(".library-send-membership").childNodes.length, 3);

    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();
    await screen.node(".library-send-submit").click();
    await flush();

    assert.equal(screen.pushes.length, 2);
    assert.notEqual(screen.pushes[1].operation_key, refusedKey);
    assert.deepEqual(
      screen.pushes[1].assignments[0].sources,
      [
        { slug: "night-story", manifest_fingerprint: "f-night", files: ["01.mp3", "02.mp3"] },
        { slug: "dawn-story", manifest_fingerprint: "f-dawn", files: ["dawn.mp3"] },
      ],
    );
  } finally {
    screen.stop();
  }
});

test("Refresh targets rebinds the chosen Tonie to the freshly fetched one", async () => {
  const screen = mountLibrary({
    collections: [nightStory()],
    toniesQueue: [[{ ...blueTonie(), chapters: [] }], [filledTonie()]],
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();
    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();
    assert.equal(screen.node(".library-send-submit").disabled, false);

    // The Tonie filled up elsewhere while the selection sat here.
    await screen.node(".library-send-refresh").click();
    await flush();

    assert.deepEqual(screen.node(".library-send-target").childNodes.map((option) => option.selected), [false, true]);
    assert.equal(screen.node(".library-send-submit").disabled, true);
    assert.match(screen.node(".library-send-validation").textContent, /does not fit Blue Tonie · Home/);

    await screen.byFocusKey("library-send-effect-1-replace").dispatchEvent({ type: "change" });
    await flush();
    assert.equal(screen.node(".library-send-submit").disabled, false);

    const sending = screen.node(".library-send-submit").click();
    await flush();
    const dialog = screen.dom.document.getElementById("dialogHost").querySelector(".confirmation-dialog");
    await buttonWithText(dialog, "Replace and send").click();
    await sending;
    await flush();

    assert.equal(screen.pushes.length, 1);
    // The precondition has to describe the Tonie as it is now, not as it was
    // when the target was chosen.
    assert.deepEqual(screen.pushes[0].assignments[0].remote_chapters, [{ id: "c9", title: "Added from the phone" }]);
    assert.equal(screen.pushes[0].assignments[0].replace, true);
  } finally {
    screen.stop();
  }
});

test("a failed target refresh leaves the chosen target selected and the operation key intact", async () => {
  const screen = mountLibrary({
    collections: [nightStory()],
    toniesQueue: [
      [blueTonie()],
      // The refresh request itself never lands: a lost connection, not a
      // decision the operator made.
      () => { throw new Error("The Tonie Cloud could not be reached."); },
    ],
    pushOutcomes: [new Error("The Tonie Cloud refused the batch.")],
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();
    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();

    await screen.node(".library-send-submit").click();
    await flush();
    assert.equal(screen.pushes.length, 1);
    const firstKey = screen.pushes[0].operation_key;
    assert.ok(firstKey.length > 0);

    await screen.node(".library-send-refresh").click();
    await flush();

    // A failed refresh must not read as "that Tonie is gone": the picker
    // still shows the operator's choice and Send is not blocked by it.
    assert.match(screen.node(".library-send-validation").textContent, /Creative Tonies could not refresh/);
    assert.deepEqual(screen.node(".library-send-target").childNodes.map((option) => option.selected), [false, true]);
    assert.equal(screen.node(".library-send-submit").disabled, false);

    await screen.node(".library-send-submit").click();
    await flush();

    assert.equal(screen.pushes.length, 2);
    assert.equal(screen.pushes[1].operation_key, firstKey);
  } finally {
    screen.stop();
  }
});

test("a rejection with an empty message is still a failure: the chosen target and operation key survive a retry", async () => {
  // A 503 body of {"detail": []} joins to an empty string, so the ApiError
  // that reaches loadTargets can carry no text at all. Deciding failure from
  // whether error.message is truthy would misread this rejection as success.
  const screen = mountLibrary({
    collections: [nightStory()],
    toniesQueue: [
      [blueTonie()],
      () => { throw new ApiError("", { status: 503 }); },
    ],
    pushOutcomes: [new Error("The Tonie Cloud refused the batch.")],
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();
    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();

    await screen.node(".library-send-submit").click();
    await flush();
    assert.equal(screen.pushes.length, 1);
    const firstKey = screen.pushes[0].operation_key;
    assert.ok(firstKey.length > 0);

    await screen.node(".library-send-refresh").click();
    await flush();

    // The empty-message rejection must still read as a failure: the picker
    // keeps the operator's choice and Send is not blocked by it.
    assert.deepEqual(screen.node(".library-send-target").childNodes.map((option) => option.selected), [false, true]);
    assert.equal(screen.node(".library-send-submit").disabled, false);

    await screen.node(".library-send-submit").click();
    await flush();

    assert.equal(screen.pushes.length, 2);
    assert.equal(screen.pushes[1].operation_key, firstKey);
  } finally {
    screen.stop();
  }
});

test("a rejection with an empty message still shows non-empty text in the validation bar", async () => {
  const screen = mountLibrary({
    collections: [nightStory()],
    toniesQueue: [
      [blueTonie()],
      () => { throw new ApiError("", { status: 503 }); },
    ],
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();
    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();

    await screen.node(".library-send-refresh").click();
    await flush();

    const text = screen.node(".library-send-validation").textContent;
    assert.match(text, /Creative Tonies could not refresh\. \S/);
    assert.equal(screen.dom.spoken.at(-1), text);
  } finally {
    screen.stop();
  }
});

test("a lost response, a failed refresh, then a landed refresh still resend under the original key", async () => {
  // The exact reproduction behind the finding: an append that landed but
  // whose response was lost, one failed target refresh, then one refresh
  // that lands and shows the appended chapter. The bug read the failed
  // refresh as the Tonie vanishing, emptied the picker, and reselecting it
  // cleared the key, so a retry could append the same audio a second time
  // instead of getting the safe 409.
  const screen = mountLibrary({
    collections: [nightStory()],
    toniesQueue: [
      [blueTonie()],
      () => { throw new Error("The Tonie Cloud could not be reached."); },
      // This is the refresh that lands, and it shows the append the
      // lost-response send already made.
      [{ ...blueTonie(), chapters: [{ id: "c1", title: "Old chapter" }, { id: "c2", title: "Night Story" }] }],
    ],
    pushOutcomes: [new Error("The connection dropped before the receipt arrived.")],
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();
    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();

    // The server committed this one; only the receipt was lost.
    await screen.node(".library-send-submit").click();
    await flush();
    assert.equal(screen.pushes.length, 1);
    const originalKey = screen.pushes[0].operation_key;
    assert.deepEqual(screen.pushes[0].assignments[0].remote_chapters, [{ id: "c1", title: "Old chapter" }]);

    // The operator's first attempt to see what happened drops off the network.
    await screen.node(".library-send-refresh").click();
    await flush();
    assert.deepEqual(screen.node(".library-send-target").childNodes.map((option) => option.selected), [false, true]);

    // The second attempt lands and reveals the append.
    await screen.node(".library-send-refresh").click();
    await flush();
    assert.deepEqual(screen.node(".library-send-target").childNodes.map((option) => option.selected), [false, true]);

    await screen.node(".library-send-submit").click();
    await flush();

    assert.equal(screen.pushes.length, 2);
    // The ORIGINAL key: the server's idempotency digest sees it with a
    // payload that moved and answers 409 instead of appending twice.
    assert.equal(screen.pushes[1].operation_key, originalKey);
    assert.ok(screen.pushes[1].operation_key.length > 0);
    assert.deepEqual(screen.pushes[1].assignments[0].remote_chapters, [
      { id: "c1", title: "Old chapter" },
      { id: "c2", title: "Night Story" },
    ]);
  } finally {
    screen.stop();
  }
});

test("a successful refresh that no longer offers the chosen Tonie still clears that target", async () => {
  // rebindTargets is right to null a target a SUCCESSFUL refresh does not
  // offer; the fix for a failed refresh must not take that away.
  const screen = mountLibrary({
    collections: [nightStory()],
    toniesQueue: [[blueTonie()], [{ ...blueTonie(), id: "t2" }]],
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();
    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();
    assert.equal(screen.node(".library-send-submit").disabled, false);

    await screen.node(".library-send-refresh").click();
    await flush();

    assert.deepEqual(screen.node(".library-send-target").childNodes.map((option) => option.selected), [false, false]);
    assert.equal(screen.node(".library-send-submit").disabled, true);
    assert.match(screen.node(".library-send-validation").textContent, /Group 1 has no Creative Tonie chosen/);
  } finally {
    screen.stop();
  }
});

test("a refused send leaves the selection ticked and ready to try again", async () => {
  const screen = mountLibrary({
    collections: [nightStory()],
    pushOutcomes: [new Error("The Tonie Cloud refused the batch.")],
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();
    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();

    await screen.node(".library-send-submit").click();
    await flush();

    assert.equal(screen.pushes.length, 1);
    assert.equal(screen.node(".library-send-bar").hidden, false);
    assert.equal(screen.node(".library-select").checked, true);
    assert.equal(screen.node(".library-send-groups").childNodes.length, 1);
    assert.equal(screen.node(".library-send-membership").childNodes.length, 2);
    assert.deepEqual(screen.node(".library-send-target").childNodes.map((option) => option.selected), [false, true]);
    assert.equal(screen.node(".library-send-submit").disabled, false);
    assert.equal(screen.focusKey(), "library-send-submit");
  } finally {
    screen.stop();
  }
});

test("the bar speaks each new problem once, and says nothing on an unrelated render", async () => {
  const screen = mountLibrary({ collections: [nightStory()] });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();
    assert.deepEqual(screen.dom.spoken, [
      "Creative Tonies are not loaded yet.",
      "Group 1 has no Creative Tonie chosen.",
    ]);

    const search = screen.node("input");
    search.value = "Night";
    await search.dispatchEvent({ type: "input" });
    await flush();
    assert.equal(screen.dom.spoken.length, 2);

    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();
    assert.equal(screen.dom.spoken.length, 2);
  } finally {
    screen.stop();
  }
});

const staleTarget = () => ({
  id: "t9",
  householdId: "h1",
  householdName: "Home",
  name: "Stale Target",
  time_free: "90m 00s",
  seconds_present: 0,
  chapters: [{ id: "s1", title: "Stale chapter" }],
});

const freshTarget = () => ({
  id: "t8",
  householdId: "h1",
  householdName: "Home",
  name: "Fresh Target",
  time_free: "80m 00s",
  seconds_present: 600,
  chapters: [{ id: "f1", title: "Fresh chapter" }],
});

test("a send in flight freezes the selection instead of discarding a late tick", async () => {
  let release = () => {};
  const gate = new Promise((resolve) => { release = resolve; });
  const screen = mountLibrary({
    collections: [nightStory(), dawnStory()],
    pushOutcomes: [async () => { await gate; return { job_id: 7 }; }],
  });
  try {
    await flush();
    await screen.dom.workspace.querySelectorAll(".library-select")[0].dispatchEvent({ type: "change" });
    await flush();
    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();

    const sending = screen.node(".library-send-submit").click();
    await flush();

    // Every row checkbox is frozen for the duration, the same as Send and Clear.
    assert.deepEqual(
      screen.dom.workspace.querySelectorAll(".library-select").map((tick) => tick.disabled),
      [true, true],
    );
    assert.equal(screen.node(".library-send-submit").disabled, true);
    assert.equal(screen.node(".library-send-clear").disabled, true);

    // A change forced through anyway must not reach the selection.
    const second = screen.dom.workspace.querySelectorAll(".library-select")[1];
    second.checked = true;
    await second.dispatchEvent({ type: "change" });
    await flush();
    assert.match(screen.node(".library-send-bar").textContent, /1 story selected/);
    assert.equal(screen.node(".library-send-groups").childNodes.length, 1);
    assert.equal(screen.node(".library-send-membership").childNodes.length, 2);

    release();
    await sending;
    await flush();

    assert.equal(screen.pushes.length, 1);
    assert.deepEqual(
      screen.pushes[0].assignments[0].sources,
      [{ slug: "night-story", manifest_fingerprint: "f-night", files: ["01.mp3", "02.mp3"] }],
    );
    assert.equal(screen.node(".library-send-bar").hidden, true);
    assert.deepEqual(
      screen.dom.workspace.querySelectorAll(".library-select").map((tick) => [tick.checked, tick.disabled]),
      [[false, false], [false, false]],
    );
  } finally {
    screen.stop();
  }
});

test("a slow first target read cannot overwrite what Refresh targets already showed", async () => {
  let release = () => {};
  const gate = new Promise((resolve) => { release = resolve; });
  const screen = mountLibrary({
    collections: [nightStory()],
    toniesQueue: [async () => { await gate; return [staleTarget()]; }, () => [freshTarget()]],
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();
    assert.match(screen.node(".library-send-validation").textContent, /Creative Tonies are not loaded yet/);

    await screen.node(".library-send-refresh").click();
    await flush();
    assert.match(screen.node(".library-send-target").textContent, /Fresh Target · Home · 1h 20m free/);

    // The automatic read finally answers, with an obsolete free space and an
    // obsolete remote_chapters precondition. It is not the newest, so it lands
    // nowhere.
    release();
    await flush();
    await flush();

    const picker = screen.node(".library-send-target");
    assert.match(picker.textContent, /Fresh Target · Home · 1h 20m free/);
    assert.doesNotMatch(picker.textContent, /Stale Target/);
    assert.deepEqual(picker.childNodes.map((option) => option.getAttribute("value")), ["", "h1/t8"]);
  } finally {
    screen.stop();
  }
});

test("retrying an unchanged selection reuses the operation key the refused send used", async () => {
  const screen = mountLibrary({
    collections: [nightStory()],
    pushOutcomes: [new Error("The Tonie Cloud refused the batch.")],
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();
    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();

    await screen.node(".library-send-submit").click();
    await flush();
    // Nothing about the selection moved, so the second attempt is the same
    // operation and the server has to be able to recognise it as one.
    await screen.node(".library-send-submit").click();
    await flush();

    assert.equal(screen.pushes.length, 2);
    assert.equal(typeof screen.pushes[0].operation_key, "string");
    assert.ok(screen.pushes[0].operation_key.length > 0);
    assert.equal(screen.pushes[1].operation_key, screen.pushes[0].operation_key);
    assert.deepEqual(screen.pushes[1].assignments, screen.pushes[0].assignments);
  } finally {
    screen.stop();
  }
});

test("Refresh targets keeps the operation key when nothing in the payload moved", async () => {
  const screen = mountLibrary({
    collections: [nightStory()],
    // Two separate objects carrying identical data, which is what a refresh
    // answers with when nothing changed on the Tonie.
    toniesQueue: [[blueTonie()], [blueTonie()]],
    pushOutcomes: [new Error("The connection dropped before the receipt arrived.")],
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();
    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();

    // The server committed this one and the response was lost, so the operator
    // sees a failure and has no way to know a job already exists.
    await screen.node(".library-send-submit").click();
    await flush();
    assert.equal(screen.pushes.length, 1);

    await screen.node(".library-send-refresh").click();
    await flush();

    await screen.node(".library-send-submit").click();
    await flush();

    assert.equal(screen.pushes.length, 2);
    // Same key, so the server recognises the retry and hands back the first
    // job receipt instead of appending the same chapters a second time.
    assert.equal(screen.pushes[1].operation_key, screen.pushes[0].operation_key);
    assert.deepEqual(screen.pushes[1].assignments, screen.pushes[0].assignments);
  } finally {
    screen.stop();
  }
});

test("Refresh targets KEEPS the operation key when the Tonie gained a chapter", async () => {
  // This test was the exact reverse a round ago, asserting the key was DROPPED
  // here. That was the bug, not the fix, so do not restore it. The operation
  // key tracks the operator's INTENT, and a target refresh reports what the
  // world did rather than what the operator decided.
  //
  // Read the scenario below: the first send LANDED and its response was lost,
  // so the chapter this refresh reveals is the one that send just appended.
  // Dropping the key there makes the next Send a brand new operation carrying
  // [Old, Story] as its precondition, and the Tonie ends up [Old, Story,
  // Story]. A Tonie write has no undo, so that duplicate cannot be taken back.
  // Keeping the key hands the moved payload back under the same key: the
  // server's idempotency digest sees the same key with a different payload,
  // raises OperationConflict, answers 409, and tells the operator the
  // situation moved instead of uploading the same audio twice.
  const screen = mountLibrary({
    collections: [nightStory()],
    toniesQueue: [
      [blueTonie()],
      // The refresh shows the append the lost-response send already made.
      [{ ...blueTonie(), chapters: [{ id: "c1", title: "Old chapter" }, { id: "c2", title: "Night Story" }] }],
    ],
    pushOutcomes: [new Error("The connection dropped before the receipt arrived.")],
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();
    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();

    // The server committed this one; only the receipt was lost. The operator
    // cannot tell whether it landed.
    await screen.node(".library-send-submit").click();
    await flush();
    assert.equal(screen.pushes.length, 1);
    assert.deepEqual(screen.pushes[0].assignments[0].remote_chapters, [{ id: "c1", title: "Old chapter" }]);

    await screen.node(".library-send-refresh").click();
    await flush();

    await screen.node(".library-send-submit").click();
    await flush();

    assert.equal(screen.pushes.length, 2);
    // The ORIGINAL key, so the server can refuse rather than duplicate.
    assert.equal(screen.pushes[1].operation_key, screen.pushes[0].operation_key);
    assert.ok(screen.pushes[1].operation_key.length > 0);
    // The precondition did move, which is what makes the server answer 409
    // under that same key rather than creating a second job.
    assert.deepEqual(screen.pushes[1].assignments[0].remote_chapters, [
      { id: "c1", title: "Old chapter" },
      { id: "c2", title: "Night Story" },
    ]);
  } finally {
    screen.stop();
  }
});

test("Refresh targets keeps the operation key when someone else edited the Tonie", async () => {
  // The third case of the four. The operator did not decide anything here
  // either, so the key survives and the server refuses the moved payload under
  // it. Same mechanism as the lost-response case, and safe for the same
  // reason: a 409 is recoverable, a second upload is not.
  const screen = mountLibrary({
    collections: [nightStory()],
    toniesQueue: [
      [blueTonie()],
      [{ ...blueTonie(), chapters: [{ id: "c1", title: "Old chapter" }, { id: "c2", title: "Added from the phone" }] }],
    ],
    pushOutcomes: [new Error("The Tonie Cloud refused the batch.")],
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();
    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();

    await screen.node(".library-send-submit").click();
    await flush();

    await screen.node(".library-send-refresh").click();
    await flush();

    await screen.node(".library-send-submit").click();
    await flush();

    assert.equal(screen.pushes.length, 2);
    assert.equal(screen.pushes[1].operation_key, screen.pushes[0].operation_key);
  } finally {
    screen.stop();
  }
});

test("changing the effect after a refresh is a new operation and takes a new key", async () => {
  // The fourth case: the operator's own action. Only this clears the key, and
  // it still clears it after a refresh has been through, so keeping the key
  // across a refresh never strands a genuinely different operation on an old
  // one.
  const screen = mountLibrary({
    collections: [nightStory()],
    pushOutcomes: [new Error("The Tonie Cloud refused the batch.")],
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();
    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();

    await screen.node(".library-send-submit").click();
    await flush();

    await screen.node(".library-send-refresh").click();
    await flush();

    await screen.byFocusKey("library-send-effect-1-replace").dispatchEvent({ type: "change" });
    await flush();

    const sending = screen.node(".library-send-submit").click();
    await flush();
    const dialog = screen.dom.document.getElementById("dialogHost").querySelector(".confirmation-dialog");
    await buttonWithText(dialog, "Replace and send").click();
    await sending;
    await flush();

    assert.equal(screen.pushes.length, 2);
    assert.notEqual(screen.pushes[1].operation_key, screen.pushes[0].operation_key);
    assert.equal(screen.pushes[1].assignments[0].replace, true);
  } finally {
    screen.stop();
  }
});

test("an option never advertises free space the fit check will refuse", async () => {
  const oversized = nightStory();
  oversized.tracks = [{ name: "long.mp3", title: "Long", seconds: 5371, duration: "1h 30m" }];
  const screen = mountLibrary({
    collections: [oversized],
    // time_free is set deliberately stale here, one headroom above what the
    // fit check itself computes from limitSeconds() and secondsPresent, so
    // this test can prove the picker's printed figure never just trusts the
    // tonie's own snapshot.
    tonies: [{ ...blueTonie(), time_free: "1h 30m", seconds_present: 0 }],
    limitSeconds: 5370,
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();

    const picker = screen.node(".library-send-target");
    assert.match(picker.textContent, /Blue Tonie · Home · 1h 29m free · does not fit/);
    assert.doesNotMatch(picker.textContent, /1h 30m/);
    assert.equal(screen.node(".library-send-submit").disabled, true);
  } finally {
    screen.stop();
  }
});

test("an option reports the Tonie's own free space under either effect", async () => {
  // 5,000 seconds of a 5,400 second limit are already on the box, so it has
  // 6m 40s free. That is a fact about the Tonie, so ticking Replace everything
  // must not turn it into "1h 30m free": the operator would be reading the
  // operation's budget and calling it the box's remaining space.
  const screen = mountLibrary({
    collections: [nightStory()],
    tonies: [{ ...blueTonie(), time_free: "6m 40s", seconds_present: 5000 }],
    limitSeconds: 5400,
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();

    // Append: 25m does not fit into 6m 40s, and the option says both.
    assert.match(screen.node(".library-send-target").textContent, /Blue Tonie · Home · 6m 40s free · does not fit/);

    await screen.byFocusKey("library-send-effect-1-replace").dispatchEvent({ type: "change" });
    await flush();

    // Replace: the SAME free figure, with the suffix explaining why a group
    // bigger than it is still accepted, rather than reading as a contradiction.
    assert.match(screen.node(".library-send-target").textContent, /Blue Tonie · Home · 6m 40s free · fits once everything is replaced/);
    assert.doesNotMatch(screen.node(".library-send-target").textContent, /1h 30m/);
  } finally {
    screen.stop();
  }
});

test("a replace of a group larger than the free figure still sends", async () => {
  const screen = mountLibrary({
    collections: [nightStory()],
    tonies: [{ ...blueTonie(), time_free: "6m 40s", seconds_present: 5000 }],
    limitSeconds: 5400,
  });
  try {
    await flush();
    await screen.node(".library-select").dispatchEvent({ type: "change" });
    await flush();
    const picker = screen.node(".library-send-target");
    picker.value = "h1/t1";
    await picker.dispatchEvent({ type: "change" });
    await flush();

    // The append is refused, which is what the printed free space describes.
    assert.equal(screen.node(".library-send-submit").disabled, true);
    assert.match(screen.node(".library-send-validation").textContent, /Group 1 does not fit Blue Tonie · Home\. Choose Replace everything, or another Tonie\./);

    await screen.byFocusKey("library-send-effect-1-replace").dispatchEvent({ type: "change" });
    await flush();

    // The fit budget for a replace is the whole usable limit, so the same 25m
    // group now fits and the bar has nothing to complain about.
    assert.equal(screen.node(".library-send-submit").disabled, false);
    assert.equal(screen.node(".library-send-validation").textContent, "");

    const sending = screen.node(".library-send-submit").click();
    await flush();
    const dialog = screen.dom.document.getElementById("dialogHost").querySelector(".confirmation-dialog");
    await buttonWithText(dialog, "Replace and send").click();
    await sending;
    await flush();

    assert.equal(screen.pushes.length, 1);
    assert.equal(screen.pushes[0].assignments[0].replace, true);
  } finally {
    screen.stop();
  }
});


test("the phone send bar leaves the collections behind it on screen", () => {
  // Measured at 390x844 before this cap: the bar stood 756px tall, hid every
  // row it was sending, and ended 32px below the top of the fixed navigation,
  // so the last of Send sat behind it. A bar that tall is also pinned against
  // the top of its containing block, where the sticky bottom offset has no
  // room left to lift it clear.
  const bar = phoneDeclarations(".library-send-bar");

  assert.equal(bar["max-height"], "55svh");
  assert.equal(bar.bottom, "calc(4.5rem + env(safe-area-inset-bottom))");
});

test("only the chapter list gives way when the phone send bar runs out of room", () => {
  // The target field and the send buttons are the point of the bar, so the
  // list of what is being sent is the one part allowed to shrink and scroll.
  assert.equal(phoneDeclarations(".library-send-bar > *").flex, "0 0 auto");

  const groups = phoneDeclarations(".library-send-groups");

  assert.equal(groups.flex, "1 1 auto");
  assert.equal(groups["min-height"], "0");
  assert.equal(groups["overflow-y"], "auto");
});
