import { api } from "./api.js";
import { icon } from "./icons.js";
import {
  buildPushBatchPayload,
  createSendAttempt,
  membershipSignature,
  newOperationKey,
  packSelection,
  rebindTargets,
  selectionProblems,
  sendCapacityLimit,
  tonieCapacity,
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
  // A set of slugs, never an array of collections. The send order is the
  // Library's own order, resolved at read time, so nothing depends on the
  // order the operator happened to tick.
  const chosen = new Set();
  return {
    has: (slug) => chosen.has(slug),
    size: () => chosen.size,
    toggle(slug) {
      if (chosen.has(slug)) chosen.delete(slug);
      else chosen.add(slug);
    },
    clear() { chosen.clear(); },
    ordered: (collections) => collections.filter((collection) => chosen.has(collection.slug)),
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
      const tick = selectable
        ? element("input", {
          id: tickId,
          type: "checkbox",
          className: "library-select",
          checked: selection.has(collection.slug),
          // The whole selection freezes for the duration of a send, the same
          // way Send and Clear already do. The payload was built from the ticks
          // as they stood, and the receipt clears every tick, so a story ticked
          // mid-flight would be neither sent nor kept.
          disabled: sending,
          "data-focus-key": `library-${collection.slug}-select`,
        })
        : null;
      if (tick) {
        tick.addEventListener("change", () => {
          // disabled is the visible guard, and a stale render is one bug away
          // from leaving it wrong, so the freeze gets its own guard too.
          if (sending) return;
          selection.toggle(collection.slug);
          if (selection.size() === 1 && tonies === null) loadTargets(`library-${collection.slug}-select`);
          render({ focusKey: `library-${collection.slug}-select` });
        });
      }
      const tickCell = element("div", { className: "library-select-cell" }, tick
        ? [tick, element("label", { for: tickId, className: "visually-hidden", text: `Select ${collection.title || collection.slug} to send` })]
        : []);
      const facts = element("ul", { className: "collection-facts", "aria-label": "Collection facts" }, [
        element("li", { text: `${collection.track_count || 0} ${collection.track_count === 1 ? "chapter" : "chapters"}` }),
        element("li", { text: collection.total_duration || "No duration yet" }),
        element("li", { text: `${collection.tonies_needed || 0} ${collection.tonies_needed === 1 ? "Tonie" : "Tonies"} needed` }),
      ]);
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
        element("p", { className: "library-source", text: source }),
        preparation.state === "failed"
          ? element("p", { className: "inline-error", role: "alert", text: preparation.error })
          : null,
        element("div", { className: "library-row-actions" }, [primary, removeButton]),
      ]);
      return element("li", { className: "library-row", "aria-labelledby": titleId }, [tickCell, collectionCover(collection), body]);
    }

    function render({ focusKey = "" } = {}) {
      if (!active || signal?.aborted) return;
      const token = focusKey ? { key: focusKey } : rememberFocus(root);
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
      }
      renderSendBar();
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
      let failure = "";
      try {
        loaded = await request("/api/tonies", { signal });
      } catch (error) {
        failure = error.message;
      }
      if (token !== targetsToken) return;
      tonies = loaded;
      toniesError = failure;
      rebindTargets(selections, tonies);
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
    function renderSendBar() {
      if (!active || signal?.aborted) return;
      const picked = selection.ordered(collections);
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
      const heading = element("div", { className: "screen-heading" }, [
        element("h2", {
          id: "library-send-title",
          text: `${picked.length} ${picked.length === 1 ? "story" : "stories"} selected · ${humanDuration(totalSeconds)}`,
        }),
        element("button", { type: "button", className: "button button-secondary library-send-refresh", "data-focus-key": "library-send-refresh" }, [
          iconNode("refresh"), element("span", { text: "Refresh targets" }),
        ]),
      ]);
      heading.querySelector("button").addEventListener("click", () => loadTargets());

      const groupNodes = groups.map((group, index) => {
        const chosen = selections[index];
        const picker = element("select", {
          className: "library-send-target",
          "aria-label": `Creative Tonie for group ${group.index}`,
          "data-focus-key": `library-send-target-${group.index}`,
        });
        // No preselected target. Sending without an explicit choice would be an
        // automatic assignment, and a Tonie write has no undo.
        picker.append(element("option", { value: "", text: "Choose a Creative Tonie" }));
        for (const tonie of tonies || []) {
          const capacity = tonieCapacity(tonie, group.seconds, chosen.replaceExisting, limitSeconds());
          picker.append(element("option", {
            value: `${tonie.householdId}/${tonie.id}`,
            // The number printed here is the one the fit check ran against, so
            // an option can never read "1h 30m free · does not fit". That has
            // to come from tonieCapacity, not the tonie's own time_free:
            // replaceExisting frees the whole usable limit, not the usable
            // limit minus what is already present, and time_free does not
            // know which effect is chosen.
            text: `${tonieLabel(tonie)} · ${humanDuration(capacity.availableSeconds)} free${capacity.fits ? "" : " · does not fit"}`,
            selected: chosen.tonie ? `${chosen.tonie.householdId}/${chosen.tonie.id}` === `${tonie.householdId}/${tonie.id}` : false,
          }));
        }
        picker.addEventListener("change", () => {
          chosen.tonie = (tonies || []).find((tonie) => `${tonie.householdId}/${tonie.id}` === picker.value) || null;
          operationKey = "";
          render({ focusKey: `library-send-target-${group.index}` });
        });

        const appendInput = element("input", { type: "radio", name: `library-effect-${group.index}`, value: "append", checked: !chosen.replaceExisting, "data-focus-key": `library-send-effect-${group.index}-append` });
        const replaceInput = element("input", { type: "radio", name: `library-effect-${group.index}`, value: "replace", checked: chosen.replaceExisting, "data-focus-key": `library-send-effect-${group.index}-replace` });
        appendInput.addEventListener("change", () => { chosen.replaceExisting = false; operationKey = ""; render({ focusKey: `library-send-effect-${group.index}-append` }); });
        replaceInput.addEventListener("change", () => { chosen.replaceExisting = true; operationKey = ""; render({ focusKey: `library-send-effect-${group.index}-replace` }); });

        const membership = element("ol", { className: "library-send-membership" }, group.entries.map((entry) => (
          element("li", {}, [
            element("span", { text: entry.collectionTitle }),
            element("span", { text: entry.title }),
            element("span", { text: entry.duration || "" }),
          ])
        )));

        return element("li", { className: "library-send-group" }, [
          element("h3", { text: groups.length === 1 ? "Chapters to send" : `Group ${group.index}` }),
          membership,
          element("div", { className: "library-send-target-field" }, [picker]),
          element("fieldset", { className: "library-send-effect" }, [
            element("legend", { text: `What group ${group.index} does to that Tonie` }),
            element("label", {}, [appendInput, element("span", { text: "Append to the back" })]),
            element("label", {}, [replaceInput, element("span", { text: "Replace everything" })]),
          ]),
        ]);
      });

      const problems = tonies ? selectionProblems(groups, selections, limitSeconds(), picked) : ["Creative Tonies are not loaded yet."];
      const validation = element("p", { className: "library-send-validation" },
        problems.length ? [element("span", { text: problems[0] })] : []);
      if (toniesError) {
        replace(validation, element("span", { text: `Creative Tonies could not load. ${toniesError}` }));
      }
      // The paragraph is rebuilt every render, so it can never be the live
      // region that fires: a node has to be in the document before its text
      // changes. Announcing the message itself is the only way a screen reader
      // hears it, and only on a change, or every keystroke would re-read it.
      const spoken = toniesError ? `Creative Tonies could not load. ${toniesError}` : (problems[0] || "");
      if (spoken !== announcedProblem) {
        announcedProblem = spoken;
        if (spoken) announce(spoken);
      }

      const send = element("button", {
        type: "button",
        className: "button button-primary library-send-submit",
        disabled: sending || problems.length > 0,
        "data-focus-key": "library-send-submit",
      }, [iconNode("tonie"), element("span", { text: `Send ${picked.length} ${picked.length === 1 ? "story" : "stories"}` })]);
      send.addEventListener("click", () => submitSend(groups, picked));

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

    async function submitSend(groups, picked) {
      if (sending) return;
      if (!operationKey) operationKey = newOperationKey();
      const payload = buildPushBatchPayload(groups, selections, operationKey);
      const replacing = selections.filter((choice) => choice.replaceExisting);
      const attempt = createSendAttempt({
        payload,
        request,
        signal,
        setPending: (pending) => { sending = pending; render({ focusKey: "library-send-submit" }); },
        confirm: () => showConfirmDialog({
          title: "Replace chapters on a Creative Tonie?",
          message: `${replacing.map((choice) => tonieLabel(choice.tonie)).join(", ")} will lose every chapter currently stored, and this cannot be undone. Your local library is not touched.`,
          confirmLabel: "Replace and send",
          destructive: true,
        }),
        onReceipt: async () => {
          selection.clear();
          operationKey = "";
          notify(`${picked.length} ${picked.length === 1 ? "story is" : "stories are"} queued to send.`, { kind: "success" });
          announce("Creative Tonie send queued.");
          await refresh.request();
          render({ focusKey: "library-search" });
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
