import { api } from "./api.js";
import { icon } from "./icons.js";
import { element, notify, replace } from "./shared.js";

const ACTIVE_REFRESH_MS = 2500;
const RESTING_REFRESH_MS = 30000;
const RESOURCES = [
  ["status", "/api/status"],
  ["jobs", "/api/jobs"],
  ["history", "/api/jobs/history"],
  ["collections", "/api/collections"],
  ["dismissals", "/api/desk/dismissals"],
];

function hasActiveJobs(jobs) {
  return jobs.some((job) => job.status === "queued" || job.status === "running");
}

function abortError() {
  if (typeof DOMException === "function") return new DOMException("The route was closed.", "AbortError");
  const error = new Error("The route was closed.");
  error.name = "AbortError";
  return error;
}

function routePromise(promise, signal) {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(abortError());
  return new Promise((resolve, reject) => {
    const abort = () => reject(abortError());
    signal.addEventListener("abort", abort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
}

export function scopeRefresh(refresh, signal) {
  if (!signal) return refresh;
  return {
    get snapshot() { return refresh.snapshot; },
    request: () => refresh.request({ signal }),
    subscribe(listener) {
      const guarded = (snapshot) => {
        if (!signal.aborted) listener(snapshot);
      };
      const unsubscribe = refresh.subscribe(guarded);
      let active = true;
      const dispose = () => {
        if (!active) return;
        active = false;
        unsubscribe();
        signal.removeEventListener("abort", dispose);
      };
      signal.addEventListener("abort", dispose, { once: true });
      return dispose;
    },
  };
}

export function createRefreshCoordinator({
  request: loadResource = api,
  documentObject = document,
  windowObject = window,
  notifyFailure = (message) => notify(message, { kind: "failure", timeout: 0 }),
} = {}) {
  const listeners = new Set();
  let timer = null;
  let inFlight = null;
  let requestedAgain = false;
  let failureNotice = "";
  let snapshot = {
    status: null,
    jobs: [],
    history: [],
    collections: [],
    dismissals: {},
    stale: [],
    errors: {},
    loadedAt: null,
  };

  function publish(name, result) {
    const stale = new Set(snapshot.stale);
    const errors = { ...snapshot.errors };
    if (result.status === "fulfilled") {
      stale.delete(name);
      delete errors[name];
      snapshot = { ...snapshot, [name]: result.value, stale: [...stale], errors, loadedAt: new Date() };
    } else {
      stale.add(name);
      errors[name] = result.reason;
      snapshot = { ...snapshot, stale: [...stale], errors, loadedAt: new Date() };
    }
    for (const listener of listeners) {
      try {
        listener(snapshot);
      } catch (error) {
        console.error("Refresh listener failed", error);
      }
    }
  }

  function schedule() {
    windowObject.clearTimeout(timer);
    timer = null;
    if (documentObject.hidden) return;
    const delay = hasActiveJobs(snapshot.jobs) ? ACTIVE_REFRESH_MS : RESTING_REFRESH_MS;
    timer = windowObject.setTimeout(() => request(), delay);
  }

  async function load() {
    await Promise.all(RESOURCES.map(async ([name, path]) => {
      try {
        publish(name, { status: "fulfilled", value: await loadResource(path) });
      } catch (error) {
        publish(name, { status: "rejected", reason: error });
      }
    }));
    if (snapshot.stale.length === RESOURCES.length) {
      const message = "Current TonieFi state could not be refreshed. Existing information may be out of date.";
      if (failureNotice !== message) notifyFailure(message);
      failureNotice = message;
    } else {
      failureNotice = "";
    }
    return snapshot;
  }

  function request({ signal } = {}) {
    if (documentObject.hidden) {
      requestedAgain = true;
      return routePromise(Promise.resolve(snapshot), signal);
    }
    if (!inFlight) {
      windowObject.clearTimeout(timer);
      timer = null;
      inFlight = load().finally(() => {
        inFlight = null;
        if (requestedAgain) {
          requestedAgain = false;
          request();
        } else {
          schedule();
        }
      });
    } else {
      requestedAgain = true;
    }
    return routePromise(inFlight, signal);
  }

  function subscribe(listener) {
    listeners.add(listener);
    if (snapshot.loadedAt) listener(snapshot);
    return () => listeners.delete(listener);
  }

  function stop() {
    windowObject.clearTimeout(timer);
    timer = null;
    listeners.clear();
  }

  documentObject.addEventListener("visibilitychange", () => {
    if (documentObject.hidden) {
      windowObject.clearTimeout(timer);
      timer = null;
    } else {
      requestedAgain = false;
      request();
    }
  });

  return {
    request,
    subscribe,
    stop,
    get snapshot() { return snapshot; },
  };
}

export function updateShell(snapshot) {
  const credentials = snapshot.status?.credentials;
  const summary = document.getElementById("accountSummary");
  if (summary && credentials) {
    const configured = Boolean(credentials.configured);
    const source = credentials.source === "environment"
      ? "Environment credentials"
      : credentials.source === "saved"
        ? "Locally saved credentials"
        : "Open Settings to connect";
    summary.dataset.state = configured ? "configured" : "unconfigured";
    const symbol = element("span", { className: "account-summary-icon", "aria-hidden": "true" });
    symbol.innerHTML = icon(configured ? "check" : "account");
    const copy = element("span", {}, [
      element("strong", { text: configured ? "Account configured" : "Account not configured" }),
      element("small", { text: credentials.username || source }),
    ]);
    replace(summary, symbol, copy);
  }

  const active = snapshot.jobs.filter((job) => job.status === "queued" || job.status === "running").length;
  // Both navigations, because only one of them is on screen at a time and the
  // phone was the one showing nothing.
  for (const [statusId, countId] of [
    ["activityStatus", "activityCount"],
    ["mobileActivityStatus", "mobileActivityCount"],
  ]) {
    const status = document.getElementById(statusId);
    const count = document.getElementById(countId);
    if (!status || !count) continue;
    count.textContent = String(active);
    status.hidden = active === 0;
    status.setAttribute("aria-label", `${active} ${active === 1 ? "job" : "jobs"} active`);
  }
  const moreStatus = document.getElementById("mobileMoreStatus");
  if (moreStatus) moreStatus.hidden = active === 0;
}
