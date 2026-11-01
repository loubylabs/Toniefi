import { api } from "./api.js";
import { icon } from "./icons.js";
import { forgePreparationState, sourceLabel } from "./library.js";
import {
  announce,
  chapterMarks,
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
  if (forge.trim_head) details.push(`${forge.trim_head} sec cut from start`);
  if (forge.trim_tail) details.push(`${forge.trim_tail} sec cut from end`);
  if (forge.ads_cut_seconds) details.push(`${forge.ads_cut_seconds} sec of ads cut`);
  return details.length ? details.join(", ") : "Forge complete";
}

const REWRITE_KINDS = new Set(["trim", "remove_ads"]);

// Trim and ad removal both swap the whole collection folder when they finish.
export function activeRewrite(jobs, slug, kinds = REWRITE_KINDS) {
  return (jobs || []).find((job) => (
    kinds.has(job.kind)
    && job.payload?.slug === slug
    && (job.status === "queued" || job.status === "running")
  )) || null;
}

function trimSecondsField(id, label, seconds, onInput) {
  const input = element("input", {
    id,
    type: "number",
    min: "0",
    step: "0.1",
    value: seconds,
    inputmode: "decimal",
    "data-focus-key": id,
    "data-collection-mutation": "",
  });
  input.addEventListener("input", () => onInput(input.value));
  return element("label", { className: "forge-number-control", for: id }, [
    element("span", { text: label }),
    input,
  ]);
}

function loadingState(title, message) {
  return element("section", { className: "route-pending", "aria-label": title }, [
    iconNode("library", "route-pending-mark"),
    element("h1", { text: title }),
    element("p", { text: message }),
  ]);
}

function sourceFact(collection) {
  const label = sourceLabel(collection);
  if (!/^https?:\/\//i.test(collection.url || "")) return element("dd", { text: label });
  const copy = element("button", {
    type: "button",
    className: "button button-secondary collection-source-copy",
    text: "Copy source URL",
  });
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(collection.url);
      notify("Source URL copied.", { kind: "success" });
    } catch {
      notify("Could not copy the source URL.", { kind: "failure" });
    }
  });
  return element("dd", { className: "collection-source" }, [
    element("a", {
      className: "collection-source-link",
      href: collection.url,
      target: "_blank",
      rel: "noopener noreferrer",
      title: collection.url,
      text: label,
    }),
    copy,
  ]);
}

// A phone keeps one line of facts under the title; Source, Uploader and Forge
// (with Copy source URL) wait in a details whose open state the caller holds.
function phoneFacts(collection, open, onToggle) {
  const count = collection.track_count || 0;
  const line = element("p", {
    className: "collection-detail-line",
    text: `${collection.total_duration || "No duration yet"} · ${count} ${count === 1 ? "chapter" : "chapters"}`,
  });
  const details = element("details", { className: "collection-detail-more", open }, [
    element("summary", { text: "Source and Forge" }),
    element("dl", { className: "collection-detail-facts" }, [
      element("div", {}, [element("dt", { text: "Source" }), sourceFact(collection)]),
      element("div", {}, [element("dt", { text: "Uploader" }), element("dd", { text: collection.uploader || "Not provided" })]),
      element("div", {}, [element("dt", { text: "Forge" }), element("dd", { text: forgeSummary(collection) })]),
    ]),
  ]);
  details.addEventListener("toggle", () => onToggle(details.open));
  return [line, details];
}

function detailFacts(collection) {
  const facts = [
    ["Uploader", collection.uploader || "Not provided"],
    ["Duration", collection.total_duration || "No duration yet"],
    ["Chapters", String(collection.track_count || 0)],
    ["Forge", forgeSummary(collection)],
  ];
  return element("dl", { className: "collection-detail-facts" }, [
    element("div", {}, [element("dt", { text: "Source" }), sourceFact(collection)]),
    ...facts.map(([term, description]) => (
      element("div", {}, [element("dt", { text: term }), element("dd", { text: description })])
    )),
  ]);
}

// On a phone a Tonie's group is a closed details, so 223 chapters are not 223
// repeated lines; on a desktop the group stays open as a plain record. `view`
// carries the phone flag and the JS-held set of open groups.
function capacityPlan(collection, usableLimit, view = { phone: false, openGroups: new Set() }) {
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
    const meter = element("div", {
      className: "capacity-meter",
      role: "meter",
      "aria-label": `Capacity group ${group.index}`,
      "aria-valuemin": "0",
      "aria-valuemax": String(usableLimit),
      "aria-valuenow": String(group.seconds),
      "aria-valuetext": `${group.duration} of ${humanDuration(usableLimit)}`,
    }, [element("span", { style: `--capacity:${percent}%` })]);
    if (view.phone) {
      const count = group.tracks.length;
      const details = element("details", {
        className: "capacity-group-details",
        open: view.openGroups.has(group.index),
      }, [
        element("summary", {
          text: `Tonie ${group.index} · ${group.duration} · ${count} ${count === 1 ? "chapter" : "chapters"}`,
        }),
        meter,
        trackList,
      ]);
      details.addEventListener("toggle", () => {
        if (details.open) view.openGroups.add(group.index);
        else view.openGroups.delete(group.index);
      });
      groups.append(element("li", { className: "capacity-group" }, [details]));
      continue;
    }
    groups.append(element("li", { className: "capacity-group" }, [
      element("div", { className: "capacity-group-heading" }, [
        element("h3", { text: `Creative Tonie ${group.index}` }),
        element("span", { className: "status-stamp", "data-status": "success", text: group.duration }),
      ]),
      meter,
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

  let redrawing = false;

  // Trim and Ads are a titled section on a desktop. On a phone each is a closed
  // details line, so neither sits under 200 chapters; what is open is kept here.
  function assignmentPanel(key, className, titleId, title, children) {
    if (!isPhone()) {
      return element("section", { className: `assignment-panel ${className}`, "aria-labelledby": titleId }, [
        element("h2", { id: titleId, text: title }),
        ...children,
      ]);
    }
    const details = element("details", {
      className: `assignment-panel assignment-details ${className}`,
      open: openPanels.has(key),
    }, [element("summary", {}, [element("h2", { id: titleId, text: title })]), ...children]);
    details.addEventListener("toggle", () => {
      if (details.open) openPanels.add(key);
      else openPanels.delete(key);
    });
    return details;
  }

  // The page is rebuilt on every refresh poll, so what a phone has opened (one
  // chapter's More, the chapter being renamed, the capacity groups) lives here
  // and is drawn back in, never kept only in the DOM.
  const phoneQuery = globalThis.window?.matchMedia?.("(max-width: 759.98px)") || null;
  const isPhone = () => Boolean(phoneQuery?.matches);
  let moreOpen = "";
  let moreFresh = "";
  let renaming = "";
  const openGroups = new Set();
  const openPanels = new Set();
  // The seconds typed into Trim, for the same reason: losing one to a poll
  // would cut the audio by only the other, and a trim cannot be undone.
  const trimSeconds = { head: "0", tail: "0" };
  const onViewport = () => {
    if (collection) renderDetail();
  };
  phoneQuery?.addEventListener?.("change", onViewport);

  function renderDetail({ focusKey = "", fallback = null } = {}) {
    if (!active || signal.aborted || !collection) return;
    const preparation = forgePreparationState(collection, jobs);
    const token = focusKey ? { key: focusKey } : rememberFocus(root);
    // A refresh poll redraws the page every few seconds while work runs. A
    // title being typed (the collection's or a chapter's) survives it rather
    // than snapping back to the saved one. A redraw that names its focus
    // target (after a save) shows the saved titles.
    const drafts = new Map();
    const editing = document.activeElement;
    const editingKey = editing && root.contains(editing) ? editing.getAttribute("data-focus-key") || "" : "";
    if (!focusKey && (editingKey === "collection-title" || /^chapter-.+-title$/.test(editingKey))) {
      drafts.set(editingKey, editing.value);
    }
    // A textarea, so a long title wraps instead of being cut off mid-word.
    const titleInput = element("textarea", {
      id: "collection-title-input",
      className: "collection-title-input",
      name: "collection-title",
      rows: "1",
      "aria-label": "Collection title",
      "data-focus-key": "collection-title",
      maxlength: "240",
      required: true,
      "data-collection-mutation": "",
    });
    titleInput.value = drafts.get("collection-title") ?? (collection.title || "");
    // field-sizing grows the box with its text where supported; elsewhere fit it by hand.
    const fitTitle = () => {
      if (globalThis.CSS?.supports?.("field-sizing", "content")) return;
      titleInput.style.height = "auto";
      titleInput.style.height = `${titleInput.scrollHeight}px`;
    };
    window.requestAnimationFrame(fitTitle);
    // The h1 is the field: Enter or leaving it saves, Escape puts the saved
    // title back. One save at a time, so Enter and the blur it causes are one.
    let savingTitle = false;
    async function saveTitle() {
      // Some engines fire blur on a focused node as a redraw removes it; that
      // is not the operator leaving the field, so it saves nothing.
      if (redrawing || savingTitle || titleInput.isConnected === false) return;
      const title = titleInput.value.trim();
      if (title && title === collection.title) {
        titleInput.value = title;
        return;
      }
      if (!title) {
        titleInput.setCustomValidity("Enter a collection title.");
        titleInput.reportValidity();
        return;
      }
      titleInput.setCustomValidity("");
      savingTitle = true;
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
        notify(error.message, { kind: "failure", timeout: 0 });
        // The failed save reloaded the collection and redrew the field with
        // the saved title; put the typed one back so it is not lost.
        const current = root.querySelector("#collection-title-input") || titleInput;
        current.value = title;
        current.focus({ preventScroll: true });
      } finally {
        savingTitle = false;
      }
    }
    titleInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        saveTitle();
      } else if (event.key === "Escape") {
        titleInput.setCustomValidity("");
        titleInput.value = collection.title || "";
      }
    });
    titleInput.addEventListener("input", () => {
      titleInput.setCustomValidity("");
      fitTitle();
    });
    titleInput.addEventListener("blur", saveTitle);

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
          element("h1", { id: "collection-detail-title", tabindex: "-1" }, [titleInput]),
          ...(isPhone()
            ? phoneFacts(collection, openPanels.has("facts"), (open) => {
              if (open) openPanels.add("facts");
              else openPanels.delete("facts");
            })
            : [detailFacts(collection)]),
        ]),
      ]),
    ]);

    const chaptersHeading = element("div", { className: "section-heading" }, [
      iconNode("library"),
      element("div", {}, [
        element("h2", { id: "chapter-list-title", text: "Chapter order" }),
        // HTML5 drag never starts from a touch, so a coarse pointer reads only
        // the buttons (see the pointer: coarse block in style.css).
        element("p", {}, [
          element("span", { className: "drag-copy", text: "Drag with a pointer, or use the Move up and Move down buttons." }),
          element("span", { className: "drag-copy-coarse", text: "Use the Move up and Move down buttons." }),
          // On a phone those buttons live in each chapter's More menu.
          element("span", { className: "drag-copy-phone", text: "To reorder, open a chapter's More menu." }),
        ]),
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
        "data-more-open": moreOpen === track.name ? "" : null,
        "data-more-fresh": moreOpen === track.name && moreFresh === track.name ? "" : null,
        "data-renaming": renaming === track.name ? "" : null,
      });
      const titleId = `chapter-title-${index}`;
      const titleKey = `chapter-${track.name}-title`;
      const titleInput = element("input", {
        id: titleId,
        "data-focus-key": titleKey,
        maxlength: "240",
        "data-collection-mutation": "",
        "aria-label": `Chapter ${index + 1} title`,
      });
      titleInput.value = drafts.get(titleKey) ?? (track.title || "");
      // Change saves; blur saves too, because a field redrawn with a carried
      // draft has no change baseline left to fire against. One save at a time,
      // and a blur caused by a redraw removing the field saves nothing.
      let savingChapterTitle = false;
      async function saveChapterTitle() {
        if (redrawing || savingChapterTitle || titleInput.isConnected === false) return;
        const title = titleInput.value.trim();
        if (title && title === track.title) return;
        if (!title) {
          titleInput.setCustomValidity("Enter a chapter title.");
          titleInput.reportValidity();
          return;
        }
        titleInput.setCustomValidity("");
        savingChapterTitle = true;
        titleInput.disabled = true;
        try {
          const saved = await mutation.run(async () => {
            await request(`/api/collections/${encodeURIComponent(slug)}/tracks/${encodeURIComponent(track.name)}`, {
              method: "PATCH",
              body: JSON.stringify({ title }),
              signal,
            });
            await loadCollection();
            // On a phone the field is hidden again once saved, so focus goes
            // back to the row's More button.
            renderDetail({ focusKey: isPhone() ? `chapter-${track.name}-more` : titleKey });
            return true;
          });
          if (!saved || !active || signal.aborted) return;
          notify("Chapter title saved.", { kind: "success" });
        } catch (error) {
          if (!active || signal.aborted) return;
          titleInput.disabled = false;
          notify(error.message, { kind: "failure", timeout: 0 });
          // A failed rename keeps its field open, with what was typed in it.
          if (isPhone()) startRename(false);
          titleInput.focus({ preventScroll: true });
        } finally {
          savingChapterTitle = false;
        }
      }
      // A phone shows the title as text; Rename opens the field.
      function startRename(redraw = true) {
        renaming = track.name;
        moreOpen = "";
        if (redraw) renderDetail({ focusKey: titleKey });
        else row.setAttribute("data-renaming", "");
      }
      function leaveRename() {
        if (renaming !== track.name) return;
        renaming = "";
        row.removeAttribute("data-renaming");
      }
      // Enter and leaving the field save, like the collection title. An empty
      // title keeps the field open so its message has somewhere to show.
      function commitTitle() {
        if (titleInput.value.trim()) leaveRename();
        saveChapterTitle();
      }
      titleInput.addEventListener("change", saveChapterTitle);
      titleInput.addEventListener("blur", () => {
        if (redrawing || titleInput.isConnected === false) return;
        commitTitle();
      });
      titleInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          commitTitle();
        } else if (event.key === "Escape") {
          titleInput.setCustomValidity("");
          titleInput.value = track.title || "";
          leaveRename();
          if (isPhone()) row.querySelector(".chapter-more")?.focus({ preventScroll: true });
        }
      });
      const titleText = element("div", { className: "chapter-title-text", text: track.title || "" });
      const moreId = `chapter-actions-${index}`;
      const more = element("button", {
        type: "button",
        className: "icon-button chapter-more",
        "aria-label": `More actions for ${track.title}`,
        "aria-expanded": String(moreOpen === track.name),
        "aria-controls": moreId,
        "data-focus-key": `chapter-${track.name}-more`,
        onclick: () => {
          moreOpen = moreOpen === track.name ? "" : track.name;
          // Only this render's row eases in; a refresh poll rebuilds it settled.
          moreFresh = moreOpen;
          renderDetail({ focusKey: `chapter-${track.name}-more` });
          moreFresh = "";
        },
      }, [iconNode("more")]);
      const rename = element("button", {
        type: "button",
        className: "button button-secondary chapter-rename",
        "data-focus-key": `chapter-${track.name}-rename`,
        "aria-label": `Rename ${track.title}`,
        "data-collection-mutation": "",
        onclick: () => startRename(),
      }, [element("span", { text: "Rename" })]);
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
      moveUp.append(element("span", { className: "chapter-action-label", text: "Move up" }));
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
      moveDown.append(element("span", { className: "chapter-action-label", text: "Move down" }));
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
      removeButton.append(element("span", { className: "chapter-action-label", text: "Remove" }));
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
            // The neighbour's Remove waits behind More on a phone.
            moreOpen = "";
            renderDetail({
              focusKey: fallbackTrack
                ? `chapter-${fallbackTrack.name}-${isPhone() ? "more" : "remove"}`
                : "collection-title",
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
      const actions = element("div", { className: "chapter-more-actions", id: moreId }, [rename, moveUp, moveDown, removeButton]);
      const controls = element("div", { className: "chapter-row-controls" }, [play, more, actions]);
      const markList = chapterMarks(track);
      const marks = markList.length ? element("span", { className: "chapter-marks" }, markList) : null;
      row.append(
        iconNode("grip", "chapter-grip"),
        element("span", { className: "chapter-index", text: String(index + 1) }),
        element("div", { className: "chapter-title-field" }, [titleText, titleInput, element("span", { text: track.duration }), marks]),
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
    const trimming = Boolean(activeRewrite(jobs, slug, new Set(["trim"])));
    const trimButton = element("button", {
      type: "button",
      className: "button button-secondary trim-button",
      text: trimming ? "Trimming" : "Trim every chapter",
      "data-focus-key": "trim-submit",
      "data-collection-mutation": "",
    });
    const trimPanel = assignmentPanel("trim", "trim-panel", "trim-title", "Trim", [
      element("p", {
        text: trimming
          ? "Trimming every chapter. Editing stays locked until it finishes."
          : "Cut the same seconds off every chapter. This changes the saved audio and cannot be undone.",
      }),
      trimSecondsField("trim-head-input", "Cut from start, seconds", trimSeconds.head, (value) => { trimSeconds.head = value; }),
      trimSecondsField("trim-tail-input", "Cut from end, seconds", trimSeconds.tail, (value) => { trimSeconds.tail = value; }),
      trimButton,
    ]);
    trimButton.addEventListener("click", async () => {
      const trimHead = Number(trimSeconds.head) || 0;
      const trimTail = Number(trimSeconds.tail) || 0;
      if (trimHead <= 0 && trimTail <= 0) {
        notify("Enter seconds to cut from the start or the end.", { kind: "failure", timeout: 0 });
        return;
      }
      trimButton.disabled = true;
      try {
        const receipt = await request(`/api/collections/${encodeURIComponent(slug)}/trim`, {
          method: "POST",
          body: JSON.stringify({ trim_head: trimHead, trim_tail: trimTail }),
          signal,
        });
        if (!active || signal.aborted) return;
        jobs = [{ id: receipt.job_id, kind: "trim", status: "queued", payload: { slug } }, ...jobs];
        trimSeconds.head = "0";
        trimSeconds.tail = "0";
        renderDetail({ focusKey: "trim-submit" });
        notify(`${collection.title || "Collection"} is queued to be trimmed.`, { kind: "success" });
        await refresh.request();
      } catch (error) {
        if (!active || signal.aborted) return;
        trimButton.disabled = false;
        notify(error.message, { kind: "failure", timeout: 0 });
      }
    });
    let adsPanel = null;
    if (collection.source === "podcast") {
      const removing = Boolean(activeRewrite(jobs, slug, new Set(["remove_ads"])));
      const last = collection.forge?.ads_last;
      const adsButton = element("button", {
        type: "button",
        className: "button button-secondary remove-ads-button",
        text: removing ? "Removing ads" : "Remove ads",
        "data-focus-key": "remove-ads-submit",
        "data-collection-mutation": "",
      });
      const lastLines = [];
      if (last) {
        const chapters = last.chapters_changed === 1 ? "1 chapter" : `${last.chapters_changed} chapters`;
        lastLines.push(element("p", {
          text: last.cut_seconds
            ? `Last check cut ${last.cut_seconds} sec from ${chapters}.`
            : "Last check found no ads to cut.",
        }));
        if (last.unchecked?.length) {
          lastLines.push(element("p", { text: `Not checked: ${last.unchecked.join(", ")}.` }));
        }
      }
      adsPanel = assignmentPanel("ads", "remove-ads-panel", "remove-ads-title", "Ads", [
        element("p", {
          text: removing
            ? "Removing ads from every chapter. Editing stays locked until it finishes."
            : "Downloads each episode again and cuts what the new copy does not have. Ads left in may go on a later run. This cannot be undone. Send the story to your Tonie again afterward.",
        }),
        ...lastLines,
        adsButton,
      ]);
      adsButton.addEventListener("click", async () => {
        adsButton.disabled = true;
        try {
          const receipt = await request(`/api/collections/${encodeURIComponent(slug)}/remove-ads`, {
            method: "POST",
            signal,
          });
          if (!active || signal.aborted) return;
          jobs = [{ id: receipt.job_id, kind: "remove_ads", status: "queued", payload: { slug } }, ...jobs];
          renderDetail({ focusKey: "remove-ads-submit" });
          notify(`${collection.title || "Collection"} is queued to have ads removed.`, { kind: "success" });
          await refresh.request();
        } catch (error) {
          if (!active || signal.aborted) return;
          adsButton.disabled = false;
          notify(error.message, { kind: "failure", timeout: 0 });
        }
      });
    }
    redrawing = true;
    try {
      replace(root, header, element("div", { className: "collection-detail-grid" }, [
        element("div", { className: "collection-detail-main" }, [chapters]),
        element("aside", { className: "collection-detail-plan" }, preparation.state === "ready"
          ? [capacityPlan(collection, status.usable_limit_seconds, { phone: isPhone(), openGroups }), trimPanel, adsPanel].filter(Boolean)
          : [preparationPanel]),
      ]));
    } finally {
      redrawing = false;
    }
    // A trim or an ad removal swaps the whole collection folder when it
    // finishes, so an edit made meanwhile would wait on the server and then
    // act on the old files.
    const rewriting = Boolean(activeRewrite(jobs, slug));
    if (rewriting) {
      for (const control of root.querySelectorAll("[data-collection-mutation]")) control.disabled = true;
    }
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
    const wasRewriting = Boolean(activeRewrite(jobs, slug));
    if (!snapshot.stale?.includes("jobs")) jobs = snapshot.jobs || [];
    const indexed = snapshot.collections?.find((item) => item.slug === slug);
    const rewriteFinished = wasRewriting && !activeRewrite(jobs, slug);
    if (rewriteFinished || (indexed?.stage === "forged" && collection?.stage !== "forged")) {
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
    phoneQuery?.removeEventListener?.("change", onViewport);
  };
}

export function createCollectionScreen({
  request = api,
  refresh,
  player,
} = {}) {
  if (!refresh) throw new Error("The collection screen requires the application refresh coordinator.");
  if (!player) throw new Error("The collection screen requires the persistent audio player.");

  // `/collection/:slug` has a required parameter, so a matched route always
  // carries a slug and this screen never has to render without one.
  return function renderCollectionRoute({ workspace, params, signal }) {
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
