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
    assert.equal(row(0).hasAttribute("data-more-fresh"), true, "the tap's own render eases in");
    assert.equal(screen.focusKey(), "library-a-more");

    await screen.poll();
    await flush();
    assert.equal(row(0).hasAttribute("data-more-open"), true, "a poll rebuilds the list, not the state");
    assert.equal(row(0).hasAttribute("data-more-fresh"), false, "a poll never replays the reveal");
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

const SELECTION_KEY = "toniefi.library.selection";
const SCROLL_KEY = "toniefi.library.scroll";

// A session that outlives each mounted screen, the way a browser tab does.
function withSession(run) {
  return async () => {
    const original = globalThis.sessionStorage;
    const values = new Map();
    globalThis.sessionStorage = {
      getItem: (key) => (values.has(key) ? values.get(key) : null),
      setItem: (key, value) => values.set(key, String(value)),
      removeItem: (key) => values.delete(key),
    };
    try {
      await run(values);
    } finally {
      if (original === undefined) delete globalThis.sessionStorage;
      else globalThis.sessionStorage = original;
    }
  };
}

const stored = (values) => JSON.parse(values.get(SELECTION_KEY) ?? "null");
const ticked = (screen) => screen.all(".library-select").filter((box) => box.checked).length;

test("the selection survives leaving and coming back, with no target and no key", withSession(async (values) => {
  const first = mount({ collections: [story("a"), story("b")], phone: false });
  await flush();
  await tickFirst(first);
  assert.deepEqual(stored(values), { a: { fingerprint: "f-a", names: ["a-1.mp3", "a-2.mp3"] } });
  first.stop();

  const second = mount({ collections: [story("a"), story("b")], phone: false });
  try {
    await flush();
    assert.equal(ticked(second), 1);
    assert.equal(second.node(".library-send-bar").hidden, false);
    assert.ok(second.all(".library-send-target").length, "targets load for a restored selection");
    assert.equal(second.all(".library-send-target").filter((input) => input.checked).length, 0, "nothing is aimed for them");
    assert.equal(second.node(".library-send-submit").disabled, true);
    assert.equal(values.get(SELECTION_KEY).includes("operation"), false);
    assert.equal(second.dom.document.activeElement, null, "a restore moves no focus and raises no keyboard");
  } finally {
    second.stop();
  }
}));

test("a partial selection comes back partial", withSession(async () => {
  const first = mount({ collections: [story("a", 3)] });
  await flush();
  await first.all("button").find((button) => button.getAttribute("data-focus-key") === "library-a-chapters").click();
  await first.node(".library-chapter-select").dispatchEvent({ type: "change" });
  await flush();
  first.stop();

  const second = mount({ collections: [story("a", 3)] });
  try {
    await flush();
    assert.equal(second.node(".library-chapter-count").textContent, "1 of 3 chapters selected");
    assert.match(second.node(".library-send-bar").textContent, /1 chapter from 1 story/);
  } finally {
    second.stop();
  }
}));

test("restore drops a deleted story, a changed story and a vanished chapter", withSession(async (values) => {
  values.set(SELECTION_KEY, JSON.stringify({
    deleted: { fingerprint: "f-deleted", names: ["deleted-1.mp3"] },
    changed: { fingerprint: "old", names: ["changed-1.mp3"] },
    trimmed: { fingerprint: "f-trimmed", names: ["trimmed-1.mp3", "gone.mp3"] },
    emptied: { fingerprint: "f-emptied", names: ["gone.mp3"] },
    raw: { fingerprint: "", names: ["raw-1.mp3"] },
  }));
  const screen = mount({
    collections: [
      story("changed"),
      story("trimmed"),
      story("emptied"),
      { slug: "raw", stage: "extracted", title: "Raw", tracks: [{ name: "raw-1.mp3", seconds: 60 }] },
    ],
  });
  try {
    await flush();
    assert.deepEqual(stored(values), { trimmed: { fingerprint: "f-trimmed", names: ["trimmed-1.mp3"] } });
    assert.match(screen.node(".library-send-bar").textContent, /1 chapter from 1 story/);
  } finally {
    screen.stop();
  }
}));

test("nothing restored and nothing written while the first list has not arrived", withSession(async (values) => {
  const saved = JSON.stringify({ a: { fingerprint: "f-a", names: ["a-1.mp3"] } });
  values.set(SELECTION_KEY, saved);
  // The real coordinator publishes once per resource, so the status answer
  // reaches the screen while the collection list is still the empty initial one.
  const screen = mount({ collections: [] });
  try {
    await screen.poll();
    assert.equal(values.get(SELECTION_KEY), saved, "an empty publish does not wipe the ticks");
    screen.refresh.snapshot.collections = [story("a")];
    await screen.poll();
    await flush();
    assert.deepEqual(stored(values), { a: { fingerprint: "f-a", names: ["a-1.mp3"] } });
    assert.equal(screen.node(".library-send-dock").hidden, false);
  } finally {
    screen.stop();
  }
}));

async function aimAndSend(screen) {
  const target = screen.node(".library-send-target");
  target.checked = true;
  await target.dispatchEvent({ type: "change" });
  const sent = screen.node(".library-send-submit").click();
  await flush();
  return sent;
}

test("clearing the selection clears the store", withSession(async (values) => {
  const screen = mount({ collections: [story("a")] });
  try {
    await flush();
    await tickFirst(screen);
    assert.ok(values.has(SELECTION_KEY));
    await screen.node(".library-send-clear").click();
    assert.equal(values.has(SELECTION_KEY), false);
  } finally {
    screen.stop();
  }
}));

test("a finished send clears the store, and coming back restores no selection and no key", withSession(async (values) => {
  const first = mount({ collections: [story("a")] });
  await flush();
  await tickFirst(first);
  await aimAndSend(first);
  await flush();
  assert.equal(first.pushes.length, 1);
  assert.equal(values.has(SELECTION_KEY), false);
  first.stop();

  const second = mount({ collections: [story("a")] });
  try {
    await flush();
    assert.equal(ticked(second), 0);
    assert.equal(second.node(".library-receipt"), null);
    await tickFirst(second);
    await aimAndSend(second);
    await flush();
    assert.notEqual(second.pushes[0].operation_key, first.pushes[0].operation_key, "ticking again is a new operation");
  } finally {
    second.stop();
  }
}));

test("nothing is stored while a send is in flight, and a failed send writes the ticks back", withSession(async (values) => {
  let settle = () => {};
  const dom = mount({
    collections: [story("a")],
    pushAnswer: new Promise((_, reject) => { settle = reject; }),
  });
  try {
    await flush();
    await tickFirst(dom);
    const sending = aimAndSend(dom);
    await flush();
    assert.equal(dom.pushes.length, 1);
    assert.equal(values.has(SELECTION_KEY), false, "leaving now must not leave these ticks waiting");

    settle(new Error("Upstream failed"));
    await sending;
    await flush();
    assert.equal(ticked(dom), 1, "the selection is unchanged");
    assert.deepEqual(Object.keys(stored(values)), ["a"]);
  } finally {
    dom.stop();
  }
}));

test("a sheet that comes back from desktop width brings focus inside it", async () => {
  const screen = mount({ collections: [story("a")] });
  try {
    await flush();
    await tickFirst(screen);
    await screen.node(".library-send-choose").click();
    screen.media.resize(false);
    // The desktop bar hides Close, so a browser has dropped focus to the body.
    screen.dom.document.activeElement = null;
    screen.media.resize(true);
    assert.equal(sheet(screen), "open");
    assert.equal(screen.focusKey(), "library-send-close");
  } finally {
    screen.stop();
  }
});

test("a sheet that comes back leaves focus on a control of the bar where it was", async () => {
  const screen = mount({ collections: [story("a")] });
  try {
    await flush();
    await tickFirst(screen);
    await screen.node(".library-send-choose").click();
    const target = screen.node(".library-send-target");
    target.checked = true;
    await target.dispatchEvent({ type: "change" });
    const key = screen.focusKey();
    screen.media.resize(false);
    screen.media.resize(true);
    assert.equal(sheet(screen), "open");
    assert.equal(screen.focusKey(), key);
  } finally {
    screen.stop();
  }
});

test("a receipt is always the sheet on a phone, so coming back puts focus inside it", async () => {
  const screen = mount({ collections: [story("a")] });
  try {
    await flush();
    await tickFirst(screen);
    await aimAndSend(screen);
    await flush();
    assert.ok(screen.node(".library-receipt"));
    screen.media.resize(false);
    screen.dom.document.activeElement = null;
    screen.media.resize(true);
    assert.equal(screen.node(".library-send-bar").getAttribute("data-sheet"), "open");
    assert.equal(screen.focusKey(), "library-send-done");
  } finally {
    screen.stop();
  }
});

const OPERATION_KEY = "toniefi.library.operation";

// A send the server may have accepted, whose answer never arrived.
async function failedSend() {
  const dropped = Promise.reject(new Error("The connection dropped."));
  dropped.catch(() => {});
  const first = mount({
    collections: [story("a")],
    tonies: [tonie("a"), tonie("b")],
    pushAnswer: dropped,
  });
  await flush();
  await tickFirst(first);
  await aimAndSend(first);
  await flush();
  assert.equal(first.pushes.length, 1);
  assert.equal(ticked(first), 1, "the ticks are kept for a retry");
  first.stop();
  return first.pushes[0].operation_key;
}

// A reload: the ticks come back and the target is chosen again by hand.
function reloaded(tonies = [tonie("a"), tonie("b")]) {
  return mount({ collections: [story("a")], tonies });
}

test("after a failed send a reload re-sends the same selection under the same key", withSession(async () => {
  const key = await failedSend();
  const second = reloaded();
  try {
    await flush();
    assert.equal(ticked(second), 1);
    await aimAndSend(second);
    await flush();
    assert.equal(second.pushes[0].operation_key, key);
  } finally {
    second.stop();
  }
}));

test("a reload keeps the key when the Tonie moved, so the server can answer 409 rather than append twice", withSession(async () => {
  const key = await failedSend();
  const landed = { ...tonie("a"), chapters: [{ id: "c1", title: "a 1" }], chapter_count: 1 };
  const second = reloaded([landed, tonie("b")]);
  try {
    await flush();
    await aimAndSend(second);
    await flush();
    assert.equal(second.pushes[0].operation_key, key);
    assert.deepEqual(second.pushes[0].assignments[0].remote_chapters, [{ id: "c1", title: "a 1" }]);
  } finally {
    second.stop();
  }
}));

test("a reload with a different target or effect gets a new key", withSession(async () => {
  const key = await failedSend();
  const second = reloaded();
  try {
    await flush();
    const other = second.all(".library-send-target")[1];
    other.checked = true;
    await other.dispatchEvent({ type: "change" });
    await second.node(".library-send-submit").click();
    await flush();
    assert.notEqual(second.pushes[0].operation_key, key);
  } finally {
    second.stop();
  }
}));

test("a poll never removes the stored key", withSession(async (values) => {
  const key = await failedSend();
  const second = reloaded();
  try {
    await flush();
    await second.poll();
    await flush();
    assert.equal(JSON.parse(values.get(OPERATION_KEY)).key, key);
  } finally {
    second.stop();
  }
}));

test("clearing the selection removes the stored key", withSession(async (values) => {
  const key = await failedSend();
  const second = reloaded();
  try {
    await flush();
    await second.node(".library-send-clear").click();
    assert.equal(values.has(OPERATION_KEY), false);
    await tickFirst(second);
    await aimAndSend(second);
    await flush();
    assert.notEqual(second.pushes[0].operation_key, key);
  } finally {
    second.stop();
  }
}));

test("a first render with nothing restored yet leaves the stored key alone", withSession(async (values) => {
  const key = await failedSend();
  const second = mount({ collections: [] });
  try {
    await second.poll();
    assert.equal(JSON.parse(values.get(OPERATION_KEY)).key, key);
  } finally {
    second.stop();
  }
}));

test("a finished send removes the stored key", withSession(async (values) => {
  const key = await failedSend();
  const second = reloaded();
  try {
    await flush();
    await aimAndSend(second);
    await flush();
    assert.equal(second.pushes[0].operation_key, key);
    assert.equal(values.has(OPERATION_KEY), false);
  } finally {
    second.stop();
  }
}));

test("the scroll position comes back on Back to Library and on back or forward, not on a fresh visit", withSession(async (values) => {
  for (const [context, expected] of [
    [{ from: "collection" }, [11000]],
    [{ from: "tonies", popped: true }, [11000]],
    [{ from: "desk" }, []],
    [{}, []],
  ]) {
    values.set(SCROLL_KEY, "11000");
    const screen = mount({ collections: [story("a")], context });
    const moves = [];
    globalThis.window.scrollTo = (_, y) => moves.push(y);
    assert.equal(moves.length, 0, "the page has no height until the router attaches it");
    screen.dom.document.dispatchEvent({ type: "toniefi:routechange" });
    assert.deepEqual(moves, expected);
    screen.stop();
  }
}));

test("leaving stores the position last scrolled to", withSession(async (values) => {
  const screen = mount({ collections: [story("a")] });
  try {
    await flush();
    screen.leave();
    assert.equal(values.get(SCROLL_KEY), "0");
  } finally {
    screen.stop();
  }
}));
