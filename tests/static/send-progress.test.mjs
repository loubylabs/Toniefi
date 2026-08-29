import assert from "node:assert/strict";
import test from "node:test";

import { activeSendsByTonie, sendJobView, tonieJobKey } from "../../app/static/send.js";

const job = (overrides) => ({
  id: 1,
  kind: "push",
  status: "running",
  phase: "sending",
  progress: "Uploading 7/30: Whale Shark Rescue",
  progress_percent: 22.5,
  payload: { household_id: "h1", tonie_id: "t1" },
  ...overrides,
});

test("only queued and running sends are matched to a Tonie", () => {
  const map = activeSendsByTonie([
    job({}),
    job({ id: 2, status: "queued", phase: "queued", progress: "" }),
    job({ id: 3, status: "done", phase: "sent" }),
    job({ id: 4, kind: "forge", status: "running" }),
    job({ id: 5, payload: { household_id: "h1", tonie_id: "t2" } }),
  ]);
  assert.deepEqual([...map.keys()].sort(), ["h1/t1", "h1/t2"]);
  assert.deepEqual(map.get("h1/t1").map((entry) => entry.id), [1, 2]);
  assert.equal(tonieJobKey("h1", "t1"), "h1/t1");
});

test("a send with no target in its payload is ignored rather than crashing", () => {
  const map = activeSendsByTonie([job({ payload: {} }), job({ payload: null })]);
  assert.equal(map.size, 0);
});

test("a send with a real percentage drives a determinate bar", () => {
  const view = sendJobView(job({}));
  assert.equal(view.mode, "determinate");
  assert.equal(view.percent, 22.5);
  assert.equal(view.message, "Uploading 7/30: Whale Shark Rescue");
  assert.equal(view.label, "Sending");
});

test("a send with no percentage stays indeterminate and never invents one", () => {
  const view = sendJobView(job({ progress_percent: null, progress: "Signing in to myTonies" }));
  assert.equal(view.mode, "indeterminate");
  assert.equal(view.percent, null);
  assert.equal(view.message, "Signing in to myTonies");
});

test("a queued send says it is waiting rather than showing an empty line", () => {
  const view = sendJobView(job({ status: "queued", phase: "queued", progress: "", progress_percent: null }));
  assert.equal(view.label, "Queued");
  assert.equal(view.message, "Waiting for a worker");
});
