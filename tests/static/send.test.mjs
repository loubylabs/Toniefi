import assert from "node:assert/strict";
import test from "node:test";

import {
  buildPushBatchPayload,
  createSendAttempt,
  groupSources,
  membershipSignature,
  packSelection,
  rebindTargets,
  selectionProblems,
  sendCapacityLimit,
  targetLabel,
  tonieCapacity,
} from "../../app/static/send.js";

const story = (slug, fingerprint, tracks) => ({
  slug,
  manifest_fingerprint: fingerprint,
  title: slug,
  tracks,
});

test("packSelection keeps three small stories in one group", () => {
  const groups = packSelection([
    story("a", "f-a", [{ name: "a1.mp3", title: "A1", seconds: 300 }]),
    story("b", "f-b", [{ name: "b1.mp3", title: "B1", seconds: 300 }]),
    story("c", "f-c", [{ name: "c1.mp3", title: "C1", seconds: 300 }]),
  ], 5400);

  assert.equal(groups.length, 1);
  assert.equal(groups[0].seconds, 900);
  assert.deepEqual(groups[0].entries.map((entry) => entry.name), ["a1.mp3", "b1.mp3", "c1.mp3"]);
});

test("packSelection starts a new group when the next track would overflow", () => {
  const groups = packSelection([
    story("a", "f-a", [
      { name: "a1.mp3", title: "A1", seconds: 1000 },
      { name: "a2.mp3", title: "A2", seconds: 1000 },
    ]),
  ], 1500);

  assert.equal(groups.length, 2);
  assert.deepEqual(groups.map((group) => group.index), [1, 2]);
  assert.deepEqual(groups[0].entries.map((entry) => entry.name), ["a1.mp3"]);
  assert.deepEqual(groups[1].entries.map((entry) => entry.name), ["a2.mp3"]);
});

test("packSelection keeps an oversized single track alone rather than dropping it", () => {
  const groups = packSelection([
    story("a", "f-a", [{ name: "big.mp3", title: "Big", seconds: 9000 }]),
  ], 5400);

  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].entries.map((entry) => entry.name), ["big.mp3"]);
});

test("groupSources collapses consecutive tracks of one collection into one source", () => {
  const [group] = packSelection([
    story("a", "f-a", [
      { name: "a1.mp3", title: "A1", seconds: 100 },
      { name: "a2.mp3", title: "A2", seconds: 100 },
    ]),
    story("b", "f-b", [{ name: "b1.mp3", title: "B1", seconds: 100 }]),
  ], 5400);

  assert.deepEqual(groupSources(group), [
    { slug: "a", manifest_fingerprint: "f-a", files: ["a1.mp3", "a2.mp3"] },
    { slug: "b", manifest_fingerprint: "f-b", files: ["b1.mp3"] },
  ]);
});

test("tonieCapacity subtracts what is present for an append and ignores it for a replace", () => {
  const tonie = { seconds_present: 4000 };
  assert.equal(tonieCapacity(tonie, 900, false, 5400).fits, true);
  assert.equal(tonieCapacity(tonie, 2000, false, 5400).fits, false);
  assert.equal(tonieCapacity(tonie, 2000, true, 5400).fits, true);
});

test("targetLabel names the household, because Tonie names repeat across them", () => {
  assert.equal(targetLabel({ name: "Bedtime", householdName: "Emily" }), "Bedtime · Emily");
  assert.equal(targetLabel({ name: "Bedtime" }), "Bedtime");
  assert.equal(targetLabel({}), "Creative Tonie");
});

test("sendCapacityLimit reads the usable limit from status", () => {
  assert.equal(sendCapacityLimit({ usable_limit_seconds: 5100 }), 5100);
  assert.equal(sendCapacityLimit({}), 0);
});

test("selectionProblems reports a group with no target chosen", () => {
  const groups = packSelection([story("a", "f-a", [{ name: "a1.mp3", title: "A1", seconds: 100 }])], 5400);

  assert.deepEqual(selectionProblems(groups, [{ tonie: null, replaceExisting: false }], 5400), [
    "Group 1 has no Creative Tonie chosen.",
  ]);
});

test("selectionProblems reports one Tonie chosen for two groups", () => {
  const groups = packSelection([
    story("a", "f-a", [
      { name: "a1.mp3", title: "A1", seconds: 1000 },
      { name: "a2.mp3", title: "A2", seconds: 1000 },
    ]),
  ], 1500);
  const tonie = { id: "t1", householdId: "h1", name: "Bedtime", householdName: "Emily", seconds_present: 0 };

  const problems = selectionProblems(groups, [
    { tonie, replaceExisting: false },
    { tonie, replaceExisting: false },
  ], 5400);

  assert.ok(problems.some((line) => line.includes("more than one group")));
});

test("selectionProblems reports an append that does not fit", () => {
  const groups = packSelection([story("a", "f-a", [{ name: "a1.mp3", title: "A1", seconds: 2000 }])], 5400);
  const tonie = { id: "t1", householdId: "h1", name: "Bedtime", householdName: "Emily", seconds_present: 5000 };

  const problems = selectionProblems(groups, [{ tonie, replaceExisting: false }], 5400);

  assert.ok(problems.some((line) => line.includes("does not fit")));
});

test("selectionProblems is empty when every group has a distinct target that fits", () => {
  const groups = packSelection([story("a", "f-a", [{ name: "a1.mp3", title: "A1", seconds: 100 }])], 5400);
  const tonie = { id: "t1", householdId: "h1", name: "Bedtime", householdName: "Emily", seconds_present: 0 };

  assert.deepEqual(selectionProblems(groups, [{ tonie, replaceExisting: false }], 5400), []);
});

test("buildPushBatchPayload emits one assignment per group with its sources", () => {
  const groups = packSelection([
    story("a", "f-a", [{ name: "a1.mp3", title: "A1", seconds: 100 }]),
    story("b", "f-b", [{ name: "b1.mp3", title: "B1", seconds: 100 }]),
  ], 5400);
  const tonie = {
    id: "t1",
    householdId: "h1",
    name: "Bedtime",
    householdName: "Emily",
    chapters: [{ id: "c1", title: "Already there" }],
  };

  const payload = buildPushBatchPayload(groups, [{ tonie, replaceExisting: false }], "key-1");

  assert.deepEqual(payload, {
    operation_key: "key-1",
    assignments: [{
      household_id: "h1",
      tonie_id: "t1",
      replace: false,
      remote_chapters: [{ id: "c1", title: "Already there" }],
      sources: [
        { slug: "a", manifest_fingerprint: "f-a", files: ["a1.mp3"] },
        { slug: "b", manifest_fingerprint: "f-b", files: ["b1.mp3"] },
      ],
    }],
  });
});

test("createSendAttempt refuses a second submit while one is in flight", async () => {
  let calls = 0;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const attempt = createSendAttempt({
    payload: { operation_key: "key-1", assignments: [] },
    confirm: async () => true,
    request: async () => { calls += 1; await gate; return { job_ids: [1] }; },
  });

  const first = attempt.submit();
  const second = await attempt.submit();
  release();
  await first;

  assert.equal(calls, 1);
  assert.equal(second, false);
});

test("createSendAttempt retries with the same payload and skips confirmation", async () => {
  const seen = [];
  const attempt = createSendAttempt({
    payload: { operation_key: "key-1", assignments: [] },
    confirm: async () => { throw new Error("retry must not confirm again"); },
    request: async (_path, options) => { seen.push(JSON.parse(options.body).operation_key); return { job_ids: [1] }; },
  });

  await attempt.retry();
  await attempt.retry();

  assert.deepEqual(seen, ["key-1", "key-1"]);
});

test("membershipSignature changes when a collection joins the same group", () => {
  const limit = 5400;
  const before = packSelection([story("a", "f-a", [{ name: "a1.mp3", title: "A1", seconds: 100 }])], limit);
  const after = packSelection([
    story("a", "f-a", [{ name: "a1.mp3", title: "A1", seconds: 100 }]),
    story("b", "f-b", [{ name: "b1.mp3", title: "B1", seconds: 100 }]),
  ], limit);

  assert.notEqual(membershipSignature(before), membershipSignature(after));
});

test("membershipSignature changes when a collection's fingerprint moves", () => {
  const limit = 5400;
  const before = packSelection([story("a", "f-a", [{ name: "a1.mp3", title: "A1", seconds: 100 }])], limit);
  const after = packSelection([story("a", "f-a2", [{ name: "a1.mp3", title: "A1", seconds: 100 }])], limit);

  assert.notEqual(membershipSignature(before), membershipSignature(after));
});

test("membershipSignature is stable for the same selection", () => {
  const limit = 5400;
  const build = () => packSelection([story("a", "f-a", [{ name: "a1.mp3", title: "A1", seconds: 100 }])], limit);

  assert.equal(membershipSignature(build()), membershipSignature(build()));
});

test("rebindTargets points a chosen target at the freshly fetched object", () => {
  const stale = { id: "t1", householdId: "h1", name: "Bedtime", seconds_present: 0, chapters: [] };
  const fresh = { id: "t1", householdId: "h1", name: "Bedtime", seconds_present: 5000, chapters: [{ id: "c9", title: "New" }] };
  const selections = [{ tonie: stale, replaceExisting: false }];

  rebindTargets(selections, [fresh]);

  assert.equal(selections[0].tonie.seconds_present, 5000);
  assert.deepEqual(selections[0].tonie.chapters, [{ id: "c9", title: "New" }]);
});

test("rebindTargets drops a target that is no longer offered", () => {
  const stale = { id: "t1", householdId: "h1", name: "Bedtime" };
  const selections = [{ tonie: stale, replaceExisting: false }];

  rebindTargets(selections, [{ id: "t2", householdId: "h1", name: "Travel" }]);

  assert.equal(selections[0].tonie, null);
});

test("createSendAttempt returns false when confirmation is declined", async () => {
  let calls = 0;
  const attempt = createSendAttempt({
    payload: { operation_key: "key-1", assignments: [] },
    confirm: async () => false,
    request: async () => { calls += 1; return { job_ids: [1] }; },
  });

  assert.equal(await attempt.submit(), false);
  assert.equal(calls, 0);
});
