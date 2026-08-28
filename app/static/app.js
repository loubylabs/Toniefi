import { api } from "./api.js";
import { createActivityScreen } from "./activity.js";
import { createDeskScreen } from "./desk.js";
import { icon } from "./icons.js";
import { createLibraryScreen } from "./library.js";
import { createReviewScreen } from "./review.js";
import { createRouter } from "./router.js";
import { createSettingsScreen } from "./settings.js";
import { createPersistentAudioPlayer, element, notify, replace } from "./shared.js";
import { createToniesScreen } from "./tonies.js";

const ACTIVE_REFRESH_MS = 2500;
const RESTING_REFRESH_MS = 30000;

const routeDefinitions = [
  { name: "desk", path: "/" },
  { name: "desk", path: "/desk" },
  { name: "review", path: "/review/:slug?" },
  { name: "library", path: "/library" },
  { name: "tonies", path: "/tonies" },
  { name: "activity", path: "/activity" },
  { name: "settings", path: "/settings" },
];

function injectIcons() {
  document.querySelectorAll("[data-icon]").forEach((host) => {
    host.innerHTML = icon(host.dataset.icon);
  });
}

function hasActiveJobs(jobs) {
  return jobs.some((job) => job.status === "queued" || job.status === "running");
}

function createRefreshCoordinator() {
  const listeners = new Set();
  let timer = null;
  let inFlight = null;
  let requestedAgain = false;
  let failureNotice = "";
  let snapshot = {
    status: null,
    jobs: [],
    collections: [],
    stale: [],
    errors: {},
    loadedAt: null,
  };

  function schedule() {
    window.clearTimeout(timer);
    timer = null;
    if (document.hidden) return;
    const delay = hasActiveJobs(snapshot.jobs) ? ACTIVE_REFRESH_MS : RESTING_REFRESH_MS;
    timer = window.setTimeout(() => request(), delay);
  }

  async function load() {
    const names = ["status", "jobs", "collections"];
    const results = await Promise.allSettled([
      api("/api/status"),
      api("/api/jobs"),
      api("/api/collections"),
    ]);
    const next = {
      ...snapshot,
      stale: [],
      errors: {},
      loadedAt: new Date(),
    };

    results.forEach((result, index) => {
      const name = names[index];
      if (result.status === "fulfilled") {
        next[name] = result.value;
      } else {
        next.stale.push(name);
        next.errors[name] = result.reason;
      }
    });
    snapshot = next;

    for (const listener of listeners) {
      try {
        listener(snapshot);
      } catch (error) {
        console.error("Refresh listener failed", error);
      }
    }

    if (next.stale.length === names.length) {
      const message = "Current TonieFi state could not be refreshed. Existing information may be out of date.";
      if (failureNotice !== message) notify(message, { kind: "failure", timeout: 0 });
      failureNotice = message;
    } else {
      failureNotice = "";
    }
    return snapshot;
  }

  function request() {
    if (document.hidden) {
      requestedAgain = true;
      return Promise.resolve(snapshot);
    }
    if (inFlight) {
      requestedAgain = true;
      return inFlight;
    }
    window.clearTimeout(timer);
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
    return inFlight;
  }

  function subscribe(listener) {
    listeners.add(listener);
    if (snapshot.loadedAt) listener(snapshot);
    return () => listeners.delete(listener);
  }

  function stop() {
    window.clearTimeout(timer);
    timer = null;
    listeners.clear();
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      window.clearTimeout(timer);
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

function updateShell(snapshot) {
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

  const ready = snapshot.collections.filter((collection) => collection.stage === "forged").length;
  const reviewCount = document.getElementById("reviewCount");
  if (reviewCount) {
    reviewCount.textContent = String(ready);
    reviewCount.hidden = ready === 0;
    reviewCount.setAttribute("aria-label", `${ready} ${ready === 1 ? "collection" : "collections"} ready`);
  }

  const active = snapshot.jobs.filter((job) => job.status === "queued" || job.status === "running").length;
  const activityStatus = document.getElementById("activityStatus");
  const activityCount = document.getElementById("activityCount");
  if (activityStatus && activityCount) {
    activityCount.textContent = String(active);
    activityStatus.hidden = active === 0;
    activityStatus.setAttribute("aria-label", `${active} ${active === 1 ? "job" : "jobs"} active`);
  }
}

function initializeMobileMore() {
  const button = document.getElementById("mobileMoreButton");
  const menu = document.getElementById("mobileMoreMenu");
  if (!button || !menu) return;

  function close({ restoreFocus = false } = {}) {
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
    if (restoreFocus) button.focus();
  }

  button.addEventListener("click", () => {
    const opening = !menu.hidden;
    menu.hidden = opening;
    button.setAttribute("aria-expanded", String(!opening));
    if (!opening) menu.querySelector("a")?.focus();
  });
  menu.addEventListener("click", () => close());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !menu.hidden) close({ restoreFocus: true });
  });
  document.addEventListener("click", (event) => {
    if (!menu.hidden && !menu.contains(event.target) && !button.contains(event.target)) close();
  });
  document.addEventListener("toniefi:routechange", (event) => {
    const moreActive = event.detail.name === "activity" || event.detail.name === "settings";
    button.toggleAttribute("data-active", moreActive);
    if (moreActive) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
    close();
  });
}

export const refresh = createRefreshCoordinator();
export const player = createPersistentAudioPlayer({ host: document.getElementById("audioPlayerHost") });

export const router = createRouter(routeDefinitions, {
  onError(error) {
    console.error(error);
    notify("This part of the desk could not be opened. Try the navigation again.", {
      kind: "failure",
      timeout: 0,
    });
  },
});

router.register("desk", createDeskScreen({ request: api, refresh }));
router.register("review", createReviewScreen({ request: api, refresh, player }));
router.register("library", createLibraryScreen({ request: api, refresh }));
router.register("tonies", createToniesScreen({ request: api }));
router.register("activity", createActivityScreen({ request: api, refresh }));
router.register("settings", createSettingsScreen({ request: api, refresh }));
injectIcons();
initializeMobileMore();
refresh.subscribe(updateShell);
router.start();
refresh.request();
