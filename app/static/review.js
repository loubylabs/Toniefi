import { api } from "./api.js";
import { icon } from "./icons.js";
import {
  announce,
  element,
  moveItem,
  notify,
  rememberFocus,
  replace,
  restoreFocus,
  setBusy,
  showConfirmDialog,
} from "./shared.js";

export function forgedCollectionsNewestFirst(collections) {
  return collections
    .filter((collection) => collection.stage === "forged")
    .slice()
    .sort((left, right) => Number(right.created_at || 0) - Number(left.created_at || 0));
}

export function tonieCapacity(tonie, groupSeconds, replaceExisting, limitSeconds) {
  const present = Number(tonie?.seconds_present ?? tonie?.secondsPresent ?? 0);
  const free = Number(tonie?.seconds_free ?? Math.max(0, limitSeconds - present));
  const availableSeconds = replaceExisting ? Number(limitSeconds) : free;
  const projectedSeconds = replaceExisting ? Number(groupSeconds) : present + Number(groupSeconds);
  return {
    availableSeconds,
    projectedSeconds,
    fits: Number(groupSeconds) <= availableSeconds,
  };
}

export function buildPushPayload(slug, groupIndex, tonie, replaceExisting) {
  return {
    slug,
    household_id: tonie.householdId,
    tonie_id: tonie.id,
    group_index: groupIndex,
    replace: replaceExisting,
  };
}

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

function formatSeconds(value) {
  const total = Math.max(0, Math.round(Number(value) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
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

function factList(collection) {
  const facts = element("ul", { className: "collection-facts", "aria-label": "Collection facts" });
  const values = [
    `${collection.track_count || 0} ${collection.track_count === 1 ? "chapter" : "chapters"}`,
    collection.total_duration || "No duration yet",
    `${collection.tonies_needed || 0} ${collection.tonies_needed === 1 ? "Tonie" : "Tonies"} needed`,
  ];
  replace(facts, ...values.map((value) => element("li", { text: value })));
  return facts;
}

function forgeSummary(collection) {
  const forge = collection.forge || {};
  const details = [];
  if (forge.normalized) details.push("−16 LUFS normalized");
  if (forge.titles_cleaned) details.push("titles cleaned");
  if (forge.split) details.push("oversized tracks split");
  return details.length ? details.join(", ") : "Forge complete";
}

function loadingState(title, message) {
  return element("section", { className: "route-pending", "aria-label": title }, [
    iconNode("review", "route-pending-mark"),
    element("h1", { text: title }),
    element("p", { text: message }),
  ]);
}

function reviewShelfRow(collection) {
  const titleId = `review-shelf-${collection.slug}`;
  const body = element("div", { className: "review-shelf-row-body" }, [
    element("div", { className: "review-shelf-row-heading" }, [
      element("h2", { id: titleId, text: collection.title || "Untitled collection" }),
      element("span", { className: "status-stamp", "data-status": "success", text: "Forge complete" }),
    ]),
    factList(collection),
    element("p", { className: "collection-forge-summary", text: forgeSummary(collection) }),
    element("a", {
      className: "button button-primary review-shelf-open",
      href: `/review/${encodeURIComponent(collection.slug)}`,
      "data-route": "review",
      "data-focus-key": `review-${collection.slug}`,
    }, [element("span", { text: "Open review" }), iconNode("chevronRight")]),
  ]);
  return element("li", { className: "review-shelf-row", "aria-labelledby": titleId }, [
    coverNode(collection, "review-shelf-cover"),
    body,
  ]);
}

function createReviewShelf({ workspace, refresh }) {
  const root = element("section", { className: "review-shelf-screen", "aria-labelledby": "review-shelf-title" });
  const heading = element("div", { className: "screen-heading" }, [
    element("div", {}, [
      element("h1", { id: "review-shelf-title", text: "Review Shelf" }),
      element("p", { text: "Prepared stories wait here until you choose their Creative Tonies." }),
    ]),
    element("button", { type: "button", className: "button button-secondary review-refresh" }, [
      iconNode("refresh"), element("span", { text: "Refresh shelf" }),
    ]),
  ]);
  const stale = element("div", { className: "stale-notice", role: "status", hidden: true });
  const list = element("ol", { className: "review-shelf-list" });
  const refreshButton = heading.querySelector("button");

  function render(snapshot) {
    const token = rememberFocus(root);
    const collections = forgedCollectionsNewestFirst(snapshot.collections || []);
    stale.hidden = !snapshot.stale?.includes("collections");
    stale.textContent = stale.hidden
      ? ""
      : "The shelf may be out of date. The last available collections remain visible.";
    if (!collections.length) {
      replace(list, element("li", { className: "empty-state review-shelf-empty" }, [
        iconNode("review"),
        element("strong", { text: "No stories are waiting for review" }),
        element("p", { text: "Prepared collections appear here after Forge finishes." }),
        element("a", { className: "button button-primary", href: "/", "data-route": "desk" }, [
          iconNode("desk"), element("span", { text: "Prepare a story" }),
        ]),
      ]));
    } else {
      replace(list, ...collections.map(reviewShelfRow));
    }
    restoreFocus(token, { root, fallback: refreshButton });
  }

  refreshButton.addEventListener("click", async () => {
    refreshButton.disabled = true;
    try {
      await refresh.request();
      notify("Review Shelf refreshed.", { kind: "success" });
    } catch (error) {
      notify(error.message, { kind: "failure", timeout: 0 });
    } finally {
      refreshButton.disabled = false;
      refreshButton.focus({ preventScroll: true });
    }
  });

  root.append(heading, stale, list);
  replace(workspace, root);
  render(refresh.snapshot);
  const unsubscribe = refresh.subscribe(render);
  refresh.request();
  return () => unsubscribe();
}

function detailFacts(collection) {
  const facts = [
    ["Source", collection.source || "Local audio"],
    ["Uploader", collection.uploader || "Not provided"],
    ["Duration", collection.total_duration || "No duration yet"],
    ["Chapters", String(collection.track_count || 0)],
    ["Forge", forgeSummary(collection)],
  ];
  return element("dl", { className: "review-detail-facts" }, facts.map(([term, description]) => (
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
        element("p", { text: `${formatSeconds(usableLimit)} usable per Creative Tonie, including safety headroom.` }),
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
        "aria-valuetext": `${group.duration} of ${formatSeconds(usableLimit)}`,
      }, [element("span", { style: `--capacity:${percent}%` })]),
      trackList,
    ]));
  }
  section.append(groups);
  return section;
}

function assignmentLabel(tonie) {
  const household = tonie.householdName ? `, ${tonie.householdName}` : "";
  return `${tonie.name || "Creative Tonie"}${household}`;
}

function createAssignmentPanel({ collection, tonies, limitSeconds, onSubmit }) {
  const form = element("form", { className: "assignment-form" });
  const rows = [];
  const intro = element("p", {
    className: "assignment-intro",
    text: "Choose one target and effect for each capacity group. TonieFi will ask once more before any send is queued.",
  });
  const validation = element("p", { className: "assignment-validation", role: "status", "aria-live": "polite" });
  const submit = element("button", { type: "submit", className: "button button-primary" }, [
    iconNode("shield"), element("span", { text: "Review final confirmation" }),
  ]);

  function selections() {
    return rows.map(({ group, target, replaceControls }) => {
      const tonie = tonies.find((item) => `${item.householdId}:${item.id}` === target.value);
      const replaceExisting = replaceControls.find((control) => control.checked)?.value !== "append";
      return { group, tonie, replaceExisting };
    });
  }

  function validate() {
    const selected = selections();
    const missing = selected.some((selection) => !selection.tonie);
    const repeated = new Set(selected.map((selection) => selection.tonie && `${selection.tonie.householdId}:${selection.tonie.id}`)).size !== selected.length;
    const over = selected.some((selection) => selection.tonie && !tonieCapacity(
      selection.tonie,
      selection.group.seconds,
      selection.replaceExisting,
      limitSeconds,
    ).fits);
    if (missing) validation.textContent = "Choose a Creative Tonie for every group.";
    else if (repeated) validation.textContent = "Choose a different Creative Tonie for each capacity group.";
    else if (over) validation.textContent = "One append selection exceeds the target's available space. Choose replace or another Tonie.";
    else validation.textContent = "Every capacity group has a valid target.";
    validation.dataset.kind = missing || repeated || over ? "failure" : "success";
    submit.disabled = missing || repeated || over;
    return selected;
  }

  for (const [position, group] of collection.plan.entries()) {
    const groupId = `assignment-group-${group.index}`;
    const targetId = `${groupId}-target`;
    const effectId = `${groupId}-effect`;
    const target = element("select", { id: targetId, name: `target-${group.index}` });
    target.append(element("option", { value: "", text: "Choose a Creative Tonie" }));
    for (const [tonieIndex, tonie] of tonies.entries()) {
      target.append(element("option", {
        value: `${tonie.householdId}:${tonie.id}`,
        text: `${assignmentLabel(tonie)}. ${tonie.time_free || formatSeconds(tonie.seconds_free)} free`,
        selected: tonieIndex === position,
      }));
    }
    const effect = element("p", { id: effectId, className: "assignment-effect" });
    const replaceInput = element("input", { type: "radio", name: `effect-${group.index}`, value: "replace", checked: true });
    const appendInput = element("input", { type: "radio", name: `effect-${group.index}`, value: "append" });
    replaceInput.checked = true;
    appendInput.checked = false;
    const replaceControls = [replaceInput, appendInput];
    const updateEffect = () => {
      const tonie = tonies.find((item) => `${item.householdId}:${item.id}` === target.value);
      const replaceExisting = replaceInput.checked;
      if (!tonie) {
        effect.textContent = "Choose a target to see its available space and resulting effect.";
      } else {
        const capacity = tonieCapacity(tonie, group.seconds, replaceExisting, limitSeconds);
        const current = `${tonie.chapter_count || 0} current ${tonie.chapter_count === 1 ? "chapter" : "chapters"}`;
        const action = replaceExisting ? `Replaces ${current}` : `Appends after ${current}`;
        effect.textContent = `${action}. ${formatSeconds(capacity.availableSeconds)} available; this group uses ${group.duration}. ${capacity.fits ? "Fits." : "Does not fit."}`;
        effect.dataset.status = capacity.fits ? "success" : "failure";
      }
      validate();
    };
    target.addEventListener("change", updateEffect);
    replaceControls.forEach((control) => control.addEventListener("change", updateEffect));
    const fieldset = element("fieldset", { className: "assignment-group", "aria-describedby": effectId }, [
      element("legend", {}, [element("strong", { text: `Group ${group.index}` }), element("span", { text: group.duration })]),
      element("label", { for: targetId, text: "Creative Tonie" }),
      target,
      element("div", { className: "assignment-effect-options", role: "group", "aria-label": `Effect for group ${group.index}` }, [
        element("label", {}, [replaceInput, element("span", { text: "Replace current chapters" })]),
        element("label", {}, [appendInput, element("span", { text: "Append after current chapters" })]),
      ]),
      effect,
    ]);
    rows.push({ group, target, replaceControls });
    form.append(fieldset);
    updateEffect();
  }

  form.append(validation, submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const selected = validate();
    if (submit.disabled) return;
    await onSubmit(selected, { form, submit });
  });
  return element("div", {}, [intro, form]);
}

function createFocusedReview({ workspace, slug, request, refresh, player, signal }) {
  let collection = null;
  let status = refresh.snapshot.status;
  let toniesStale = false;
  const root = element("article", { className: "review-detail-screen", "aria-labelledby": "review-detail-title" });
  replace(workspace, loadingState("Opening collection review", "Reading the collection manifest and capacity plan."));

  async function loadCollection({ rescan = false } = {}) {
    const query = rescan ? "?refresh=true" : "";
    const [nextCollection, nextStatus] = await Promise.all([
      request(`/api/collections/${encodeURIComponent(slug)}${query}`, { signal }),
      status ? Promise.resolve(status) : request("/api/status", { signal }),
    ]);
    collection = nextCollection;
    status = nextStatus;
    return collection;
  }

  function renderDetail({ focusKey = "", fallback = null } = {}) {
    const token = focusKey ? { key: focusKey } : rememberFocus(root);
    const titleInput = element("input", {
      id: "review-collection-title",
      name: "collection-title",
      value: collection.title || "",
      "data-focus-key": "collection-title",
      maxlength: "240",
      required: true,
    });
    const renameButton = element("button", { type: "submit", className: "button button-secondary" }, [
      iconNode("check"), element("span", { text: "Save title" }),
    ]);
    const titleForm = element("form", { className: "review-title-form" }, [
      element("label", { for: "review-collection-title", text: "Collection title" }),
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
        await request(`/api/collections/${encodeURIComponent(slug)}`, {
          method: "PATCH",
          body: JSON.stringify({ title }),
        });
        await loadCollection();
        renderDetail({ focusKey: "collection-title" });
        notify("Collection title saved.", { kind: "success" });
        await refresh.request();
      } catch (error) {
        renameButton.disabled = false;
        notify(error.message, { kind: "failure", timeout: 0 });
        titleInput.focus({ preventScroll: true });
      }
    });

    const header = element("header", { className: "review-detail-header" }, [
      element("a", { className: "back-link", href: "/review", "data-route": "review" }, [
        iconNode("chevronRight"), element("span", { text: "Back to Review Shelf" }),
      ]),
      element("div", { className: "review-detail-heading" }, [
        coverNode(collection, "review-detail-cover"),
        element("div", {}, [
          element("span", { className: "status-stamp", "data-status": "success", text: "Ready to review" }),
          element("h1", { id: "review-detail-title", text: collection.title || "Untitled collection", tabindex: "-1" }),
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
    const chapterList = element("ol", { className: "review-chapter-list", "aria-labelledby": "chapter-list-title" });

    async function persistOrder(nextTracks, key) {
      setBusy(chapterList, true, "Saving chapter order");
      try {
        await request(`/api/collections/${encodeURIComponent(slug)}/reorder`, {
          method: "POST",
          body: JSON.stringify({ names: nextTracks.map((track) => track.name) }),
        });
        await loadCollection();
        renderDetail({ focusKey: key });
        notify("Chapter order saved.", { kind: "success" });
        announce("Chapter order saved.");
        await refresh.request();
      } catch (error) {
        setBusy(chapterList, false);
        notify(error.message, { kind: "failure", timeout: 0 });
      }
    }

    function chapterRow(track, index) {
      const row = element("li", {
        className: "review-chapter-row",
        draggable: "true",
        "data-track-name": track.name,
      });
      const titleId = `chapter-title-${index}`;
      const titleInput = element("input", {
        id: titleId,
        value: track.title || "",
        "data-focus-key": `chapter-${track.name}-title`,
        maxlength: "240",
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
          await request(`/api/collections/${encodeURIComponent(slug)}/tracks/${encodeURIComponent(track.name)}`, {
            method: "PATCH",
            body: JSON.stringify({ title }),
          });
          await loadCollection();
          renderDetail({ focusKey: `chapter-${track.name}-title` });
          notify("Chapter title saved.", { kind: "success" });
        } catch (error) {
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
          await request(`/api/collections/${encodeURIComponent(slug)}/tracks/${encodeURIComponent(track.name)}`, { method: "DELETE" });
          await loadCollection();
          renderDetail({
            focusKey: fallbackTrack ? `chapter-${fallbackTrack.name}-remove` : "collection-title",
          });
          notify(`${track.title} and its local audio file were removed.`, { kind: "success" });
          await refresh.request();
        } catch (error) {
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
    const chapters = element("section", { className: "review-chapters", "aria-labelledby": "chapter-list-title" }, [
      chaptersHeading,
      chapterList,
    ]);
    const assignmentHost = element("section", {
      className: "assignment-panel",
      "aria-labelledby": "assignment-title",
      "aria-live": "polite",
    });
    const chooseButton = element("button", {
      type: "button",
      className: "button button-primary choose-tonies-button",
      disabled: !collection.plan?.length,
      "data-focus-key": "choose-tonies",
    }, [iconNode("tonie"), element("span", { text: "Choose Creative Tonies" })]);

    async function showTargets() {
      chooseButton.disabled = true;
      setBusy(assignmentHost, true, "Refreshing Creative Tonies");
      replace(assignmentHost, element("h2", { id: "assignment-title", text: "Creative Tonie assignment" }), element("p", { text: "Refreshing targets and available space from myTonies." }));
      try {
        const tonies = await request("/api/tonies", { signal });
        toniesStale = false;
        if (!tonies.length) {
          replace(assignmentHost,
            element("h2", { id: "assignment-title", text: "Creative Tonie assignment" }),
            element("div", { className: "empty-state" }, [
              element("strong", { text: "No Creative Tonies found" }),
              element("p", { text: "Check the connected account in Settings, then refresh these targets." }),
              element("button", { type: "button", className: "button button-secondary", text: "Refresh targets", onclick: showTargets }),
            ]),
          );
          return;
        }
        const heading = element("div", { className: "section-heading" }, [
          iconNode("tonie"),
          element("div", {}, [
            element("h2", { id: "assignment-title", text: "Creative Tonie assignment" }),
            element("p", { text: "These figures were refreshed just before target selection." }),
          ]),
        ]);
        const panel = createAssignmentPanel({
          collection,
          tonies,
          limitSeconds: status.tonie_limit_seconds,
          onSubmit: async (selected, { form, submit }) => {
            const summary = selected.map(({ group, tonie, replaceExisting }) => (
              `Group ${group.index} (${group.duration}) will ${replaceExisting ? "replace" : "append to"} ${tonie.name || "Creative Tonie"}`
            )).join(". ");
            const confirmed = await showConfirmDialog({
              title: "Send these groups?",
              message: `${summary}. Replacing clears the target's current chapters. Tonie Cloud changes have no undo.`,
              confirmLabel: `Confirm ${selected.length} ${selected.length === 1 ? "send" : "sends"}`,
              destructive: true,
            });
            submit.focus({ preventScroll: true });
            if (!confirmed) return;
            form.querySelectorAll("input, select, button").forEach((control) => { control.disabled = true; });
            let queued = 0;
            try {
              for (const selection of selected) {
                await request("/api/push", {
                  method: "POST",
                  body: JSON.stringify(buildPushPayload(slug, selection.group.index, selection.tonie, selection.replaceExisting)),
                });
                queued += 1;
              }
              toniesStale = true;
              replace(assignmentHost,
                heading,
                element("div", { className: "stale-notice", role: "status" }, [
                  element("strong", { text: `${queued} ${queued === 1 ? "send is" : "sends are"} queued.` }),
                  element("p", { text: "Remote capacity figures are now stale while those jobs run. Refresh targets before another assignment." }),
                  element("button", { type: "button", className: "button button-secondary", text: "Refresh targets", onclick: showTargets }),
                ]),
              );
              notify("Creative Tonie sends were queued after confirmation.", { kind: "success" });
              await refresh.request();
            } catch (error) {
              toniesStale = true;
              replace(assignmentHost,
                heading,
                element("div", { className: "stale-notice", "data-kind": "failure", role: "alert" }, [
                  element("strong", { text: "The send could not be completed." }),
                  element("p", { text: `${error.message} Remote figures are stale. ${queued ? "Some sends may already be queued." : "No additional send was queued after the failure."}` }),
                  element("button", { type: "button", className: "button button-secondary", text: "Refresh targets", onclick: showTargets }),
                ]),
              );
              notify(error.message, { kind: "failure", timeout: 0 });
            }
          },
        });
        setBusy(assignmentHost, false);
        replace(assignmentHost, heading, panel);
        assignmentHost.querySelector("select")?.focus({ preventScroll: true });
      } catch (error) {
        toniesStale = true;
        setBusy(assignmentHost, false);
        replace(assignmentHost,
          element("h2", { id: "assignment-title", text: "Creative Tonie assignment" }),
          element("div", { className: "stale-notice", "data-kind": "failure", role: "alert" }, [
            element("strong", { text: "Creative Tonie figures are stale" }),
            element("p", { text: `${error.message} The collection review remains unchanged.` }),
            element("button", { type: "button", className: "button button-secondary", text: "Try target refresh again", onclick: showTargets }),
          ]),
        );
        notify(error.message, { kind: "failure", timeout: 0 });
      } finally {
        chooseButton.disabled = false;
      }
    }

    chooseButton.addEventListener("click", showTargets);
    assignmentHost.append(
      element("h2", { id: "assignment-title", text: "Creative Tonie assignment" }),
      element("p", { text: "No target information is loaded until you choose Creative Tonies." }),
      chooseButton,
    );
    replace(root, header, element("div", { className: "review-detail-grid" }, [
      element("div", { className: "review-detail-main" }, [chapters]),
      element("aside", { className: "review-detail-plan" }, [
        capacityPlan(collection, status.usable_limit_seconds),
        assignmentHost,
      ]),
    ]));
    const fallbackTarget = fallback || root.querySelector("h1");
    restoreFocus(token, { root, fallback: fallbackTarget });
  }

  return loadCollection().then(() => {
    if (signal.aborted) return null;
    replace(workspace, root);
    renderDetail();
    return () => {
      if (toniesStale) announce("Creative Tonie capacity figures remain stale.");
    };
  });
}

export function createReviewScreen({
  request = api,
  refresh,
  player,
} = {}) {
  if (!refresh) throw new Error("Review Shelf requires the application refresh coordinator.");
  if (!player) throw new Error("Review Shelf requires the persistent audio player.");

  return function renderReviewRoute({ workspace, params, signal }) {
    if (params.slug) {
      return createFocusedReview({
        workspace,
        slug: params.slug,
        request,
        refresh,
        player,
        signal,
      });
    }
    return createReviewShelf({ workspace, refresh });
  };
}
