import assert from "node:assert/strict";
import test from "node:test";

import {
  addATonieWorth,
  buildPushBatchPayload,
  createSendAttempt,
  groupSources,
  membershipSignature,
  packSelection,
  rebindTargets,
  selectionProblems,
  sendCapacityLimit,
  tonieCapacity,
  tonieFreeSeconds,
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

test("packSelection keeps a track that exactly fills the limit in the same group", () => {
  // The boundary that separates the server's `>` from a `>=` off-by-one. At
  // exactly the limit the track still belongs to the current group; only a
  // track that would take the total PAST the limit starts a new one. A drift
  // here makes the server refuse every real send with a 409 nobody can clear.
  const groups = packSelection([
    story("a", "f-a", [
      { name: "a1.mp3", title: "A1", seconds: 1000 },
      { name: "a2.mp3", title: "A2", seconds: 500 },
    ]),
  ], 1500);

  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].entries.map((entry) => entry.name), ["a1.mp3", "a2.mp3"]);
  assert.equal(groups[0].seconds, 1500);
});

test("packSelection starts a new group one second past the limit", () => {
  const groups = packSelection([
    story("a", "f-a", [
      { name: "a1.mp3", title: "A1", seconds: 1000 },
      { name: "a2.mp3", title: "A2", seconds: 501 },
    ]),
  ], 1500);

  assert.equal(groups.length, 2);
  assert.deepEqual(groups[0].entries.map((entry) => entry.name), ["a1.mp3"]);
  assert.deepEqual(groups[1].entries.map((entry) => entry.name), ["a2.mp3"]);
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

test("tonieFreeSeconds is a property of the Tonie and never moves with the effect", () => {
  // The fit budget grows for a replace, because the box is cleared first. The
  // free space does not: it describes how full the Tonie is right now, and the
  // picker prints it so the operator reads the same figure under either effect.
  const tonie = { seconds_present: 5000 };
  assert.equal(tonieFreeSeconds(tonie, 5400), 400);
  assert.equal(tonieCapacity(tonie, 1200, false, 5400).availableSeconds, 400);
  assert.equal(tonieCapacity(tonie, 1200, true, 5400).availableSeconds, 5400);
});

test("tonieFreeSeconds never reports negative space for an overfull Tonie", () => {
  assert.equal(tonieFreeSeconds({ seconds_present: 6000 }, 5400), 0);
  assert.equal(tonieFreeSeconds({}, 5400), 5400);
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

test("selectionProblems reports a picked collection with no chapters, even alone", () => {
  // Every chapter removed from a forged collection on its own page leaves it
  // with zero tracks. packSelection drops it silently: it contributes no
  // entries, so groups is empty and the loop over groups never runs. Without
  // this check, an all-empty selection would read as zero problems, Send
  // would be enabled, and the batch it posts would carry no assignments.
  const empty = story("empty", "f-empty", []);

  const problems = selectionProblems([], [], 5400, [empty]);

  assert.deepEqual(problems, ["empty has no chapters and cannot be sent."]);
});

test("selectionProblems reports a picked collection with no chapters alongside real ones", () => {
  const real = story("a", "f-a", [{ name: "a1.mp3", title: "A1", seconds: 100 }]);
  const empty = story("empty", "f-empty", []);
  const groups = packSelection([real], 5400);
  const tonie = { id: "t1", householdId: "h1", name: "Bedtime", householdName: "Emily", seconds_present: 0 };

  const problems = selectionProblems(groups, [{ tonie, replaceExisting: false }], 5400, [real, empty]);

  assert.deepEqual(problems, ["empty has no chapters and cannot be sent."]);
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

const chapters = [
  { name: "01.mp3", seconds: 600 },
  { name: "02.mp3", seconds: 600 },
  { name: "03.mp3", seconds: 600 },
  { name: "04.mp3", seconds: 600 },
];

test("addATonieWorth fills from the start when nothing is ticked", () => {
  assert.deepEqual(addATonieWorth(chapters, [], 1500), ["01.mp3", "02.mp3"]);
});

test("addATonieWorth carries on from the last ticked chapter", () => {
  assert.deepEqual(
    addATonieWorth(chapters, ["01.mp3", "02.mp3"], 1500),
    ["01.mp3", "02.mp3", "03.mp3", "04.mp3"],
  );
});

test("addATonieWorth starts after the last tick, not after the first gap", () => {
  // Ticks at 1 and 3 mean the operator has already taken chapter 3, so the
  // next Tonie's worth begins at 4, not back at the hole in the middle.
  assert.deepEqual(
    addATonieWorth(chapters, ["01.mp3", "03.mp3"], 1500),
    ["01.mp3", "03.mp3", "04.mp3"],
  );
});

test("addATonieWorth returns names in manifest order, not ticking order", () => {
  assert.deepEqual(addATonieWorth(chapters, ["03.mp3", "01.mp3"], 0), ["01.mp3", "03.mp3", "04.mp3"]);
});

test("addATonieWorth takes one chapter that is longer than a whole Tonie", () => {
  // Forge with Split off can leave an oversized chapter. Refusing to take it
  // would leave the control permanently dead on that story, and the Send bar
  // already reports such a group as one that does not fit.
  const oversized = [{ name: "big.mp3", seconds: 9000 }, { name: "small.mp3", seconds: 60 }];
  assert.deepEqual(addATonieWorth(oversized, [], 1500), ["big.mp3"]);
});

test("addATonieWorth changes nothing once the last chapter is ticked", () => {
  const every = chapters.map((track) => track.name);
  assert.deepEqual(addATonieWorth(chapters, every, 1500), every);
});

test("addATonieWorth drops a name the collection no longer holds", () => {
  assert.deepEqual(addATonieWorth(chapters, ["gone.mp3"], 1200), ["01.mp3", "02.mp3"]);
});

