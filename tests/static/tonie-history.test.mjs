import assert from "node:assert/strict";
import test from "node:test";

import { createToniesScreen, versionSourceLabel, versionSummary } from "../../app/static/tonies.js";
import { flush, installDom } from "./mini-dom.mjs";

test("summaries read plainly", () => {
  assert.equal(versionSummary({ changes: { first: true }, chapters: [{}, {}] }), "First seen, 2 chapters");
  assert.equal(versionSummary({ changes: { first: true }, chapters: [{}] }), "First seen, 1 chapter");
  assert.equal(versionSummary({ changes: { added: 2, removed: 1, renamed: 0, reordered: false }, chapters: [] }), "2 added, 1 removed");
  assert.equal(versionSummary({ changes: { added: 0, removed: 0, renamed: 0, reordered: true }, chapters: [] }), "Reordered");
  assert.equal(versionSummary({ changes: { added: 0, removed: 0, renamed: 1, reordered: true }, chapters: [] }), "1 renamed, reordered");
  assert.equal(versionSourceLabel("toniefi"), "Toniefi");
  assert.equal(versionSourceLabel("seen"), "Changed outside Toniefi");
});

const VERSIONS = [
  {
    id: 2, created_at: 1760000000, source: "toniefi", tonie_name: "Bear",
    chapters: [{ id: "a", title: "One", duration: "3m" }, { id: "b", title: "Two", duration: "" }],
    changes: { first: false, added: 1, removed: 0, renamed: 0, reordered: false },
  },
  {
    id: 1, created_at: 1750000000, source: "seen", tonie_name: "Bear",
    chapters: [{ id: "a", title: "One", duration: "3m" }],
    changes: { first: true, added: 1, removed: 0, renamed: 0, reordered: false },
  },
];

function mount({ chapters = [], versions = VERSIONS, failVersions = false } = {}) {
  const dom = installDom();
  const controller = new AbortController();
  const state = { versionCalls: [] };
  const tonies = [{
    id: "t1", householdId: "h1", householdName: "Home", name: "Bear", imageUrl: "",
    chapter_count: chapters.length, chapters, time_free: "1h",
  }];
  const request = async (url) => {
    if (url === "/api/tonies") return tonies;
    if (url.endsWith("/versions")) {
      state.versionCalls.push(url);
      if (failVersions) throw new Error("boom");
      return versions;
    }
    throw new Error(`unexpected request ${url}`);
  };
  let listener = null;
  const refresh = {
    subscribe(fn) { listener = fn; return () => { listener = null; }; },
    request() {},
  };
  const push = (snapshot) => listener(snapshot);
  const teardown = createToniesScreen({ request, refresh })({ workspace: dom.workspace, signal: controller.signal });
  return { dom, state, teardown, push };
}

const historyButton = (dom) => dom.workspace.querySelectorAll("button")
  .find((b) => b.textContent === "Version history");

async function openTonie(harness) {
  await flush();
  await harness.dom.workspace.querySelectorAll("button")
    .find((b) => b.className.includes("tonie-summary")).dispatchEvent({ type: "click" });
  await flush();
}

const chapter = { id: "a", title: "One", seconds: 10, duration: "10s" };

test("opening history fetches once and lists each version with its chapters", async () => {
  const harness = mount({ chapters: [chapter] });
  await openTonie(harness);
  assert.equal(harness.state.versionCalls.length, 0);
  await historyButton(harness.dom).dispatchEvent({ type: "click" });
  await flush();
  assert.deepEqual(harness.state.versionCalls, ["/api/tonies/h1/t1/versions"]);
  const rows = harness.dom.workspace.querySelectorAll("details");
  assert.equal(rows.length, 2);
  assert.ok(rows[0].textContent.includes("Toniefi"));
  assert.ok(rows[0].textContent.includes("1 added"));
  assert.ok(!rows[1].textContent.includes("Changed outside Toniefi"));
  assert.ok(rows[1].textContent.includes("First seen, 1 chapter"));
  const titles = rows[0].querySelectorAll("ol")[0].querySelectorAll("li").map((li) => li.textContent);
  assert.deepEqual(titles, ["One (3m)", "Two"]);
  harness.teardown();
  harness.dom.restore();
});

test("only a later seen version is labelled as changed outside Toniefi", async () => {
  const later = { ...VERSIONS[0], id: 3, source: "seen" };
  const harness = mount({ chapters: [chapter], versions: [later, VERSIONS[1]] });
  await openTonie(harness);
  await historyButton(harness.dom).dispatchEvent({ type: "click" });
  await flush();
  const rows = harness.dom.workspace.querySelectorAll("details");
  assert.ok(rows[0].textContent.includes("Changed outside Toniefi"));
  assert.ok(!rows[1].textContent.includes("Changed outside Toniefi"));
  harness.teardown();
  harness.dom.restore();
});

test("a Tonie with no chapters still offers its history", async () => {
  const harness = mount({ chapters: [] });
  await openTonie(harness);
  assert.ok(historyButton(harness.dom));
  harness.teardown();
  harness.dom.restore();
});

test("a refresh re-render keeps the open history and does not fetch again", async () => {
  const harness = mount({ chapters: [chapter] });
  await openTonie(harness);
  await historyButton(harness.dom).dispatchEvent({ type: "click" });
  await flush();
  harness.push({ jobs: [], stale: [], errors: {} });
  await flush();
  assert.equal(harness.dom.workspace.querySelectorAll("details").length, 2);
  assert.equal(harness.state.versionCalls.length, 1);
  harness.teardown();
  harness.dom.restore();
});

test("an expanded version row stays expanded across a refresh", async () => {
  const harness = mount({ chapters: [chapter] });
  await openTonie(harness);
  await historyButton(harness.dom).dispatchEvent({ type: "click" });
  await flush();
  const first = harness.dom.workspace.querySelectorAll("details")[0];
  first.setAttribute("open", "");
  await first.dispatchEvent({ type: "toggle" });
  harness.push({ jobs: [], stale: [], errors: {} });
  await flush();
  const rows = harness.dom.workspace.querySelectorAll("details");
  assert.ok(rows[0].hasAttribute("open"));
  assert.ok(!rows[1].hasAttribute("open"));
  harness.teardown();
  harness.dom.restore();
});

test("a finished send refetches the open history", async () => {
  const harness = mount({ chapters: [chapter] });
  await openTonie(harness);
  await historyButton(harness.dom).dispatchEvent({ type: "click" });
  await flush();
  const running = { id: 9, kind: "push", status: "running", payload: { household_id: "h1", tonie_id: "t1" } };
  harness.push({ jobs: [running], stale: [], errors: {} });
  await flush();
  assert.equal(harness.state.versionCalls.length, 1);
  harness.push({ jobs: [], stale: [], errors: {} });
  await flush();
  await flush();
  assert.equal(harness.state.versionCalls.length, 2);
  harness.teardown();
  harness.dom.restore();
});

test("a failed history read is reported in the panel and the detail still renders", async () => {
  const harness = mount({ chapters: [chapter], failVersions: true });
  await openTonie(harness);
  await historyButton(harness.dom).dispatchEvent({ type: "click" });
  await flush();
  assert.ok(harness.dom.workspace.textContent.includes("Version history could not be loaded."));
  assert.ok(harness.dom.workspace.querySelectorAll("input").some((i) => i.className.includes("tonie-name-input")));
  assert.ok(harness.dom.workspace.querySelectorAll("[data-tonie-chapter]").length === 1);
  harness.teardown();
  harness.dom.restore();
});
