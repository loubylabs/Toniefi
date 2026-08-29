import assert from "node:assert/strict";
import test from "node:test";

import { activityAction, activityFacts } from "../../app/static/activity.js";

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
  }, () => "Time");
  const flat = Object.fromEntries(facts);
  assert.equal(flat.Delivered, "30 chapters (1h 26m) to Bedtime Bear");
  assert.equal(flat.Phase, "Sent");
});

test("a job with no delivery result carries no delivered row", () => {
  const facts = activityFacts({ kind: "forge", status: "done", phase: "ready", payload: {}, result: {} }, () => "Time");
  assert.ok(!Object.fromEntries(facts).Delivered);
});
