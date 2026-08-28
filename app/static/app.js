import { api, scopeRequest } from "./api.js";
import { createActivityScreen } from "./activity.js";
import { createDeskScreen } from "./desk.js";
import { icon } from "./icons.js";
import { createLibraryScreen } from "./library.js";
import { createRefreshCoordinator, scopeRefresh, updateShell } from "./refresh.js";
import { createReviewScreen } from "./review.js";
import { createRouter } from "./router.js";
import { createSettingsScreen } from "./settings.js";
import { createPersistentAudioPlayer, notify } from "./shared.js";
import { createToniesScreen } from "./tonies.js";

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

function scopedScreen(createScreen, dependencies) {
  return (context) => createScreen({
    ...dependencies,
    request: scopeRequest(dependencies.request, context.signal),
    ...(dependencies.refresh
      ? { refresh: scopeRefresh(dependencies.refresh, context.signal) }
      : {}),
  })(context);
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

router.register("desk", scopedScreen(createDeskScreen, { request: api, refresh }));
router.register("review", scopedScreen(createReviewScreen, { request: api, refresh, player }));
router.register("library", scopedScreen(createLibraryScreen, { request: api, refresh }));
router.register("tonies", scopedScreen(createToniesScreen, { request: api }));
router.register("activity", scopedScreen(createActivityScreen, { request: api, refresh }));
router.register("settings", scopedScreen(createSettingsScreen, { request: api, refresh }));
injectIcons();
initializeMobileMore();
refresh.subscribe(updateShell);
router.start();
refresh.request();
