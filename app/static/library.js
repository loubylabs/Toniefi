import { api } from "./api.js";
import { icon } from "./icons.js";
import {
  activeSendsByTonie,
  addATonieWorth,
  buildPushBatchPayload,
  createSendAttempt,
  membershipSignature,
  newOperationKey,
  packSelection,
  rebindTargets,
  selectionProblems,
  sendCapacityLimit,
  sendJobView,
  tonieCapacity,
  tonieFreeSeconds,
  tonieJobKey,
} from "./send.js";
import {
  announce,
  createMutationController,
  element,
  humanDuration,
  notify,
  rememberFocus,
  replace,
  restoreFocus,
  setBusy,
  showConfirmDialog,
  snapshotRefreshOutcome,
  tonieJacket,
  tonieLabel,
} from "./shared.js";

export function filterCollectionsByTitle(collections, query) {
  const needle = String(query || "").trim().toLocaleLowerCase();
  if (!needle) return collections.slice();
  return collections.filter((collection) => String(collection.title || "").toLocaleLowerCase().includes(needle));
}

export function selectableCollections(collections, jobs = []) {
  // Only a forged collection can be sent, and forgePreparationState is the one
  // place that decides what "ready" means.
  return collections.filter((collection) => forgePreparationState(collection, jobs).state === "ready");
}

export function createSelectionState() {
  // A map of slug to the chapter names chosen inside it, never an "all"
  // sentinel. The send order is the Library's own order and each story's own
  // manifest order, both resolved at read time, so nothing depends on the
  // order the operator happened to tick. Names are explicit because a story
  // that changes under a selection already fails on its manifest fingerprint,
  // and explicit names let a partial selection survive a Library refresh the
  // same way a whole one does.
  const chosen = new Map();
  const trackNames = (collection) => (collection.tracks || []).map((track) => track.name);
  const picked = (slug) => chosen.get(slug) || new Set();
  // Always stored in manifest order and always filtered to chapters that still
  // exist, so a stale name cannot reach a payload.
  const keep = (collection, wanted) => trackNames(collection).filter((name) => wanted.has(name));

  function put(slug, names) {
    if (names.length) chosen.set(slug, new Set(names));
    else chosen.delete(slug);
  }

  function state(collection) {
    if (!chosen.has(collection.slug)) return "none";
    const all = trackNames(collection);
    const held = keep(collection, picked(collection.slug)).length;
    // A registered story holding nothing still reads as ticked, whether it
    // never had a chapter or lost every one after it was ticked. The operator
    // can see what they ticked and untick it, and the Send bar is where it
    // gets refused by name.
    if (!held || held === all.length) return "all";
    return "some";
  }

  return {
    state,
    hasTrack: (slug, name) => picked(slug).has(name),
    chosenCount: (collection) => keep(collection, picked(collection.slug)).length,
    toggle(collection) {
      if (state(collection) === "all") chosen.delete(collection.slug);
      else chosen.set(collection.slug, new Set(trackNames(collection)));
    },
    toggleTrack(collection, name) {
      const wanted = new Set(picked(collection.slug));
      if (wanted.has(name)) wanted.delete(name);
      else wanted.add(name);
      put(collection.slug, keep(collection, wanted));
    },
    setTracks(collection, names) {
      put(collection.slug, keep(collection, new Set(names)));
    },
    size: () => chosen.size,
    clear() { chosen.clear(); },
    ordered: (collections) => collections
      // A story whose chosen chapters have all vanished is kept, carrying no
      // tracks, so the Send bar's own guard can refuse it by name rather than
      // the selection quietly shrinking to nothing under the operator.
      .filter((collection) => chosen.has(collection.slug))
      .map((collection) => ({
        ...collection,
        tracks: (collection.tracks || []).filter((track) => picked(collection.slug).has(track.name)),
      })),
  };
}

export async function rescanCollections({ collections, request, refresh, signal = null }) {
  const outcomes = await Promise.allSettled(collections.map((collection) => (
    request(`/api/collections/${encodeURIComponent(collection.slug)}?refresh=true`, {
      ...(signal ? { signal } : {}),
    })
  )));
  const failures = outcomes.filter((outcome) => outcome.status === "rejected");
  const snapshot = await refresh.request();
  if (snapshotRefreshOutcome(snapshot, "collections").stale) {
    failures.push({ status: "rejected", reason: snapshot.errors?.collections });
  }
  return { failures, snapshot };
}

function iconNode(name, className = "") {
  const node = element("span", { className, "aria-hidden": "true" });
  node.innerHTML = icon(name);
  return node;
}

function initials(title) {
  const words = String(title || "Story").trim().split(/\s+/).filter(Boolean);
  return words.slice(0, 2).map((word) => word[0]?.toUpperCase() || "").join("") || "ST";
}

function collectionCover(collection) {
  if (collection.cover) {
    return element("img", {
      className: "library-cover",
      src: `/api/collections/${encodeURIComponent(collection.slug)}/cover`,
      alt: "",
      loading: "lazy",
    });
  }
  return element("span", {
    className: "library-cover collection-cover-fallback",
    text: initials(collection.title),
    "aria-hidden": "true",
  });
}

function stageLabel(stage) {
  return stage === "forged" ? "Forge complete" : stage === "extracted" ? "Extracted" : stage || "Local";
}

// A playlist that held a private or deleted video arrives short. The chapters
// that did arrive are numbered straight through, so nothing else on the row
// shows the gap, and the operator would only find it by counting.
function skippedNote(collection) {
  const count = (collection?.skipped || []).length;
  if (!count) return null;
  return element("p", {
    className: "library-source",
    text: `${count} ${count === 1 ? "video was" : "videos were"} unavailable and left out.`,
  });
}

export function forgePreparationState(collection, jobs = []) {
  if (collection?.stage === "forged") return { state: "ready", error: "" };
  const job = jobs
    .filter((item) => item.kind === "forge" && item.payload?.slug === collection?.slug)
    .sort((left, right) => Number(right.id || 0) - Number(left.id || 0))[0];
  if (job?.status === "queued" || job?.status === "running") {
    return { state: "pending", error: "" };
  }
  if (job?.status === "failed") {
    return { state: "failed", error: job.error || "Forge stopped before preparation completed." };
  }
  return { state: "incomplete", error: "" };
}

export function createLibraryScreen({
  request = api,
  refresh,
} = {}) {
  if (!refresh) throw new Error("Library requires the application refresh coordinator.");

  return function renderLibrary({ workspace, signal }) {
    let active = true;
    let collections = refresh.snapshot.collections || [];
    let jobs = refresh.snapshot.jobs || [];
    let query = "";
    const selection = createSelectionState();
    const root = element("section", { className: "library-screen", "aria-labelledby": "library-title" });
    const titleGroup = element("div", {}, [
      element("h1", { id: "library-title", text: "Library" }),
      element("p", { text: "Every local collection stays available as an ordinary folder of audio files." }),
    ]);
    const rescan = element("button", {
      type: "button",
      className: "button button-secondary library-rescan",
      "data-focus-key": "library-rescan",
      "data-collection-mutation": "",
    }, [iconNode("refresh"), element("span", { text: "Rescan" })]);
    const header = element("div", { className: "screen-heading" }, [titleGroup, rescan]);
    const searchLabel = element("label", { for: "library-search", text: "Search collection titles" });
    const search = element("input", {
      id: "library-search",
      type: "search",
      placeholder: "Search by title",
      autocomplete: "off",
      "data-focus-key": "library-search",
    });
    const searchField = element("div", { className: "library-search-field" }, [
      iconNode("search"),
      element("div", { className: "form-field" }, [searchLabel, search]),
    ]);
    const stale = element("div", { className: "stale-notice", role: "status", hidden: true });
    const summary = element("p", { className: "library-summary", role: "status", "aria-live": "polite" });
    const list = element("ul", { className: "library-list" });
    const sendBar = element("section", {
      className: "library-send-bar",
      "aria-labelledby": "library-send-title",
      hidden: true,
    });
    let tonies = null;
    let toniesError = "";
    let selections = [];
    let operationKey = "";
    let signature = "";
    let sending = false;
    let announcedProblem = "";
    let targetsToken = 0;
    // What the bar becomes after a successful submit. Clearing the bar to
    // nothing was the wrong ending: it knows every group's membership and
    // target at the exact moment it used to throw both away.
    let receipt = null;
    // Which rows have their chapter list open. The render rebuilds the whole
    // list on every tick, so this cannot live in the DOM.
    const expanded = new Set();
    // Each open panel's own scroll position, keyed by slug so two open panels
    // never share one. The render rebuilds the whole library list on every
    // tick (see above), which would otherwise snap a long chapter list back
    // to its top on every single chapter the operator ticks.
    const chapterScroll = new Map();

    function showStale(message) {
      if (!active || signal?.aborted) return;
      const retry = element("button", { type: "button", className: "button button-secondary", text: "Retry" });
      retry.addEventListener("click", async () => {
        try {
          const snapshot = await refresh.request();
          if (!snapshotRefreshOutcome(snapshot, "collections").stale) onRefresh(snapshot);
          else showStale("Library information is still stale. The last available index remains visible.");
        } catch (error) {
          showStale(error.message);
        }
      });
      stale.hidden = false;
      replace(stale, element("strong", { text: "Library state may be stale" }), element("p", { text: message }), retry);
    }

    const mutation = createMutationController({
      root,
      signal,
      reload: async () => {
        const snapshot = await refresh.request();
        const outcome = snapshotRefreshOutcome(snapshot, "collections");
        if (outcome.stale) throw outcome.error || new Error("Library refresh failed.");
        return snapshot;
      },
      onReloaded: (snapshot) => onRefresh(snapshot),
      onStale: (error) => showStale(`${error.message} The current index remains visible.`),
    });

    function chapterPanel(collection, panelId) {
      const tracks = collection.tracks || [];
      const names = tracks.map((track) => track.name);
      // Nothing left to take once the final chapter is ticked, because the
      // fill always starts after the last tick.
      const isExhausted = () => !tracks.length || selection.hasTrack(collection.slug, names[names.length - 1]);
      function control(suffix, label, computeDisabled, act) {
        const focusKey = `library-${collection.slug}-chapters-${suffix}`;
        const button = element("button", {
          type: "button",
          className: `button button-secondary library-chapter-${suffix}`,
          disabled: sending || computeDisabled(),
          "data-focus-key": focusKey,
        }, [element("span", { text: label })]);
        button.addEventListener("click", () => {
          if (sending) return;
          act();
          // The press that took the last chapter, emptied the story, or filled
          // it can disable the very control that pressed it. Restoring to a
          // focus key the next render disables strands focus at the search
          // field instead (restoreFocus treats a disabled target as no
          // match), so land on the panel's own disclosure button, which never
          // disables while the panel is open.
          const next = computeDisabled() ? `library-${collection.slug}-chapters` : focusKey;
          if (selection.size() === 1 && tonies === null) loadTargets(next);
          render({ focusKey: next });
        });
        return button;
      }
      const controls = element("div", { className: "library-chapter-controls" }, [
        control("all", "All", () => selection.state(collection) === "all", () => selection.setTracks(collection, names)),
        control("none", "None", () => selection.state(collection) === "none", () => selection.setTracks(collection, [])),
        control("more", "Add a Tonie's worth", isExhausted, () => selection.setTracks(
          collection,
          addATonieWorth(tracks, names.filter((name) => selection.hasTrack(collection.slug, name)), limitSeconds()),
        )),
      ]);
      const rows = tracks.map((track, index) => {
        const boxId = `library-chapter-${collection.slug}-${index}`;
        const focusKey = `library-${collection.slug}-chapter-${index}`;
        const box = element("input", {
          id: boxId,
          type: "checkbox",
          className: "library-chapter-select",
          checked: selection.hasTrack(collection.slug, track.name),
          disabled: sending,
          "data-focus-key": focusKey,
        });
        box.addEventListener("change", () => {
          if (sending) return;
          selection.toggleTrack(collection, track.name);
          if (selection.size() === 1 && tonies === null) loadTargets(focusKey);
          render({ focusKey });
        });
        // A label bound to the box and holding it, the same shape the row tick
        // already uses, so the whole line is the hit target rather than a
        // 20px square.
        return element("li", { className: "library-chapter-row" }, [
          element("label", { className: "library-chapter-label", for: boxId }, [
            box,
            element("span", { className: "library-chapter-index", text: String(index + 1) }),
            element("span", { className: "library-chapter-title", text: track.title || track.name }),
            element("span", { className: "library-chapter-duration", text: track.duration || "" }),
          ]),
        ]);
      });
      return element("div", { className: "library-chapter-panel", id: panelId }, [
        controls,
        element("ol", {
          className: "library-chapter-list",
          "aria-label": `Chapters in ${collection.title || collection.slug}`,
          "data-collection-slug": collection.slug,
        }, rows),
      ]);
    }

    function collectionRow(collection, index, shown, sendable) {
      const titleId = `library-collection-${collection.slug}`;
      const preparation = forgePreparationState(collection, jobs);
      const primary = preparation.state === "ready"
        ? element("a", {
          className: "button button-primary",
          href: `/collection/${encodeURIComponent(collection.slug)}`,
          "data-route": "collection",
          "data-focus-key": `library-${collection.slug}-open`,
          "data-collection-mutation": "",
        }, [iconNode("library"), element("span", { text: "Open" })])
        : element("button", {
          type: "button",
          className: "button button-primary library-finish",
          disabled: preparation.state === "pending",
          "data-focus-key": `library-${collection.slug}-open`,
          "data-collection-mutation": "",
        }, [
          iconNode("forge"),
          element("span", { text: preparation.state === "pending" ? "Forge queued" : "Finish preparation" }),
        ]);
      if (preparation.state === "ready") {
        primary.addEventListener("click", (event) => {
          if (mutation.pending) event.preventDefault();
        });
      } else {
        primary.addEventListener("click", async () => {
          if (preparation.state === "pending" || mutation.pending || signal?.aborted) return;
          primary.disabled = true;
          try {
            const receipt = await request("/api/forge", {
              method: "POST",
              body: JSON.stringify({ slug: collection.slug }),
              signal,
            });
            if (!active || signal?.aborted) return;
            jobs = [{
              id: receipt.job_id,
              kind: "forge",
              status: "queued",
              payload: { slug: collection.slug },
            }, ...jobs];
            render({ focusKey: `library-${collection.slug}-open` });
            notify(`${collection.title || "Collection"} is queued to finish Forge.`, { kind: "success" });
            await refresh.request();
          } catch (error) {
            if (!active || signal?.aborted) return;
            jobs = [{
              id: Number.MAX_SAFE_INTEGER,
              kind: "forge",
              status: "failed",
              error: error.message,
              payload: { slug: collection.slug },
            }, ...jobs];
            render({ focusKey: `library-${collection.slug}-open` });
            notify(error.message, { kind: "failure", timeout: 0 });
          }
        });
      }
      // A plain link, so the browser streams the archive to disk itself. An
      // extracted collection has its audio on disk too, so this never waits
      // for Forge.
      const download = element("a", {
        className: "button button-secondary library-download",
        href: `/api/collections/${encodeURIComponent(collection.slug)}/download`,
        download: "",
        "data-focus-key": `library-${collection.slug}-download`,
        "data-collection-mutation": "",
      }, [iconNode("download"), element("span", { text: "Download" })]);
      download.addEventListener("click", (event) => {
        if (mutation.pending) event.preventDefault();
      });
      const removeButton = element("button", {
        type: "button",
        className: "button button-secondary library-delete",
        "data-focus-key": `library-${collection.slug}-delete`,
        "data-collection-mutation": "",
      }, [iconNode("trash"), element("span", { text: "Delete" })]);
      removeButton.addEventListener("click", async () => {
        const confirmed = await showConfirmDialog({
          title: `Delete ${collection.title || "this collection"}?`,
          message: `The local folder for ${collection.title || collection.slug} and every audio file inside it will be permanently removed. TonieFi cannot restore them.`,
          confirmLabel: "Delete collection and files",
          destructive: true,
        });
        removeButton.focus({ preventScroll: true });
        if (!confirmed) return;
        removeButton.disabled = true;
        const fallback = shown[index + 1] || shown[index - 1];
        try {
          const removed = await mutation.run(() => request(`/api/collections/${encodeURIComponent(collection.slug)}`, { method: "DELETE", signal }));
          if (!removed || !active || signal?.aborted) return;
          collections = collections.filter((item) => item.slug !== collection.slug);
          render({ focusKey: fallback ? `library-${fallback.slug}-open` : "library-search" });
          notify(`${collection.title || "The collection"} and its local audio files were deleted.`, { kind: "success" });
          announce(`${collection.title || "Collection"} deleted from the local library.`);
          await refresh.request();
        } catch (error) {
          if (!active || signal?.aborted) return;
          removeButton.disabled = false;
          notify(error.message, { kind: "failure", timeout: 0 });
          removeButton.focus({ preventScroll: true });
        }
      });
      const selectable = sendable.has(collection.slug);
      const tickId = `library-select-${collection.slug}`;
      const chosenState = selectable ? selection.state(collection) : "none";
      const tick = selectable
        ? element("input", {
          id: tickId,
          type: "checkbox",
          className: "library-select",
          checked: chosenState === "all",
          // The whole selection freezes for the duration of a send, the same
          // way Send and Clear already do. The payload was built from the ticks
          // as they stood, and the receipt clears every tick, so a story ticked
          // mid-flight would be neither sent nor kept.
          disabled: sending,
          "data-focus-key": `library-${collection.slug}-select`,
        })
        : null;
      if (tick) {
        // A property, not an attribute: HTML has no indeterminate attribute,
        // and aria-checked="mixed" is not valid on a native checkbox.
        tick.indeterminate = chosenState === "some";
        tick.addEventListener("change", () => {
          // disabled is the visible guard, and a stale render is one bug away
          // from leaving it wrong, so the freeze gets its own guard too.
          if (sending) return;
          selection.toggle(collection);
          if (selection.size() === 1 && tonies === null) loadTargets(`library-${collection.slug}-select`);
          render({ focusKey: `library-${collection.slug}-select` });
        });
      }
      // A label, not a div, and bound to the checkbox: the 20x20 box on its own
      // failed WCAG 2.2 AA 2.5.8, and the visually hidden label gave it no
      // target to grow into. The whole cell is now the hit area.
      const tickCell = tick
        ? element("label", {
          className: "library-select-cell",
          for: tickId,
        }, [tick, element("span", {
          className: "visually-hidden",
          text: `Select ${collection.title || collection.slug} to send`,
        })])
        : element("div", { className: "library-select-cell" });
      const facts = element("ul", { className: "collection-facts", "aria-label": "Collection facts" }, [
        element("li", { text: `${collection.track_count || 0} ${collection.track_count === 1 ? "chapter" : "chapters"}` }),
        element("li", { text: collection.total_duration || "No duration yet" }),
        element("li", { text: `${collection.tonies_needed || 0} ${collection.tonies_needed === 1 ? "Tonie" : "Tonies"} needed` }),
      ]);
      const panelId = `library-chapters-${collection.slug}`;
      const open = selectable && expanded.has(collection.slug);
      const chooseChapters = selectable
        ? element("button", {
          type: "button",
          className: "button button-secondary library-choose-chapters",
          "aria-expanded": String(Boolean(open)),
          // aria-controls names a live element only while the panel is open;
          // the id does not exist in the document while it is closed.
          "aria-controls": open ? panelId : null,
          "data-focus-key": `library-${collection.slug}-chapters`,
        }, [iconNode("more"), element("span", { text: open ? "Hide chapters" : "Choose chapters" })])
        : null;
      if (chooseChapters) {
        chooseChapters.addEventListener("click", () => {
          if (open) expanded.delete(collection.slug);
          else expanded.add(collection.slug);
          render({ focusKey: `library-${collection.slug}-chapters` });
          // Forgetting the position has to happen after render(), which
          // itself reads the outgoing DOM's scrollTop on every call: deleting
          // first would just have that read put it straight back.
          if (open) chapterScroll.delete(collection.slug);
        });
      }
      // Only while the selection is partial. "2 of 2" beside a fully ticked
      // row repeats what the tick already says, and "0 of 2" beside an
      // untouched one is noise on every row in the Library.
      const chapterCount = chosenState === "some"
        ? element("p", {
          className: "library-chapter-count",
          text: `${selection.chosenCount(collection)} of ${(collection.tracks || []).length} chapters selected`,
        })
        : null;
      const source = collection.url || collection.source || collection.path || "Local collection";
      const body = element("div", { className: "library-row-body" }, [
        element("div", { className: "library-row-heading" }, [
          element("h2", { id: titleId, text: collection.title || "Untitled collection" }),
          element("span", {
            className: "status-stamp",
            "data-status": collection.stage === "forged" ? "success" : "warning",
            text: stageLabel(collection.stage),
          }),
        ]),
        facts,
        chapterCount,
        element("p", { className: "library-source", text: source }),
        skippedNote(collection),
        preparation.state === "failed"
          ? element("p", { className: "inline-error", role: "alert", text: preparation.error })
          : null,
        element("div", { className: "library-row-actions" }, [primary, chooseChapters, download, removeButton]),
        open ? chapterPanel(collection, panelId) : null,
      ]);
      return element("li", { className: "library-row", "aria-labelledby": titleId }, [tickCell, collectionCover(collection), body]);
    }

    function render({ focusKey = "" } = {}) {
      if (!active || signal?.aborted) return;
      const token = focusKey ? { key: focusKey } : rememberFocus(root);
      // The list below is about to be torn down and rebuilt whatever branch
      // runs, which would otherwise reset every open chapter list's scroll to
      // its top. Read each one's current position before it goes.
      for (const node of list.querySelectorAll(".library-chapter-list")) {
        const slug = node.dataset.collectionSlug;
        if (slug) chapterScroll.set(slug, node.scrollTop || 0);
      }
      const shown = filterCollectionsByTitle(collections, query);
      if (!collections.length) {
        summary.textContent = "No local collections";
        replace(list, element("li", { className: "empty-state library-empty" }, [
          iconNode("library"),
          element("strong", { text: "Your local library is empty" }),
          element("p", { text: "Prepare a story on Desk, or add audio folders to the configured library path and Rescan." }),
          element("a", { className: "button button-primary", href: "/", "data-route": "desk" }, [
            iconNode("desk"), element("span", { text: "Go to Desk" }),
          ]),
        ]));
      } else if (!shown.length) {
        summary.textContent = `No titles match “${query.trim()}”`;
        replace(list, element("li", { className: "empty-state library-empty" }, [
          iconNode("search"),
          element("strong", { text: "No matching collection titles" }),
          element("p", { text: "Try a shorter title or clear the search." }),
          element("button", {
            type: "button",
            className: "button button-secondary",
            text: "Clear search",
            onclick: () => {
              query = "";
              search.value = "";
              render({ focusKey: "library-search" });
            },
          }),
        ]));
      } else {
        summary.textContent = `${shown.length} of ${collections.length} ${collections.length === 1 ? "collection" : "collections"}`;
        // One rule for what can be sent, and selectableCollections is it.
        const sendable = new Set(selectableCollections(shown, jobs).map((item) => item.slug));
        replace(list, ...shown.map((collection, index) => collectionRow(collection, index, shown, sendable)));
        // Put each open panel's scroll position back, now that its list is a
        // fresh node in the document again.
        for (const node of list.querySelectorAll(".library-chapter-list")) {
          const slug = node.dataset.collectionSlug;
          if (slug && chapterScroll.has(slug)) node.scrollTop = chapterScroll.get(slug);
        }
      }
      renderSendBar();
      // The send bar is the region the operator is watching while the POST is
      // in flight, so it is the region that reports being busy.
      sendBar.setAttribute("aria-busy", String(Boolean(sending)));
      mutation.sync();
      restoreFocus(token, { root, fallback: search });
    }

    function limitSeconds() {
      return sendCapacityLimit(refresh.snapshot.status || {});
    }

    // The caller says where focus belongs when the fetch settles, because the
    // first tick on a row loads targets too: restoring to the refresh button
    // there would take focus off the checkbox the operator just used.
    async function loadTargets(focusKey = "library-send-refresh") {
      // Two reads can be in flight at once, because Refresh targets stays live
      // while the automatic first load is still running. Only the newest answer
      // may land: an older one carries obsolete free space AND an obsolete
      // remote_chapters precondition, which would go straight into the payload.
      const token = targetsToken + 1;
      targetsToken = token;
      let loaded = null;
      let rejected = false;
      let failureMessage = "";
      try {
        loaded = await request("/api/tonies", { signal });
      } catch (error) {
        // Whether the request failed is its own fact, tracked independently
        // of the error's message text. A rejection can carry an empty
        // message (an ApiError built from a 503 body of `{"detail": []}`,
        // say), and testing `if (failure)` against that text would read the
        // empty string as no failure at all and fall into the success branch
        // below with `loaded` still null.
        rejected = true;
        failureMessage = error.message || "The Tonie Cloud did not explain what went wrong.";
      }
      if (token !== targetsToken) return;
      if (rejected) {
        // A failed request is not the operator deciding anything, so it must
        // not look like one. Leave `tonies` and every group's chosen target
        // exactly as they were: rebindTargets on an empty list would read as
        // "the operator's Tonie is gone" and null out a selection nobody
        // abandoned, the picker would print no options at all, and clearing
        // the key with it would strand a lost-response retry without the
        // safe 409. Surface the failure and let the operator retry the
        // refresh, the same as showStale keeps the last good collection
        // index visible on a failed background load.
        toniesError = failureMessage;
      } else {
        tonies = loaded;
        toniesError = "";
        rebindTargets(selections, tonies);
      }
      // The operation key is NOT touched here, whatever the refresh returned.
      // The key tracks the operator's intent, and a refresh reports what the
      // world did rather than what the operator decided. If the first send
      // landed and its response was lost, this refresh is exactly where the
      // appended chapters show up: clearing the key on that would make the
      // next Send a brand new operation and append the same audio twice, and a
      // Tonie write has no undo. Keeping the key hands the moved payload back
      // under the same key, so the server's idempotency digest answers 409 and
      // tells the operator the situation moved instead of uploading again.
      if (active && !signal?.aborted) render({ focusKey });
    }

    // Focus is NOT handled here. `render` already wraps the whole screen in
    // rememberFocus(root) / restoreFocus, and the bar lives inside `root`, so
    // a second focus dance nested inside that one would remember a focused
    // element the outer render is about to detach. Every control in the bar
    // therefore calls `render({ focusKey })`, never `renderSendBar()` directly.
    function renderReceipt() {
      const byTonie = activeSendsByTonie(jobs);
      const rows = receipt.rows.map((row) => {
        const key = row.tonie ? tonieJobKey(row.tonie.householdId, row.tonie.id) : "";
        const job = (byTonie.get(key) || [])[0];
        // No active job for this target means the send left the queue. The
        // bar says so plainly rather than guessing at a result it never saw:
        // Activity holds the outcome, including a failure.
        const view = job ? sendJobView(job) : {
          phase: "sent",
          label: "Finished",
          message: "This send has left the queue. Activity holds its result.",
          mode: "determinate",
          percent: 100,
        };
        const meterAttributes = view.mode === "determinate"
          ? {
            role: "progressbar",
            "aria-label": `Send to ${row.tonie?.name || "Creative Tonie"}`,
            "aria-valuemin": "0",
            "aria-valuemax": "100",
            "aria-valuenow": String(Math.round(view.percent)),
            style: `--work-progress:${view.percent}%`,
          }
          : { "aria-label": `${view.label}, progress amount is not available` };
        return element("li", { className: "library-receipt-row" }, [
          tonieJacket(row.tonie, "library-receipt-jacket"),
          element("div", { className: "library-receipt-copy" }, [
            element("div", { className: "library-receipt-head" }, [
              element("strong", { text: row.tonie ? tonieLabel(row.tonie) : "Creative Tonie" }),
              element("span", { className: "status-stamp", "data-status": view.phase, text: view.label }),
            ]),
            element("p", { className: "library-receipt-facts", text: `${row.chapters} ${row.chapters === 1 ? "chapter" : "chapters"}` }),
            element("p", { className: "library-receipt-message", text: view.message }),
            element("span", {
              className: "work-cart-progress-track",
              "data-mode": view.mode,
              ...meterAttributes,
            }, [element("span", { className: "work-cart-progress-fill" })]),
          ]),
        ]);
      });
      const allDone = receipt.rows.every((row) => {
        const key = row.tonie ? tonieJobKey(row.tonie.householdId, row.tonie.id) : "";
        return !(byTonie.get(key) || []).length;
      });
      const done = element("button", {
        type: "button",
        className: "button button-secondary",
        text: allDone ? "Done" : "Hide this",
        "data-focus-key": "library-send-done",
      });
      done.addEventListener("click", () => {
        receipt = null;
        render({ focusKey: "library-search" });
      });
      sendBar.hidden = false;
      replace(sendBar,
        element("div", { className: "screen-heading" }, [
          element("h2", { id: "library-send-title", text: allDone ? "Sent" : "Sending now" }),
        ]),
        element("ol", { className: "library-receipt", role: "status", "aria-live": "polite" }, rows),
        element("div", { className: "library-send-actions" }, [done]),
      );
    }

    function renderSendBar() {
      if (!active || signal?.aborted) return;
      const picked = selection.ordered(collections);
      if (!picked.length && receipt) {
        renderReceipt();
        return;
      }
      sendBar.hidden = picked.length === 0;
      if (!picked.length) {
        replace(sendBar);
        selections = [];
        operationKey = "";
        signature = "";
        announcedProblem = "";
        return;
      }
      const groups = packSelection(picked, limitSeconds());
      const nextSignature = membershipSignature(groups);
      if (nextSignature !== signature) {
        // Membership, order, fingerprint or grouping changed, so this is a new
        // operation. Targets clear with it: a Tonie chosen for one set of
        // chapters was never chosen for a different set, and preselecting it
        // for the new set would be the automatic assignment the design forbids.
        signature = nextSignature;
        selections = groups.map(() => ({ tonie: null, replaceExisting: false }));
        operationKey = "";
      }
      const totalSeconds = groups.reduce((sum, group) => sum + group.seconds, 0);
      // Chapters, because a partial story is not a story: "1 story selected"
      // beside a single ticked chapter of 196 would be a lie about what Send
      // is about to write to a Creative Tonie.
      const chapters = groups.reduce((sum, group) => sum + group.entries.length, 0);
      const heading = element("div", { className: "screen-heading" }, [
        element("h2", {
          id: "library-send-title",
          text: `${chapters} ${chapters === 1 ? "chapter" : "chapters"} from ${picked.length} ${picked.length === 1 ? "story" : "stories"} · ${humanDuration(totalSeconds)}`,
        }),
        element("button", { type: "button", className: "button button-secondary library-send-refresh", "data-focus-key": "library-send-refresh" }, [
          iconNode("refresh"), element("span", { text: "Refresh targets" }),
        ]),
      ]);
      heading.querySelector("button").addEventListener("click", () => loadTargets());

      const groupNodes = groups.map((group, index) => {
        const chosen = selections[index];
        // A radio card group, not a select. A select cannot hold an image, and
        // the figure's picture is the only thing that tells two boxes apart
        // when the Tonie Cloud has named them both "Creative Tonie". No option
        // is preselected: sending without an explicit choice would be an
        // automatic assignment, and a Tonie write has no undo.
        const picker = element("div", {
          className: "library-send-targets",
          role: "radiogroup",
          "aria-label": `Creative Tonie for group ${group.index}`,
        });
        for (const tonie of tonies || []) {
          // Free space is a property of the Tonie, so the printed figure is the
          // same whichever effect is ticked. It is computed here rather than
          // read from the tonie's own time_free, which is a server snapshot
          // that can disagree with the limit this bar packs against.
          const free = tonieFreeSeconds(tonie, limitSeconds());
          const capacity = tonieCapacity(tonie, group.seconds, chosen.replaceExisting, limitSeconds());
          // A replace clears the box first, so a group larger than the free
          // space still fits. Saying so is what keeps "6m 40s free" from
          // reading as a contradiction beside an option the bar accepts, and
          // it matches what the validation line offers when an append
          // overflows: choose Replace everything, or another Tonie.
          let fitNote = "";
          if (!capacity.fits) fitNote = "does not fit";
          else if (group.seconds > free) fitNote = "fits once everything is replaced";
          const value = `${tonie.householdId}/${tonie.id}`;
          const input = element("input", {
            type: "radio",
            className: "library-send-target",
            name: `library-target-${group.index}`,
            value,
            checked: chosen.tonie
              ? `${chosen.tonie.householdId}/${chosen.tonie.id}` === value
              : false,
            "data-focus-key": `library-send-target-${group.index}-${value}`,
          });
          input.addEventListener("change", () => {
            chosen.tonie = (tonies || []).find((entry) => `${entry.householdId}/${entry.id}` === value) || null;
            operationKey = "";
            render({ focusKey: `library-send-target-${group.index}-${value}` });
          });
          picker.append(element("label", {
            className: "library-send-target-card",
            "data-fits": String(capacity.fits),
          }, [
            input,
            tonieJacket(tonie, "library-send-target-jacket"),
            element("span", { className: "library-send-target-copy" }, [
              element("strong", { text: tonieLabel(tonie) }),
              element("small", { text: `${humanDuration(free)} free${fitNote ? ` · ${fitNote}` : ""}` }),
            ]),
          ]));
        }

        const appendInput = element("input", { type: "radio", name: `library-effect-${group.index}`, value: "append", checked: !chosen.replaceExisting, "data-focus-key": `library-send-effect-${group.index}-append` });
        const replaceInput = element("input", { type: "radio", name: `library-effect-${group.index}`, value: "replace", checked: chosen.replaceExisting, "data-focus-key": `library-send-effect-${group.index}-replace` });
        appendInput.addEventListener("change", () => { chosen.replaceExisting = false; operationKey = ""; render({ focusKey: `library-send-effect-${group.index}-append` }); });
        replaceInput.addEventListener("change", () => { chosen.replaceExisting = true; operationKey = ""; render({ focusKey: `library-send-effect-${group.index}-replace` }); });

        // Collapsed by default. A 30 chapter audiobook printed in full pushed
        // the picker and the Send button off the screen, and every row of it
        // repeated the same collection title in its first column.
        const sourceTitles = [];
        for (const entry of group.entries) {
          if (!sourceTitles.includes(entry.collectionTitle)) sourceTitles.push(entry.collectionTitle);
        }
        const membership = element("details", { className: "library-send-membership-disclosure" }, [
          element("summary", {
            text: `${group.entries.length} ${group.entries.length === 1 ? "chapter" : "chapters"} · ${humanDuration(group.seconds)} · ${sourceTitles.join(", ")}`,
          }),
          element("ol", { className: "library-send-membership" }, group.entries.map((entry) => (
            element("li", {}, [
              // The collection column appears only when a group actually spans
              // more than one, which is the only time it says anything.
              sourceTitles.length > 1 ? element("span", { text: entry.collectionTitle }) : null,
              element("span", { text: entry.title }),
              element("span", { text: entry.duration || "" }),
            ].filter(Boolean))
          ))),
        ]);

        // Decisions first, evidence second. The membership list used to lead,
        // and with one audiobook it was the whole bar: the target picker, the
        // effect and Send were all below the fold.
        return element("li", { className: "library-send-group" }, [
          element("h3", { text: groups.length === 1 ? "Chapters to send" : `Group ${group.index}` }),
          picker,
          element("fieldset", { className: "library-send-effect" }, [
            element("legend", { text: `What group ${group.index} does to that Tonie` }),
            element("label", {}, [appendInput, element("span", { text: "Append to the back" })]),
            element("label", {}, [replaceInput, element("span", { text: "Replace everything" })]),
          ]),
          membership,
        ]);
      });

      const problems = tonies ? selectionProblems(groups, selections, limitSeconds(), picked) : ["Creative Tonies are not loaded yet."];
      // Every problem, not just the first. selectionProblems already returns
      // them all, and with two unassigned groups the second was invisible.
      const validation = element("ul", { className: "library-send-validation" },
        problems.map((problem) => element("li", { text: problem })));
      // A failed refresh with a Tonies list already on hand is not the same
      // situation as never having loaded one: the picker still shows real
      // choices and the chosen target is still valid, so the message says a
      // refresh failed rather than claiming there is nothing to send to,
      // matching how showStale reports a failed background load without
      // implying the last good state is gone.
      const toniesErrorMessage = toniesError
        ? (tonies
          ? `Creative Tonies could not refresh. ${toniesError} The last known list remains available.`
          : `Creative Tonies could not load. ${toniesError}`)
        : "";
      if (toniesErrorMessage) {
        replace(validation, element("li", { text: toniesErrorMessage }));
      }
      // The paragraph is rebuilt every render, so it can never be the live
      // region that fires: a node has to be in the document before its text
      // changes. Announcing the message itself is the only way a screen reader
      // hears it, and only on a change, or every keystroke would re-read it.
      const spoken = toniesErrorMessage || (problems[0] || "");
      if (spoken !== announcedProblem) {
        announcedProblem = spoken;
        if (spoken) announce(spoken);
      }

      const send = element("button", {
        type: "button",
        className: "button button-primary library-send-submit",
        disabled: sending || problems.length > 0,
        "data-focus-key": "library-send-submit",
      }, [
        iconNode("tonie"),
        element("span", {
          text: sending
            ? "Sending..."
            : `Send ${chapters} ${chapters === 1 ? "chapter" : "chapters"}`,
        }),
      ]);
      send.addEventListener("click", () => submitSend(groups));

      const cancel = element("button", {
        type: "button",
        className: "button button-secondary library-send-clear",
        text: "Clear selection",
        disabled: sending,
        "data-focus-key": "library-send-clear",
      });
      cancel.addEventListener("click", () => {
        selection.clear();
        render({ focusKey: "library-search" });
      });

      replace(sendBar,
        heading,
        element("ol", { className: "library-send-groups" }, groupNodes),
        validation,
        element("div", { className: "library-send-actions" }, [cancel, send]),
      );
    }

    async function submitSend(groups) {
      if (sending) return;
      if (!operationKey) operationKey = newOperationKey();
      const payload = buildPushBatchPayload(groups, selections, operationKey);
      const replacing = selections.filter((choice) => choice.replaceExisting);
      const attempt = createSendAttempt({
        payload,
        request,
        signal,
        setPending: (pending) => {
          sending = pending;
          // The submit button is gone once the bar has become a receipt, and
          // asking for a focus key that no longer exists drops focus back to
          // the search field, away from the thing the operator just started.
          render({ focusKey: receipt ? "library-send-done" : "library-send-submit" });
        },
        confirm: () => showConfirmDialog({
          title: replacing.length === 1
            ? "Replace chapters on a Creative Tonie?"
            : `Replace chapters on ${replacing.length} Creative Tonies?`,
          message: "Every chapter currently stored on these Creative Tonies will be lost, and this cannot be undone. Your local library is not touched.",
          // The names alone could not answer this question: the Tonie Cloud
          // ships every Creative Tonie called "Creative Tonie", so two boxes
          // in one household read identically. The figure is the difference.
          subject: replacing.map((choice) => ({
            imageUrl: choice.tonie.imageUrl,
            name: tonieLabel(choice.tonie),
            detail: `${choice.tonie.chapter_count || 0} chapters will be replaced`,
          })),
          confirmLabel: "Replace and send",
          destructive: true,
        }),
        onReceipt: async (created) => {
          // The bar becomes the receipt. Everything it needs is right here and
          // is about to be discarded by selection.clear().
          receipt = {
            jobIds: created?.job_ids || [],
            rows: groups.map((group, index) => ({
              index: group.index,
              tonie: selections[index].tonie,
              chapters: group.entries.length,
            })),
          };
          selection.clear();
          operationKey = "";
          const sent = groups.reduce((sum, group) => sum + group.entries.length, 0);
          notify(`${sent} ${sent === 1 ? "chapter is" : "chapters are"} queued to send.`, { kind: "success" });
          announce("Creative Tonie send queued.");
          await refresh.request();
          render({ focusKey: "library-send-done" });
        },
        onFailure: (error) => {
          notify(`${error.message} The selection is unchanged. Fix the problem and send again.`, { kind: "failure", timeout: 0 });
        },
      });
      // Only a replace is irreversible, so only a replace is worth a dialog.
      if (replacing.length) await attempt.submit();
      else await attempt.retry();
    }

    function onRefresh(snapshot) {
      if (!active || signal?.aborted) return;
      const failed = snapshot.stale?.includes("collections");
      if (!snapshot.stale?.includes("jobs")) jobs = snapshot.jobs || [];
      stale.hidden = !failed;
      if (failed) showStale("Library information could not refresh. Showing the last available local collection index.");
      else replace(stale);
      if (!failed) collections = snapshot.collections || [];
      render();
    }

    search.addEventListener("input", () => {
      query = search.value;
      render({ focusKey: "library-search" });
    });

    rescan.addEventListener("click", async () => {
      try {
        const result = await mutation.run(async () => {
          const outcome = await rescanCollections({
            collections: collections.slice(),
            request,
            refresh,
            signal,
          });
          return outcome;
        });
        if (!result || !active || signal?.aborted) return;
        if (result.failures.length) {
          notify(`${result.failures.length} ${result.failures.length === 1 ? "refresh step" : "refresh steps"} could not complete. The last available details remain visible. Retry when the library is available.`, {
            kind: "failure",
            timeout: 0,
          });
        } else {
          notify("Local collection folders rescanned.", { kind: "success" });
          announce("Library rescan complete.");
        }
        rescan.focus({ preventScroll: true });
      } catch (error) {
        if (active && !signal?.aborted) notify(error.message, { kind: "failure", timeout: 0 });
      }
    });

    root.append(header, searchField, stale, summary, list, sendBar);
    replace(workspace, root);
    render();
    const unsubscribe = refresh.subscribe(onRefresh);
    refresh.request();
    return () => {
      active = false;
      unsubscribe();
    };
  };
}
