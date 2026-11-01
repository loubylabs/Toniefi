import assert from "node:assert/strict";
import test from "node:test";

import { createLibraryScreen } from "../../app/static/library.js";
import { problemSummary } from "../../app/static/send.js";

import { flush, installDom } from "./mini-dom.mjs";

const story = (slug, trackCount = 2) => ({
  slug,
  stage: "forged",
  title: `Story ${slug}`,
  manifest_fingerprint: `f-${slug}`,
  track_count: trackCount,
  total_duration: "10m 00s",
  tonies_needed: 1,
  tracks: Array.from({ length: trackCount }, (_, index) => ({
    name: `${slug}-${index + 1}.mp3`,
    title: `${slug} ${index + 1}`,
    seconds: 300,
  })),
});

const tonie = (id) => ({
  id,
  householdId: "h1",
  name: `Tonie ${id}`,
  imageUrl: "",
  chapters: [],
  chapter_count: 0,
  seconds_present: 0,
});

function viewport(matches) {
  const listeners = new Set();
  return {
    matches,
    addEventListener: (_, listener) => listeners.add(listener),
    removeEventListener: (_, listener) => listeners.delete(listener),
    resize(next) {
      this.matches = next;
      for (const listener of listeners) listener({ matches: next });
    },
    get listening() { return listeners.size; },
  };
}

function mount({ collections, tonies = [tonie("a")], phone = true, pushAnswer = { job_ids: [7] }, context = {} }) {
  const dom = installDom();
  const nav = dom.document.createElement("nav");
  nav.className = "mobile-navigation";
  dom.document.body.append(nav);
  const media = viewport(phone);
  globalThis.window.matchMedia = () => media;
  const controller = new AbortController();
  const pushes = [];
  const listeners = new Set();
  const refresh = {
    snapshot: { status: { usable_limit_seconds: 5400 }, collections, jobs: [], stale: [], errors: {} },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    request: async () => {
      for (const listener of listeners) listener(refresh.snapshot);
      return refresh.snapshot;
    },
  };
  const request = async (url, options = {}) => {
    if (url === "/api/tonies") return tonies;
    if (url === "/api/push/batch") {
      pushes.push(JSON.parse(options.body));
      return pushAnswer;
    }
    throw new Error(`Unexpected request ${url}`);
  };
  const cleanup = createLibraryScreen({ request, refresh })({
    workspace: dom.workspace,
    navigate() {},
    signal: controller.signal,
    ...context,
  });
  let stopped = false;
  const leave = () => {
    if (stopped) return;
    stopped = true;
    cleanup();
    controller.abort();
  };
  return {
    dom,
    nav,
    media,
    pushes,
    refresh,
    leave,
    node: (selector) => dom.workspace.querySelector(selector),
    all: (selector) => dom.workspace.querySelectorAll(selector),
    focusKey: () => dom.document.activeElement?.getAttribute("data-focus-key") || "",
    poll: () => refresh.request(),
    press: (key) => dom.document.dispatchEvent({ type: "keydown", key }),
    stop() {
      leave();
      dom.restore();
    },
  };
}

async function tickFirst(screen) {
  await screen.node(".library-select").dispatchEvent({ type: "change" });
  await flush();
}

const sheet = (screen) => screen.node(".library-send-bar").getAttribute("data-sheet");

test("a phone keeps the bar closed behind a dock until Choose Tonies", async () => {
  const screen = mount({ collections: [story("a")] });
  try {
    await flush();
    assert.equal(screen.node(".library-send-dock").hidden, true, "no selection, no dock");
    await tickFirst(screen);

    assert.equal(screen.node(".library-send-dock").hidden, false);
    assert.match(screen.node(".library-send-dock").textContent, /1 story · 10m 00s/);
    assert.equal(sheet(screen), null);
    assert.equal(screen.dom.document.body.classList.contains("library-sheet-open"), false);
  } finally {
    screen.stop();
  }
});

test("the open sheet survives a re-render and Esc closes it back to Choose Tonies", async () => {
  const screen = mount({ collections: [story("a")] });
  try {
    await flush();
    await tickFirst(screen);
    await screen.node(".library-send-choose").click();

    assert.equal(sheet(screen), "open");
    assert.equal(screen.node(".library-send-bar").getAttribute("role"), "dialog");
    assert.equal(screen.focusKey(), "library-send-close");
    assert.equal(screen.dom.document.body.classList.contains("library-sheet-open"), true);
    assert.equal(screen.nav.hasAttribute("inert"), true);
    assert.equal(screen.node(".library-list").hasAttribute("inert"), true);
    assert.equal(screen.node(".library-send-bar").hasAttribute("inert"), false);

    await screen.poll();
    await flush();
    assert.equal(sheet(screen), "open", "a refresh poll does not close it");
    assert.equal(screen.focusKey(), "library-send-close", "nor take focus out of it");

    screen.press("Escape");
    assert.equal(sheet(screen), null);
    assert.equal(screen.focusKey(), "library-send-choose");
    assert.equal(screen.dom.document.body.classList.contains("library-sheet-open"), false);
    assert.equal(screen.nav.hasAttribute("inert"), false);
    assert.equal(screen.node(".library-list").hasAttribute("inert"), false);
  } finally {
    screen.stop();
  }
});

test("Close does what Esc does", async () => {
  const screen = mount({ collections: [story("a")] });
  try {
    await flush();
    await tickFirst(screen);
    await screen.node(".library-send-choose").click();
    await screen.node(".library-send-close").click();

    assert.equal(sheet(screen), null);
    assert.equal(screen.focusKey(), "library-send-choose");
  } finally {
    screen.stop();
  }
});

test("no sheet at desktop width, even with the state saying open, and Esc does nothing there", async () => {
  const screen = mount({ collections: [story("a")] });
  try {
    await flush();
    await tickFirst(screen);
    await screen.node(".library-send-choose").click();
    assert.equal(sheet(screen), "open");

    screen.media.resize(false);
    assert.equal(sheet(screen), null);
    assert.equal(screen.node(".library-send-bar").getAttribute("role"), null);
    assert.equal(screen.dom.document.body.classList.contains("library-sheet-open"), false);
    assert.equal(screen.nav.hasAttribute("inert"), false);
    assert.equal(screen.node(".library-send-bar").hidden, false, "the desktop bar is what shows now");
    screen.press("Escape");
    assert.equal(screen.node(".library-send-bar").hidden, false);

    screen.media.resize(true);
    assert.equal(sheet(screen), "open", "turning the phone back returns to the same place");
  } finally {
    screen.stop();
  }
});

test("leaving the route undoes the lock and the inert", async () => {
  const screen = mount({ collections: [story("a")] });
  try {
    await flush();
    await tickFirst(screen);
    await screen.node(".library-send-choose").click();
    assert.equal(screen.dom.document.body.classList.contains("library-sheet-open"), true);

    screen.leave();
    assert.equal(screen.dom.document.body.classList.contains("library-sheet-open"), false);
    assert.equal(screen.nav.hasAttribute("inert"), false);
    assert.equal(screen.media.listening, 0);
  } finally {
    screen.stop();
  }
});

test("clearing the selection ends the sheet without raising the keyboard", async () => {
  const screen = mount({ collections: [story("a")] });
  try {
    await flush();
    await tickFirst(screen);
    await screen.node(".library-send-choose").click();
    await screen.node(".library-send-clear").click();

    assert.equal(sheet(screen), null);
    assert.equal(screen.node(".library-send-dock").hidden, true);
    assert.equal(screen.dom.document.body.classList.contains("library-sheet-open"), false);
    assert.equal(screen.focusKey(), "library-summary");
  } finally {
    screen.stop();
  }
});

test("a phone sums many problems in one line and keeps the list inside it, open across a poll", async () => {
  const screen = mount({ collections: [story("a", 40)], tonies: [tonie("a"), tonie("b")] });
  try {
    await flush();
    await tickFirst(screen);
    await screen.node(".library-send-choose").click();
    await flush();

    const details = screen.node(".library-send-problems");
    assert.equal(details.querySelector("summary").textContent, "3 of 3 groups need a Tonie");
    assert.equal(details.querySelectorAll("li").length, 3, "every reason is still there");
    assert.equal(screen.node(".library-send-submit").disabled, true);

    details.setAttribute("open", "");
    details.open = true;
    await details.dispatchEvent({ type: "toggle", target: details });
    await screen.poll();
    assert.equal(screen.node(".library-send-problems").hasAttribute("open"), true);
  } finally {
    screen.stop();
  }
});

test("an open chapter membership list survives a poll", async () => {
  const screen = mount({ collections: [story("a")] });
  try {
    await flush();
    await tickFirst(screen);
    const details = screen.node(".library-send-membership-disclosure");
    details.open = true;
    await details.dispatchEvent({ type: "toggle", target: details });
    await screen.poll();
    assert.equal(screen.node(".library-send-membership-disclosure").hasAttribute("open"), true);
  } finally {
    screen.stop();
  }
});

test("a desktop keeps the flat validation list", async () => {
  const screen = mount({ collections: [story("a", 40)], phone: false, tonies: [tonie("a")] });
  try {
    await flush();
    await tickFirst(screen);
    assert.equal(screen.node(".library-send-problems"), null);
    assert.equal(screen.node(".library-send-validation").querySelectorAll("li").length, 3);
  } finally {
    screen.stop();
  }
});

test("the receipt shows in the sheet, and Done ends it", async () => {
  const screen = mount({ collections: [story("a")] });
  try {
    await flush();
    await tickFirst(screen);
    await screen.node(".library-send-choose").click();
    const target = screen.node(".library-send-target");
    target.checked = true;
    await target.dispatchEvent({ type: "change" });
    await screen.node(".library-send-submit").click();
    await flush();

    assert.equal(screen.pushes.length, 1);
    assert.equal(sheet(screen), "open");
    assert.ok(screen.node(".library-receipt"));
    assert.equal(screen.focusKey(), "library-send-done");

    await screen.poll();
    assert.equal(sheet(screen), "open");

    await screen.all("button").find((button) => button.getAttribute("data-focus-key") === "library-send-done").click();
    assert.equal(sheet(screen), null);
    assert.equal(screen.node(".library-receipt"), null);
    assert.equal(screen.dom.document.body.classList.contains("library-sheet-open"), false);
    assert.equal(screen.node(".library-send-dock").hidden, true);
    assert.equal(screen.focusKey(), "library-summary");
  } finally {
    screen.stop();
  }
});

test("Esc on a receipt ends it the way Done does", async () => {
  const screen = mount({ collections: [story("a")] });
  try {
    await flush();
    await tickFirst(screen);
    await screen.node(".library-send-choose").click();
    const target = screen.node(".library-send-target");
    target.checked = true;
    await target.dispatchEvent({ type: "change" });
    await screen.node(".library-send-submit").click();
    await flush();

    screen.press("Escape");
    assert.equal(sheet(screen), null);
    assert.equal(screen.node(".library-receipt"), null);
  } finally {
    screen.stop();
  }
});

test("Esc is ignored while a send is in flight", async () => {
  let land = () => {};
  const screen = mount({
    collections: [story("a")],
    pushAnswer: new Promise((resolve) => { land = resolve; }),
  });
  try {
    await flush();
    await tickFirst(screen);
    await screen.node(".library-send-choose").click();
    const target = screen.node(".library-send-target");
    target.checked = true;
    await target.dispatchEvent({ type: "change" });
    const sending = screen.node(".library-send-submit").click();
    await flush();

    screen.press("Escape");
    assert.equal(sheet(screen), "open");

    land({ job_ids: [7] });
    await sending;
    await flush();
    assert.ok(screen.node(".library-receipt"), "and the result lands in the sheet");
  } finally {
    screen.stop();
  }
});

test("problemSummary counts what selectionProblems returned", () => {
  const groups = [{ index: 1 }, { index: 2 }, { index: 3 }];
  const picked = { tonie: tonie("a") };

  assert.equal(problemSummary(groups, [null, null, null], ["x", "y", "z"]), "3 of 3 groups need a Tonie");
  assert.equal(problemSummary(groups, [picked, null, null], ["x", "y"]), "2 of 3 groups need a Tonie");
  assert.equal(problemSummary(groups, [picked, null, picked], ["x", "y"]), "1 of 3 groups needs a Tonie · 1 other problem");
  assert.equal(problemSummary(groups, [picked, picked, picked], ["x", "y"]), "2 problems to fix before sending");
  assert.equal(problemSummary(groups, [picked, picked, picked], ["only this"]), "only this");
  assert.equal(problemSummary(groups, [picked, picked, picked], []), "");
});

test("More opens one row at a time, and survives a re-render", async () => {
  const screen = mount({ collections: [story("a"), story("b")] });
  try {
    await flush();
    const row = (index) => screen.all(".library-row")[index];
    const more = (index) => row(index).querySelector(".library-row-more");
    assert.equal(more(0).getAttribute("aria-label"), "More actions for Story a");
    assert.equal(more(0).getAttribute("aria-expanded"), "false");
    assert.equal(more(0).getAttribute("aria-controls"), row(0).querySelector(".library-row-actions").id);
    assert.equal(row(0).hasAttribute("data-more-open"), false);

    await more(0).click();
    assert.equal(row(0).hasAttribute("data-more-open"), true);
    assert.equal(more(0).getAttribute("aria-expanded"), "true");
    assert.equal(screen.focusKey(), "library-a-more");

    await screen.poll();
    await flush();
    assert.equal(row(0).hasAttribute("data-more-open"), true, "a poll rebuilds the list, not the state");
    assert.equal(screen.focusKey(), "library-a-more");

    await more(1).click();
    assert.equal(row(0).hasAttribute("data-more-open"), false, "one open at a time");
    assert.equal(row(1).hasAttribute("data-more-open"), true);

    await more(1).click();
    assert.equal(row(1).hasAttribute("data-more-open"), false);
  } finally {
    screen.stop();
  }
});

test("a ready title links to its story and a story that is not ready keeps plain text", async () => {
  const raw = { slug: "raw", stage: "extracted", title: "Raw Story", track_count: 1 };
  const screen = mount({ collections: [story("a"), raw] });
  try {
    await flush();
    const [ready, notReady] = screen.all(".library-row");
    const link = ready.querySelector("h2").querySelector("a");
    assert.equal(link.getAttribute("href"), "/collection/a");
    assert.equal(link.getAttribute("data-route"), "collection");
    assert.equal(link.textContent, "Story a");
    assert.equal(notReady.querySelector("h2").querySelector("a"), null);
    assert.equal(notReady.querySelector("h2").textContent, "Raw Story");
    assert.ok(notReady.querySelector(".library-finish"));
  } finally {
    screen.stop();
  }
});

test("the row's brief is the one line a phone shows", async () => {
  const screen = mount({ collections: [{ ...story("a"), track_count: 223, total_duration: "28h 50m", tonies_needed: 23 }] });
  try {
    await flush();
    assert.equal(screen.node(".library-row-brief").textContent, "223 ch · 28h 50m · 23 Tonies");
  } finally {
    screen.stop();
  }
});
