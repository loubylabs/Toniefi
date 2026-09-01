import assert from "node:assert/strict";
import test from "node:test";

import { createForgeDefaultsCoordinator } from "../../app/static/forge-defaults.js";

const profile = (useChapters) => ({
  use_chapters: useChapters,
  normalize: true,
  clean_titles: true,
  trim_head: 0,
  trim_tail: 0,
  split_oversized: true,
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function nextTurn() {
  await Promise.resolve();
  await Promise.resolve();
}

test("Forge default writes stay ordered across screen mounts", async () => {
  const firstWrite = deferred();
  const secondWrite = deferred();
  const requests = [];
  const request = (path, options) => {
    requests.push({ path, options });
    return requests.length === 1 ? firstWrite.promise : secondWrite.promise;
  };
  const coordinator = createForgeDefaultsCoordinator({ request });

  const firstMountSave = coordinator.save(profile(false));
  const secondMountSave = coordinator.save(profile(true));
  await nextTurn();

  assert.equal(requests.length, 1);
  assert.deepEqual(JSON.parse(requests[0].options.body), profile(false));

  firstWrite.resolve(profile(false));
  await firstMountSave;
  await nextTurn();

  assert.equal(requests.length, 2);
  assert.deepEqual(JSON.parse(requests[1].options.body), profile(true));

  secondWrite.resolve(profile(true));
  await secondMountSave;

  assert.deepEqual(await coordinator.load(), profile(true));
  assert.equal(requests.length, 2);
});

test("Forge default load waits for a pending write", async () => {
  const pendingWrite = deferred();
  const requests = [];
  const coordinator = createForgeDefaultsCoordinator({
    request(path, options) {
      requests.push({ path, options });
      return pendingWrite.promise;
    },
  });

  const save = coordinator.save(profile(false));
  const load = coordinator.load();
  let loadSettled = false;
  load.finally(() => { loadSettled = true; });
  await nextTurn();

  assert.equal(loadSettled, false);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.method, "PUT");

  pendingWrite.resolve(profile(false));

  assert.deepEqual(await load, profile(false));
  await save;
  assert.equal(requests.length, 1);
});

test("Forge default load drains a write queued while it waits", async () => {
  const firstWrite = deferred();
  const secondWrite = deferred();
  const requests = [];
  const coordinator = createForgeDefaultsCoordinator({
    request(path, options) {
      requests.push({ path, options });
      return requests.length === 1 ? firstWrite.promise : secondWrite.promise;
    },
  });

  const firstSave = coordinator.save(profile(false));
  const load = coordinator.load();
  await nextTurn();
  const secondSave = coordinator.save(profile(true));

  firstWrite.resolve(profile(false));
  await firstSave;
  await nextTurn();

  let loadSettled = false;
  load.finally(() => { loadSettled = true; });
  assert.equal(requests.length, 2);
  assert.equal(loadSettled, false);

  secondWrite.resolve(profile(true));

  assert.deepEqual(await load, profile(true));
  await secondSave;
  assert.equal(requests.length, 2);
});

test("a stale Forge default load cannot replace a newer saved profile", async () => {
  const staleRead = deferred();
  const pendingWrite = deferred();
  const requests = [];
  const coordinator = createForgeDefaultsCoordinator({
    request(path, options) {
      requests.push({ path, options });
      return options?.method === "PUT" ? pendingWrite.promise : staleRead.promise;
    },
  });

  const load = coordinator.load();
  await nextTurn();
  const save = coordinator.save(profile(true));
  await nextTurn();

  assert.deepEqual(requests.map(({ options }) => options?.method || "GET"), ["GET", "PUT"]);

  pendingWrite.resolve(profile(true));
  await save;
  staleRead.resolve(profile(false));

  assert.deepEqual(await load, profile(true));
  assert.deepEqual(await coordinator.load(), profile(true));
  assert.equal(requests.length, 2);
});

test("a stale Forge default load cannot clear later write uncertainty", async () => {
  const staleRead = deferred();
  const pendingWrite = deferred();
  const refreshedRead = deferred();
  const requests = [];
  let getCount = 0;
  const coordinator = createForgeDefaultsCoordinator({
    request(path, options) {
      requests.push({ path, options });
      if (options?.method === "PUT") return pendingWrite.promise;
      getCount += 1;
      return getCount === 1 ? staleRead.promise : refreshedRead.promise;
    },
  });

  const load = coordinator.load();
  await nextTurn();
  const save = coordinator.save(profile(false));
  await nextTurn();
  pendingWrite.reject(new Error("offline"));
  assert.equal(await save, null);

  staleRead.resolve(profile(false));
  refreshedRead.resolve(profile(true));

  assert.deepEqual(await load, profile(true));
  assert.deepEqual(await coordinator.load(), profile(true));
  assert.deepEqual(requests.map(({ options }) => options?.method || "GET"), ["GET", "PUT", "GET"]);
  assert.equal(requests.length, 3);
});

test("a failed Forge default write refreshes before the next load", async () => {
  const writeFailure = new Error("save failed");
  const requests = [];
  const saveErrors = [];
  const coordinator = createForgeDefaultsCoordinator({
    request(path, options) {
      requests.push({ path, options });
      if (requests.length === 1) return Promise.resolve(profile(true));
      if (requests.length === 2) return Promise.reject(writeFailure);
      return Promise.resolve(profile(true));
    },
    onSaveError(error) {
      saveErrors.push(error);
    },
  });

  assert.deepEqual(await coordinator.load(), profile(true));
  assert.equal(await coordinator.save(profile(false)), null);
  assert.deepEqual(saveErrors, [writeFailure]);

  assert.deepEqual(await coordinator.load(), profile(true));
  assert.deepEqual(requests.map(({ options }) => options?.method || "GET"), ["GET", "PUT", "GET"]);
});

test("Forge default writes continue after a failed write", async () => {
  const firstWrite = deferred();
  const secondWrite = deferred();
  const requests = [];
  const saveErrors = [];
  const coordinator = createForgeDefaultsCoordinator({
    request(path, options) {
      requests.push({ path, options });
      return requests.length === 1 ? firstWrite.promise : secondWrite.promise;
    },
    onSaveError(error) {
      saveErrors.push(error);
    },
  });

  const failedSave = coordinator.save(profile(false));
  const laterSave = coordinator.save(profile(true));
  await nextTurn();
  const error = new Error("offline");
  firstWrite.reject(error);

  assert.equal(await failedSave, null);
  await nextTurn();
  assert.equal(requests.length, 2);

  secondWrite.resolve(profile(true));

  assert.deepEqual(await laterSave, profile(true));
  assert.deepEqual(await coordinator.load(), profile(true));
  assert.deepEqual(saveErrors, [error]);
  assert.equal(requests.length, 2);
});

test("throwing save error observers cannot break the write queue", async () => {
  const firstWrite = deferred();
  const secondWrite = deferred();
  const requests = [];
  const observerError = new Error("observer failed");
  const coordinator = createForgeDefaultsCoordinator({
    request(path, options) {
      requests.push({ path, options });
      return requests.length === 1 ? firstWrite.promise : secondWrite.promise;
    },
    onSaveError() {
      throw observerError;
    },
  });

  const failedSave = coordinator.save(profile(false));
  await nextTurn();
  firstWrite.reject(new Error("offline"));

  assert.equal(await failedSave, null);

  const laterSave = coordinator.save(profile(true));
  await nextTurn();
  assert.equal(requests.length, 2);
  secondWrite.resolve(profile(true));

  assert.deepEqual(await laterSave, profile(true));
  assert.deepEqual(await coordinator.load(), profile(true));
  assert.equal(requests.length, 2);
});

test("Forge default writes use unload-safe delivery", async () => {
  const requests = [];
  const selected = profile(false);
  const coordinator = createForgeDefaultsCoordinator({
    request(path, options) {
      requests.push({ path, options });
      return Promise.resolve(profile(false));
    },
  });

  const save = coordinator.save(selected);
  selected.use_chapters = true;
  await save;

  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, "/api/settings/forge-defaults");
  assert.deepEqual(requests[0].options, {
    method: "PUT",
    body: JSON.stringify(profile(false)),
    keepalive: true,
  });
  assert.equal(Object.hasOwn(requests[0].options, "signal"), false);
});

test("Forge default profiles are defensive copies", async () => {
  const serverProfile = profile(true);
  let requestCount = 0;
  const coordinator = createForgeDefaultsCoordinator({
    request() {
      requestCount += 1;
      return Promise.resolve(serverProfile);
    },
  });

  const firstLoad = await coordinator.load();
  firstLoad.use_chapters = false;
  serverProfile.normalize = false;

  assert.deepEqual(await coordinator.load(), profile(true));
  assert.equal(requestCount, 1);
});
