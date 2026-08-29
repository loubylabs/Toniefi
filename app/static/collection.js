import { api } from "./api.js";
import { icon } from "./icons.js";
import { forgePreparationState } from "./library.js";
import {
  announce,
  createMutationController,
  element,
  humanDuration,
  moveItem,
  notify,
  rememberFocus,
  replace,
  restoreFocus,
  setBusy,
  showConfirmDialog,
} from "./shared.js";

export function moveControlFocusKey(trackName, targetIndex, total, offset) {
  const control = targetIndex === 0
    ? "down"
    : targetIndex === total - 1
      ? "up"
      : offset < 0 ? "up" : "down";
  return `chapter-${trackName}-${control}`;
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

function coverNode(collection, className) {
  if (collection.cover) {
    return element("img", {
      className,
      src: `/api/collections/${encodeURIComponent(collection.slug)}/cover`,
      alt: "",
      loading: "lazy",
    });
  }
  return element("span", {
    className: `${className} collection-cover-fallback`,
    text: initials(collection.title),
    "aria-hidden": "true",
  });
}

function forgeSummary(collection) {
  if (collection.stage !== "forged") return "Forge incomplete";
  const forge = collection.forge || {};
  const details = [];
  if (forge.normalized) details.push("−16 LUFS normalized");
  if (forge.titles_cleaned) details.push("titles cleaned");
  if (forge.split) details.push("oversized tracks split");
  return details.length ? details.join(", ") : "Forge complete";
}

function loadingState(title, message) {
  return element("section", { className: "route-pending", "aria-label": title }, [
    iconNode("library", "route-pending-mark"),
    element("h1", { text: title }),
    element("p", { text: message }),
  ]);
}

function detailFacts(collection) {
  const facts = [
    ["Source", collection.source || "Local audio"],
    ["Uploader", collection.uploader || "Not provided"],
    ["Duration", collection.total_duration || "No duration yet"],
    ["Chapters", String(collection.track_count || 0)],
    ["Forge", forgeSummary(collection)],
  ];
  return element("dl", { className: "collection-detail-facts" }, facts.map(([term, description]) => (
    element("div", {}, [element("dt", { text: term }), element("dd", { text: description })])
  )));
}

function capacityPlan(collection, usableLimit) {
  const section = element("section", { className: "capacity-plan", "aria-labelledby": "capacity-plan-title" });
  section.append(
    element("div", { className: "section-heading" }, [
      iconNode("tonie"),
      element("div", {}, [
        element("h2", { id: "capacity-plan-title", text: "Capacity plan" }),
        element("p", { text: `${humanDuration(usableLimit)} usable per Creative Tonie, including safety headroom.` }),
      ]),
    ]),
  );
  if (!collection.plan?.length) {
    section.append(element("div", { className: "empty-state" }, [
      element("strong", { text: "No audio to plan yet" }),
      element("p", { text: "Add chapters before choosing Creative Tonies." }),
    ]));
    return section;
  }
  const groups = element("ol", { className: "capacity-group-list" });
  for (const group of collection.plan) {
    const percent = Math.min(100, usableLimit ? (Number(group.seconds) / usableLimit) * 100 : 0);
    const trackList = element("ol", { className: "capacity-track-list" }, group.tracks.map((track) => (
      element("li", {}, [element("span", { text: track.title }), element("span", { text: track.duration })])
    )));
    groups.append(element("li", { className: "capacity-group" }, [
      element("div", { className: "capacity-group-heading" }, [
        element("h3", { text: `Creative Tonie ${group.index}` }),
        element("span", { className: "status-stamp", "data-status": "success", text: group.duration }),
      ]),
      element("div", {
        className: "capacity-meter",
        role: "meter",
        "aria-label": `Capacity group ${group.index}`,
        "aria-valuemin": "0",
        "aria-valuemax": String(usableLimit),
        "aria-valuenow": String(group.seconds),
        "aria-valuetext": `${group.duration} of ${humanDuration(usableLimit)}`,
      }, [element("span", { style: `--capacity:${percent}%` })]),
      trackList,
    ]));
  }
  section.append(groups);
  return section;
}

export function createCollectionDetail({ workspace, slug, request, refresh, player, signal }) {
  let active = true;
  let collection = null;
  let status = refresh.snapshot.status;
  let jobs = refresh.snapshot.jobs || [];
  const root = element("article", { className: "collection-detail-screen", "aria-labelledby": "collection-detail-title" });
  replace(workspace, loadingState("Opening collection", "Reading the collection manifest and capacity plan."));

  async function loadCollection() {
    const [nextCollection, nextStatus] = await Promise.all([
      request(`/api/collections/${encodeURIComponent(slug)}`, { signal }),
      status ? Promise.resolve(status) : request("/api/status", { signal }),
    ]);
    if (!active || signal.aborted) return null;
    collection = nextCollection;
    status = nextStatus;
    return collection;
  }

  function renderRouteFailure(error) {
    if (!active || signal.aborted) return;
    const retry = element("button", { type: "button", className: "button button-primary", text: "Retry" });
    retry.addEventListener("click", hydrate);
    replace(workspace, element("section", { className: "route-pending", role: "alert" }, [
      iconNode("alert", "route-pending-mark"),
      element("h1", { text: "This collection could not open" }),
      element("p", { text: error.message }),
      retry,
    ]));
  }

  function renderMutationStale(error) {
    if (!active || signal.aborted) return;
    const retry = element("button", { type: "button", className: "button button-secondary", text: "Retry current collection" });
    retry.addEventListener("click", async () => {
      try {
        await loadCollection();
        if (active && !signal.aborted) renderDetail({ focusKey: "collection-title" });
      } catch (reloadError) {
        notify(reloadError.message, { kind: "failure", timeout: 0 });
      }
    });
    root.prepend(element("div", { className: "stale-notice", "data-kind": "failure", role: "alert" }, [
      element("strong", { text: "Collection state may be stale" }),
      element("p", { text: `${error.message} The current collection remains visible.` }),
      retry,
    ]));
  }

  const mutation = createMutationController({
    root,
    reload: () => loadCollection(),
    onReloaded: () => renderDetail(),
    onStale: renderMutationStale,
    signal,
  });

  function renderDetail({ focusKey = "", fallback = null } = {}) {
    if (!active || signal.aborted || !collection) return;
    const preparation = forgePreparationState(collection, jobs);
    const token = focusKey ? { key: focusKey } : rememberFocus(root);
    const titleInput = element("input", {
      id: "collection-title-input",
      name: "collection-title",
      value: collection.title || "",
      "data-focus-key": "collection-title",
      maxlength: "240",
      required: true,
      "data-collection-mutation": "",
    });
    const renameButton = element("button", { type: "submit", className: "button button-secondary", "data-collection-mutation": "" }, [
      iconNode("check"), element("span", { text: "Save title" }),
    ]);
    const titleForm = element("form", { className: "collection-title-form" }, [
      element("label", { for: "collection-title-input", text: "Collection title" }),
      titleInput,
      renameButton,
    ]);
    titleForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const title = titleInput.value.trim();
      if (!title) {
        titleInput.setCustomValidity("Enter a collection title.");
        titleInput.reportValidity();
        return;
      }
      titleInput.setCustomValidity("");
      renameButton.disabled = true;
      try {
        const saved = await mutation.run(async () => {
          await request(`/api/collections/${encodeURIComponent(slug)}`, {
            method: "PATCH",
            body: JSON.stringify({ title }),
            signal,
          });
          await loadCollection();
          renderDetail({ focusKey: "collection-title" });
          return true;
        });
        if (!saved || !active || signal.aborted) return;
        notify("Collection title saved.", { kind: "success" });
        await refresh.request();
      } catch (error) {
        if (!active || signal.aborted) return;
        renameButton.disabled = false;
        notify(error.message, { kind: "failure", timeout: 0 });
        titleInput.focus({ preventScroll: true });
      }
    });

    const header = element("header", { className: "collection-detail-header" }, [
      element("a", { className: "back-link", href: "/library", "data-route": "library" }, [
        iconNode("chevronRight"), element("span", { text: "Back to Library" }),
      ]),
      element("div", { className: "collection-detail-heading" }, [
        coverNode(collection, "collection-detail-cover"),
        element("div", {}, [
          element("span", {
            className: "status-stamp",
            "data-status": preparation.state === "ready"
              ? "success"
              : preparation.state === "failed" ? "failure" : "warning",
            text: preparation.state === "ready"
              ? "Ready to send"
              : preparation.state === "pending" ? "Forge queued" : "Forge incomplete",
          }),
          element("h1", { id: "collection-detail-title", text: collection.title || "Untitled collection", tabindex: "-1" }),
          titleForm,
          detailFacts(collection),
        ]),
      ]),
    ]);

    const chaptersHeading = element("div", { className: "section-heading" }, [
      iconNode("library"),
      element("div", {}, [
        element("h2", { id: "chapter-list-title", text: "Chapter order" }),
        element("p", { text: "Drag with a pointer, or use the Move up and Move down buttons." }),
      ]),
    ]);
    const chapterList = element("ol", { className: "collection-chapter-list", "aria-labelledby": "chapter-list-title" });

    async function persistOrder(nextTracks, key) {
      setBusy(chapterList, true, "Saving chapter order");
      try {
        const saved = await mutation.run(async () => {
          await request(`/api/collections/${encodeURIComponent(slug)}/reorder`, {
            method: "POST",
            body: JSON.stringify({ names: nextTracks.map((track) => track.name) }),
            signal,
          });
          await loadCollection();
          renderDetail({ focusKey: key });
          return true;
        });
        if (!saved) return;
        notify("Chapter order saved.", { kind: "success" });
        announce("Chapter order saved.");
        await refresh.request();
      } catch (error) {
        if (!active || signal.aborted) return;
        setBusy(chapterList, false);
        notify(error.message, { kind: "failure", timeout: 0 });
      }
    }

    function chapterRow(track, index) {
      const row = element("li", {
        className: "collection-chapter-row",
        draggable: "true",
        "data-track-name": track.name,
      });
      const titleId = `chapter-title-${index}`;
      const titleInput = element("input", {
        id: titleId,
        value: track.title || "",
        "data-focus-key": `chapter-${track.name}-title`,
        maxlength: "240",
        "data-collection-mutation": "",
        "aria-label": `Chapter ${index + 1} title`,
      });
      titleInput.addEventListener("change", async () => {
        const title = titleInput.value.trim();
        if (!title) {
          titleInput.setCustomValidity("Enter a chapter title.");
          titleInput.reportValidity();
          return;
        }
        titleInput.setCustomValidity("");
        titleInput.disabled = true;
        try {
          const saved = await mutation.run(async () => {
            await request(`/api/collections/${encodeURIComponent(slug)}/tracks/${encodeURIComponent(track.name)}`, {
              method: "PATCH",
              body: JSON.stringify({ title }),
              signal,
            });
            await loadCollection();
            renderDetail({ focusKey: `chapter-${track.name}-title` });
            return true;
          });
          if (!saved || !active || signal.aborted) return;
          notify("Chapter title saved.", { kind: "success" });
        } catch (error) {
          if (!active || signal.aborted) return;
          titleInput.disabled = false;
          notify(error.message, { kind: "failure", timeout: 0 });
          titleInput.focus({ preventScroll: true });
        }
      });
      const play = element("button", {
        type: "button",
        className: "button button-secondary chapter-play",
        "data-focus-key": `chapter-${track.name}-play`,
        "aria-label": `Play ${track.title}`,
        "data-collection-mutation": "",
      }, [iconNode("play"), element("span", { text: "Play" })]);
      play.addEventListener("click", () => player.play({
        src: `/api/collections/${encodeURIComponent(slug)}/tracks/${encodeURIComponent(track.name)}/audio`,
        label: `${track.title} from ${collection.title}`,
      }));
      const moveUp = element("button", {
        type: "button",
        className: "icon-button",
        "data-focus-key": `chapter-${track.name}-up`,
        "aria-label": `Move ${track.title} up`,
        disabled: index === 0,
        "data-collection-mutation": "",
      });
      moveUp.innerHTML = icon("arrowUp");
      moveUp.addEventListener("click", () => persistOrder(
        moveItem(collection.tracks, index, -1),
        moveControlFocusKey(track.name, index - 1, collection.tracks.length, -1),
      ));
      const moveDown = element("button", {
        type: "button",
        className: "icon-button",
        "data-focus-key": `chapter-${track.name}-down`,
        "aria-label": `Move ${track.title} down`,
        disabled: index === collection.tracks.length - 1,
        "data-collection-mutation": "",
      });
      moveDown.innerHTML = icon("arrowDown");
      moveDown.addEventListener("click", () => persistOrder(
        moveItem(collection.tracks, index, 1),
        moveControlFocusKey(track.name, index + 1, collection.tracks.length, 1),
      ));
      const removeButton = element("button", {
        type: "button",
        className: "icon-button chapter-remove",
        "data-focus-key": `chapter-${track.name}-remove`,
        "aria-label": `Remove ${track.title}`,
        "data-collection-mutation": "",
      });
      removeButton.innerHTML = icon("trash");
      removeButton.addEventListener("click", async () => {
        const confirmed = await showConfirmDialog({
          title: `Remove ${track.title}?`,
          message: `${track.name} will be deleted from this collection's local folder. The audio file cannot be restored by TonieFi.`,
          confirmLabel: "Delete audio file",
          destructive: true,
        });
        removeButton.focus({ preventScroll: true });
        if (!confirmed) return;
        removeButton.disabled = true;
        const fallbackTrack = collection.tracks[index + 1] || collection.tracks[index - 1];
        try {
          const removed = await mutation.run(async () => {
            await request(`/api/collections/${encodeURIComponent(slug)}/tracks/${encodeURIComponent(track.name)}`, { method: "DELETE", signal });
            await loadCollection();
            renderDetail({
              focusKey: fallbackTrack ? `chapter-${fallbackTrack.name}-remove` : "collection-title",
            });
            return true;
          });
          if (!removed || !active || signal.aborted) return;
          notify(`${track.title} and its local audio file were removed.`, { kind: "success" });
          await refresh.request();
        } catch (error) {
          if (!active || signal.aborted) return;
          removeButton.disabled = false;
          notify(error.message, { kind: "failure", timeout: 0 });
          removeButton.focus({ preventScroll: true });
        }
      });
      const controls = element("div", { className: "chapter-row-controls" }, [play, moveUp, moveDown, removeButton]);
      row.append(
        iconNode("grip", "chapter-grip"),
        element("span", { className: "chapter-index", text: String(index + 1) }),
        element("div", { className: "chapter-title-field" }, [titleInput, element("span", { text: track.duration })]),
        controls,
      );
      row.addEventListener("dragstart", (event) => {
        if (mutation.pending || signal.aborted) {
          event.preventDefault();
          return;
        }
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", track.name);
        row.classList.add("dragging");
      });
      row.addEventListener("dragend", () => row.classList.remove("dragging"));
      row.addEventListener("dragover", (event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
      });
      row.addEventListener("drop", (event) => {
        event.preventDefault();
        if (mutation.pending || signal.aborted) return;
        const draggedName = event.dataTransfer.getData("text/plain");
        const from = collection.tracks.findIndex((item) => item.name === draggedName);
        if (from < 0 || from === index) return;
        persistOrder(moveItem(collection.tracks, from, index - from), `chapter-${draggedName}-title`);
      });
      return row;
    }

    if (!collection.tracks.length) {
      chapterList.append(element("li", { className: "empty-state" }, [
        element("strong", { text: "This collection has no chapters" }),
        element("p", { text: "Return to Library after adding audio files to its folder, then Rescan." }),
      ]));
    } else {
      replace(chapterList, ...collection.tracks.map(chapterRow));
    }
    const chapters = element("section", { className: "collection-chapters", "aria-labelledby": "chapter-list-title" }, [
      chaptersHeading,
      chapterList,
    ]);
    const preparationPanel = element("section", {
      className: "assignment-panel preparation-panel",
      "aria-labelledby": "preparation-title",
    }, [
      element("h2", { id: "preparation-title", text: "Finish preparation" }),
      element("p", {
        text: preparation.state === "pending"
          ? "Forge is queued. The capacity plan stays hidden until the prepared collection reaches the Library."
          : preparation.state === "failed"
            ? preparation.error
            : "This extracted collection has not completed Forge. Finish preparation before its capacity plan appears.",
      }),
    ]);
    if (preparation.state !== "pending") {
      const finish = element("button", {
        type: "button",
        className: "button button-primary finish-preparation-button",
        text: "Finish preparation",
        "data-focus-key": "finish-preparation",
      });
      finish.addEventListener("click", async () => {
        finish.disabled = true;
        try {
          const receipt = await request("/api/forge", {
            method: "POST",
            body: JSON.stringify({ slug }),
            signal,
          });
          if (!active || signal.aborted) return;
          jobs = [{
            id: receipt.job_id ?? receipt.id,
            kind: "forge",
            status: "queued",
            payload: { slug },
          }, ...jobs];
          renderDetail({ focusKey: "finish-preparation" });
          notify(`${collection.title || "Collection"} is queued to finish Forge.`, { kind: "success" });
          await refresh.request();
        } catch (error) {
          if (!active || signal.aborted) return;
          jobs = [{
            id: Number.MAX_SAFE_INTEGER,
            kind: "forge",
            status: "failed",
            error: error.message,
            payload: { slug },
          }, ...jobs];
          renderDetail({ focusKey: "finish-preparation" });
          notify(error.message, { kind: "failure", timeout: 0 });
        }
      });
      preparationPanel.append(finish);
    }
    replace(root, header, element("div", { className: "collection-detail-grid" }, [
      element("div", { className: "collection-detail-main" }, [chapters]),
      element("aside", { className: "collection-detail-plan" }, preparation.state === "ready"
        ? [capacityPlan(collection, status.usable_limit_seconds)]
        : [preparationPanel]),
    ]));
    const fallbackTarget = fallback || root.querySelector("h1");
    restoreFocus(token, { root, fallback: fallbackTarget });
  }

  async function hydrate() {
    replace(workspace, loadingState("Opening collection", "Reading the collection manifest and capacity plan."));
    try {
      await loadCollection();
      if (!active || signal.aborted) return;
      replace(workspace, root);
      renderDetail();
    } catch (error) {
      renderRouteFailure(error);
    }
  }

  function onRefresh(snapshot) {
    if (!active || signal.aborted) return;
    if (!snapshot.stale?.includes("jobs")) jobs = snapshot.jobs || [];
    const indexed = snapshot.collections?.find((item) => item.slug === slug);
    if (indexed?.stage === "forged" && collection?.stage !== "forged") {
      loadCollection().then(() => {
        if (active && !signal.aborted) renderDetail({ focusKey: "collection-detail-title" });
      }).catch((error) => {
        if (active && !signal.aborted) renderMutationStale(error);
      });
      return;
    }
    if (collection) renderDetail();
  }

  const unsubscribe = refresh.subscribe(onRefresh);
  hydrate();
  return () => {
    active = false;
    unsubscribe();
  };
}

export function createCollectionScreen({
  request = api,
  refresh,
  player,
} = {}) {
  if (!refresh) throw new Error("The collection screen requires the application refresh coordinator.");
  if (!player) throw new Error("The collection screen requires the persistent audio player.");

  return function renderCollectionRoute({ workspace, params, signal }) {
    if (!params.slug) {
      replace(workspace, element("section", { className: "route-pending", role: "alert" }, [
        iconNode("alert", "route-pending-mark"),
        element("h1", { text: "No collection was named" }),
        element("p", { text: "Open a collection from the Library." }),
        element("a", { className: "button button-primary", href: "/library", "data-route": "library" }, [
          iconNode("library"), element("span", { text: "Go to Library" }),
        ]),
      ]));
      return () => {};
    }
    return createCollectionDetail({
      workspace,
      slug: params.slug,
      request,
      refresh,
      player,
      signal,
    });
  };
}
