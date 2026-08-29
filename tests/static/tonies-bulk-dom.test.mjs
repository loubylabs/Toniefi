import assert from "node:assert/strict";
import test from "node:test";

import { createToniesScreen } from "../../app/static/tonies.js";
import { buttonWithText, flush, installDom } from "./mini-dom.mjs";

function chapter(id, title) {
  return { id, title, duration: "5m 00s" };
}

function blueTonie(chapters) {
  return {
    id: "t1",
    householdId: "h1",
    householdName: "Home",
    name: "Blue Tonie",
    chapter_count: chapters.length,
    time_free: "10m 00s",
    chapters,
  };
}

function mountTonies(chapters) {
  const dom = installDom();
  const controller = new AbortController();
  const puts = [];
  let current = [blueTonie(chapters)];
  const request = async (url, options = {}) => {
    if (url === "/api/tonies") return current;
    if (url === "/api/tonies/h1/t1/chapters" && options.method === "PUT") {
      const body = JSON.parse(options.body);
      puts.push(body);
      current = [{ ...current[0], chapters: body.chapters, chapter_count: body.chapters.length }];
      return current[0];
    }
    throw new Error(`Unexpected request ${url} ${options.method || "GET"}`);
  };
  const cleanup = createToniesScreen({ request })({ workspace: dom.workspace, signal: controller.signal });
  return {
    dom,
    puts,
    node: (selector) => dom.workspace.querySelector(selector),
    nodes: (selector) => dom.workspace.querySelectorAll(selector),
    byFocusKey: (key) => dom.workspace.querySelectorAll("[data-focus-key]").find((item) => item.getAttribute("data-focus-key") === key),
    focusKey: () => dom.document.activeElement?.getAttribute("data-focus-key") || "",
    async openDetail() {
      await buttonWithText(dom.workspace, "Blue Tonie").click();
      await flush();
    },
    async tick(selector) {
      const box = dom.workspace.querySelector(selector);
      box.checked = true;
      await box.dispatchEvent({ type: "change" });
      await flush();
    },
    async untick(selector) {
      const box = dom.workspace.querySelector(selector);
      box.checked = false;
      await box.dispatchEvent({ type: "change" });
      await flush();
    },
    stop() {
      cleanup();
      controller.abort();
      dom.restore();
    },
  };
}

test("every chapter tick box has a real, if visually hidden, label", async () => {
  const screen = mountTonies([chapter("c1", "One"), chapter("c2", "Two")]);
  try {
    await flush();
    await screen.openDetail();

    const ticks = screen.nodes(".tonie-chapter-select");
    assert.equal(ticks.length, 2);
    const label = screen.dom.workspace.querySelectorAll("label").find((item) => item.getAttribute("for") === ticks[0].id);
    assert.ok(label, "the tick box has an associated label element");
    assert.equal(label.textContent, "Select One for removal");
    assert.equal(label.className, "visually-hidden");
  } finally {
    screen.stop();
  }
});

test("ticking rows updates the count, Select all reflects a full set, and the button is disabled at zero", async () => {
  const screen = mountTonies([chapter("c1", "One"), chapter("c2", "Two"), chapter("c3", "Three")]);
  try {
    await flush();
    await screen.openDetail();

    assert.equal(screen.node(".tonie-remove-selected").disabled, true);
    assert.equal(screen.node(".tonie-remove-selected").textContent.includes("Remove selected"), true);

    const ticks = screen.nodes(".tonie-chapter-select");
    await screen.tick(`#${ticks[0].id}`);
    assert.equal(screen.focusKey(), "tonie-c1-select");
    assert.match(screen.node(".tonie-remove-selected").textContent, /Remove 1 selected/);
    assert.equal(screen.node(".tonie-remove-selected").disabled, false);
    assert.equal(screen.node(".tonie-select-all").checked, false);

    const ticksAgain = screen.nodes(".tonie-chapter-select");
    await screen.tick(`#${ticksAgain[1].id}`);
    assert.equal(screen.focusKey(), "tonie-c2-select");
    assert.match(screen.node(".tonie-remove-selected").textContent, /Remove 2 selected/);
    assert.equal(screen.node(".tonie-select-all").checked, false);

    // Untick one to prove the count and disabled state track down as well as up.
    const ticksBack = screen.nodes(".tonie-chapter-select");
    await screen.untick(`#${ticksBack[0].id}`);
    assert.match(screen.node(".tonie-remove-selected").textContent, /Remove 1 selected/);
  } finally {
    screen.stop();
  }
});

test("ticking two chapters then Select all removes every chapter in one confirmed save, and focus falls back to Refresh", async () => {
  const screen = mountTonies([chapter("c1", "One"), chapter("c2", "Two"), chapter("c3", "Three")]);
  try {
    await flush();
    await screen.openDetail();

    const ticks = screen.nodes(".tonie-chapter-select");
    await screen.tick(`#${ticks[0].id}`);
    await screen.tick(`#${screen.nodes(".tonie-chapter-select")[1].id}`);
    assert.match(screen.node(".tonie-remove-selected").textContent, /Remove 2 selected/);

    await screen.tick(".tonie-select-all");
    assert.equal(screen.focusKey(), "tonie-t1-select-all");
    assert.match(screen.node(".tonie-remove-selected").textContent, /Remove 3 selected/);
    assert.equal(screen.node(".tonie-select-all").checked, true);

    const clicking = screen.node(".tonie-remove-selected").click();
    await flush();
    const dialog = screen.dom.document.getElementById("dialogHost").querySelector(".confirmation-dialog");
    assert.match(dialog.textContent, /Remove 3 chapters\?/);
    assert.match(dialog.textContent, /This cannot be undone\. Your library on disk is not touched\./);
    assert.equal(screen.puts.length, 0);

    await buttonWithText(dialog, "Remove 3 chapters").click();
    await clicking;
    await flush();

    assert.equal(screen.puts.length, 1);
    assert.deepEqual(screen.puts[0].base.map((c) => c.id), ["c1", "c2", "c3"]);
    assert.deepEqual(screen.puts[0].chapters, []);

    // Nothing is left, so the empty state renders and there is no bulk
    // control left to carry the remembered focus key. It falls back to the
    // one focusable, visible landmark: Refresh Tonies.
    assert.equal(screen.node(".tonie-remove-selected"), null);
    assert.equal(screen.dom.document.activeElement, buttonWithText(screen.dom.workspace, "Refresh Tonies"));
    assert.match(screen.node(".tonie-empty").textContent, /Select stories in the Library and send them here when you are ready\./);
  } finally {
    screen.stop();
  }
});

test("removing some but not all chapters leaves correct titles and survivors, never reading the tick box as a title", async () => {
  const screen = mountTonies([chapter("c1", "One"), chapter("c2", "Two"), chapter("c3", "Three")]);
  try {
    await flush();
    await screen.openDetail();

    const ticks = screen.nodes(".tonie-chapter-select");
    await screen.tick(`#${ticks[0].id}`);
    await screen.tick(`#${screen.nodes(".tonie-chapter-select")[1].id}`);

    const clicking = screen.node(".tonie-remove-selected").click();
    await flush();
    const dialog = screen.dom.document.getElementById("dialogHost").querySelector(".confirmation-dialog");
    assert.match(dialog.textContent, /Remove 2 chapters\?/);
    await buttonWithText(dialog, "Remove 2 chapters").click();
    await clicking;
    await flush();

    assert.equal(screen.puts.length, 1);
    // If chapterDrafts ever regressed to reading the row's first input again,
    // this would come back as [{ id: "c3", title: "on" }] instead.
    assert.deepEqual(screen.puts[0].chapters, [{ id: "c3", title: "Three" }]);

    // One chapter remains, so the bulk button survives the re-render, now
    // disabled again because nothing on the new list is ticked.
    const survivor = screen.node(".tonie-remove-selected");
    assert.ok(survivor, "the bulk control is still present with one chapter left");
    assert.equal(survivor.disabled, true);
    assert.equal(survivor.textContent.includes("Remove selected"), true);
  } finally {
    screen.stop();
  }
});

test("declining the confirmation removes nothing and keeps the selection intact", async () => {
  const screen = mountTonies([chapter("c1", "One"), chapter("c2", "Two")]);
  try {
    await flush();
    await screen.openDetail();

    await screen.tick(`#${screen.nodes(".tonie-chapter-select")[0].id}`);

    const clicking = screen.node(".tonie-remove-selected").click();
    await flush();
    const dialog = screen.dom.document.getElementById("dialogHost").querySelector(".confirmation-dialog");
    await buttonWithText(dialog, "Cancel").click();
    await clicking;
    await flush();

    assert.equal(screen.puts.length, 0);
    assert.match(screen.node(".tonie-remove-selected").textContent, /Remove 1 selected/);
    assert.equal(screen.focusKey(), "tonie-t1-remove-selected");
  } finally {
    screen.stop();
  }
});

test("no per-row remove control exists anywhere in the rendered chapter list", async () => {
  const screen = mountTonies([chapter("c1", "One"), chapter("c2", "Two")]);
  try {
    await flush();
    await screen.openDetail();

    assert.equal(screen.nodes(".tonie-remove").length, 0);
    const rowRemoveButtons = screen.nodes("button").filter((button) => button.textContent.trim() === "Remove");
    assert.equal(rowRemoveButtons.length, 0);
  } finally {
    screen.stop();
  }
});
