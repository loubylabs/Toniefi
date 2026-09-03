// Every calculation a confirmed send needs, and no DOM of its own.
//
// packSelection mirrors app/audio.py `pack` exactly: sequential first fit,
// order preserved, a group closed only when the NEXT track would overflow it.
// The server plans the same way and refuses a batch whose group boundaries
// disagree, so a drift here is a 409 the operator cannot clear.

import { tonieLabel } from "./shared.js";

export function packSelection(collections, limitSeconds) {
  const limit = Number(limitSeconds) || 0;
  const groups = [];
  let current = { index: 1, seconds: 0, entries: [] };
  for (const collection of collections) {
    for (const track of collection.tracks || []) {
      const seconds = Number(track.seconds || 0);
      if (current.entries.length && current.seconds + seconds > limit) {
        groups.push(current);
        current = { index: groups.length + 1, seconds: 0, entries: [] };
      }
      current.entries.push({
        slug: collection.slug,
        manifestFingerprint: collection.manifest_fingerprint,
        collectionTitle: collection.title || collection.slug,
        name: track.name,
        title: track.title || track.name,
        seconds,
        duration: track.duration || "",
      });
      current.seconds += seconds;
    }
  }
  if (current.entries.length) groups.push(current);
  return groups;
}

export function addATonieWorth(tracks, chosenNames, limitSeconds) {
  // One press adds the next Tonie's worth of chapters, starting after the last
  // one already ticked. Starting after the LAST tick rather than the first gap
  // is what makes repeated presses read as "and another Tonie": an operator
  // who deliberately skipped a chapter does not want it offered back.
  const chosen = new Set(chosenNames || []);
  const limit = Number(limitSeconds) || 0;
  const list = tracks || [];
  let start = 0;
  for (let index = 0; index < list.length; index += 1) {
    if (chosen.has(list[index].name)) start = index + 1;
  }
  let seconds = 0;
  for (let index = start; index < list.length; index += 1) {
    const take = Number(list[index].seconds || 0);
    // The first chapter is always taken. A chapter longer than a whole Tonie
    // can exist when Forge ran with Split off, and skipping it would leave
    // this control dead on that collection forever.
    if (index > start && seconds + take > limit) break;
    chosen.add(list[index].name);
    seconds += take;
  }
  // Manifest order, and only names the collection still holds, because this
  // answer goes straight into the selection and from there into a payload.
  return list.filter((track) => chosen.has(track.name)).map((track) => track.name);
}

export function groupSources(group) {
  // Consecutive entries from one collection collapse into one source. The
  // server flattens sources in order, so the collapse changes nothing it sees;
  // it just keeps the payload readable.
  const sources = [];
  for (const entry of group.entries) {
    const last = sources[sources.length - 1];
    if (last && last.slug === entry.slug) last.files.push(entry.name);
    else sources.push({ slug: entry.slug, manifest_fingerprint: entry.manifestFingerprint, files: [entry.name] });
  }
  return sources;
}

export function tonieFreeSeconds(tonie, limitSeconds) {
  // The Tonie's own remaining space: the usable limit minus what is on it now.
  // This is a property of the box, so it does not move when the operator picks
  // an effect. The picker prints this, because "free space" that grew because
  // Replace everything was ticked describes the operation, not the Tonie, and
  // an operator reading it cannot tell how full the box actually is.
  const present = Number(tonie?.seconds_present ?? tonie?.secondsPresent ?? 0);
  return Math.max(0, Number(limitSeconds) - present);
}

export function tonieCapacity(tonie, groupSeconds, replaceExisting, limitSeconds) {
  // The fit budget, which DOES move with the effect: a replace clears the box
  // first, so the whole usable limit is available to it. Every fit check runs
  // against this, never against tonieFreeSeconds.
  const present = Number(tonie?.seconds_present ?? tonie?.secondsPresent ?? 0);
  const availableSeconds = replaceExisting
    ? Number(limitSeconds)
    : tonieFreeSeconds(tonie, limitSeconds);
  const projectedSeconds = replaceExisting ? Number(groupSeconds) : present + Number(groupSeconds);
  return {
    availableSeconds,
    projectedSeconds,
    fits: Number(groupSeconds) <= availableSeconds,
  };
}

export function sendCapacityLimit(status = {}) {
  return Number(status.usable_limit_seconds || 0);
}

export function selectionProblems(groups, selections, limitSeconds, picked = []) {
  const problems = [];
  // A forged collection can still have zero tracks (every chapter removed on
  // its own page), and packSelection silently drops it: it contributes no
  // entries and no group, so the loop below never sees it. Left unchecked,
  // ticking only empty collections leaves `groups` empty, every check below
  // passes on an empty list, and Send posts a batch with no assignments.
  for (const collection of picked) {
    if (!(collection.tracks || []).length) {
      problems.push(`${collection.title || collection.slug} has no chapters and cannot be sent.`);
    }
  }
  const chosen = [];
  groups.forEach((group, index) => {
    const selection = selections[index];
    if (!selection?.tonie) {
      problems.push(`Group ${group.index} has no Creative Tonie chosen.`);
      return;
    }
    const key = `${selection.tonie.householdId}/${selection.tonie.id}`;
    if (chosen.includes(key)) {
      problems.push(`${tonieLabel(selection.tonie)} is chosen for more than one group.`);
    }
    chosen.push(key);
    const capacity = tonieCapacity(selection.tonie, group.seconds, selection.replaceExisting, limitSeconds);
    if (!capacity.fits) {
      problems.push(`Group ${group.index} does not fit ${tonieLabel(selection.tonie)}. Choose Replace everything, or another Tonie.`);
    }
  });
  return problems;
}

export function buildPushBatchPayload(groups, selections, operationKey) {
  return {
    operation_key: operationKey,
    assignments: groups.map((group, index) => {
      const { tonie, replaceExisting } = selections[index];
      return {
        household_id: tonie.householdId,
        tonie_id: tonie.id,
        replace: replaceExisting,
        remote_chapters: (tonie.chapters || []).map(({ id, title }) => ({ id, title: title || "" })),
        sources: groupSources(group),
      };
    }),
  };
}

export function createSendAttempt({
  payload,
  confirm,
  request,
  signal = null,
  setPending = () => {},
  onReceipt = () => {},
  onFailure = () => {},
}) {
  // One selection, one operation key, one request at a time. A second click
  // while a send is in flight must not queue a second batch: a Tonie write has
  // no undo, so a duplicate upload is not something the operator can take back.
  let inFlight = false;

  async function send({ needsConfirmation }) {
    if (inFlight || signal?.aborted) return false;
    inFlight = true;
    setPending(true);
    try {
      if (needsConfirmation && !await confirm()) return false;
      if (signal?.aborted) return false;
      const receipt = await request("/api/push/batch", {
        method: "POST",
        body: JSON.stringify(payload),
        ...(signal ? { signal } : {}),
      });
      if (signal?.aborted) return false;
      await onReceipt(receipt);
      return receipt;
    } catch (error) {
      if (!signal?.aborted) await onFailure(error);
      return null;
    } finally {
      inFlight = false;
      if (!signal?.aborted) setPending(false);
    }
  }

  return {
    payload,
    // Retry reuses the same payload, and so the same operation key, which is
    // what makes the server's idempotency digest able to recognise it.
    submit: () => send({ needsConfirmation: true }),
    retry: () => send({ needsConfirmation: false }),
    get inFlight() { return inFlight; },
  };
}

export function membershipSignature(groups) {
  // Every input the sources are built from: which collection, at which
  // fingerprint, which file, in which group. Target and effect are excluded
  // because they reset on their own. Two selections with the same signature
  // build identical sources and may share an operation key. Anything else is a
  // different operation, and reusing a key across two different operations is
  // exactly what the server's idempotency digest answers with a 409.
  return JSON.stringify(groups.map((group) => (
    group.entries.map((entry) => [entry.slug, entry.manifestFingerprint, entry.name])
  )));
}

export function rebindTargets(selections, tonies) {
  // Point every chosen target at the freshly fetched object, or drop it if the
  // Tonie is gone. A retained pre-refresh object validates capacity against
  // stale free space and sends a stale remote_chapters precondition, so the
  // refresh would make the next send fail rather than succeed.
  for (const choice of selections) {
    if (!choice.tonie) continue;
    choice.tonie = (tonies || []).find((tonie) => (
      tonie.householdId === choice.tonie.householdId && tonie.id === choice.tonie.id
    )) || null;
  }
  return selections;
}

export function newOperationKey() {
  return globalThis.crypto?.randomUUID?.() || `push-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}


export function tonieJobKey(householdId, tonieId) {
  return `${householdId}/${tonieId}`;
}


export function activeSendsByTonie(jobs) {
  // A push payload already names its target, so nothing new has to be stored
  // to put a running send on the row it is sending to.
  const map = new Map();
  for (const job of jobs || []) {
    if (job?.kind !== "push") continue;
    if (job.status !== "queued" && job.status !== "running") continue;
    const payload = job.payload || {};
    if (!payload.household_id || !payload.tonie_id) continue;
    const key = tonieJobKey(payload.household_id, payload.tonie_id);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(job);
  }
  for (const entries of map.values()) entries.sort((a, b) => a.id - b.id);
  return map;
}


export function sendJobView(job) {
  // The message is the worker's own sentence, shown verbatim. Nothing here
  // parses "7/30" back out of it: the bar's number comes from the column the
  // worker wrote, so the words and the figure can never disagree.
  const reported = job?.progress_percent;
  // Number(null) is 0, not NaN, so a null column would otherwise render as a
  // determinate bar sitting at 0%: a measured figure where there is none.
  // Absent has to be rejected before the range check, not by it.
  const percent = Number(reported);
  const determinate = reported != null
    && Number.isFinite(percent) && percent >= 0 && percent <= 100;
  const queued = job?.status === "queued";
  return {
    phase: job?.phase || job?.status || "queued",
    label: queued ? "Queued" : "Sending",
    message: job?.progress || (queued ? "Waiting for a worker" : "Working"),
    mode: determinate ? "determinate" : "indeterminate",
    percent: determinate ? percent : null,
  };
}
