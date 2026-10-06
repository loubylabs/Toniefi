import assert from "node:assert/strict";
import test from "node:test";

import { activityAction, activityDetail, activityFacts, relativeTime } from "../../app/static/activity.js";

test("a failed send never tells the operator to send it again", () => {
  const action = activityAction({
    id: 4,
    kind: "push",
    status: "failed",
    phase: "failed",
    payload: { sources: [{ slug: "sleepy-sophie" }] },
  });
  assert.equal(action.kind, "collection");
  assert.ok(!/send it again/i.test(action.guidance));
  assert.match(action.guidance, /check what landed/i);
});

test("a finished send reports what it delivered", () => {
  const facts = activityFacts({
    kind: "push",
    status: "done",
    phase: "sent",
    payload: {},
    result: { tonie: "Bedtime Bear", chapters: 30, duration: "1h 26m" },
  });
  const flat = Object.fromEntries(facts);
  assert.equal(flat.Delivered, "30 chapters (1h 26m) to Bedtime Bear");
  assert.ok(!("Phase" in flat) && !("Status" in flat));
});

test("a job with no delivery result carries no delivered row", () => {
  const facts = activityFacts({ kind: "forge", status: "done", phase: "ready", payload: {}, result: {} });
  assert.ok(!Object.fromEntries(facts).Delivered);
});

test("progress shows only when it adds to the stamp", () => {
  assert.equal(activityDetail({ phase: "sent", status: "done", progress: "Sent" }), "");
  assert.equal(activityDetail({ phase: "queued", status: "queued", progress: "queued." }), "");
  assert.equal(activityDetail({ phase: "failed", status: "failed", progress: "" }), "");
  assert.equal(
    activityDetail({ phase: "sending", status: "running", progress: "Adding chapter 3 of 9" }),
    "Adding chapter 3 of 9",
  );
});

test("relative time reads as a log and falls back to a date after two weeks", () => {
  const now = 1_000_000;
  assert.equal(relativeTime({ updated_at: now - 20 }, now), "just now");
  assert.equal(relativeTime({ updated_at: now - 5 * 60 }, now), "5 min ago");
  assert.equal(relativeTime({ updated_at: now - 3 * 3600 }, now), "3 h ago");
  assert.equal(relativeTime({ created_at: now - 2 * 86400 }, now), "2 d ago");
  assert.notEqual(relativeTime({ updated_at: now - 30 * 86400 }, now), "30 d ago");
  assert.equal(relativeTime({}, now), "Time unavailable");
});
