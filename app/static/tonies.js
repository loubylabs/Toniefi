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


export function buildTonieChapterPayload(tonie, chapters) {
  return {
    base: (tonie.chapters || []).map(({ id, title }) => ({ id, title: title || "" })),
    chapters: chapters.map(({ id, title }) => ({ id, title: title || "" })),
  };
}


export function createTonieMutation({
  request,
  reload,
  setPending = () => {},
  onUpdated = () => {},
  onReloadFailure = () => {},
  signal = null,
}) {
  let pending = false;

  async function save(tonie, chapters) {
    if (pending || signal?.aborted) return false;
    pending = true;
    setPending(true);
    const path = `/api/tonies/${encodeURIComponent(tonie.householdId)}/${encodeURIComponent(tonie.id)}/chapters`;
    try {
      const updated = await request(path, {
        method: "PUT",
        body: JSON.stringify(buildTonieChapterPayload(tonie, chapters)),
        ...(signal ? { signal } : {}),
      });
      if (!signal?.aborted) onUpdated(updated, tonie);
      return updated;
    } catch (error) {
      if (!signal?.aborted) {
        try {
          await reload();
        } catch (reloadError) {
          onReloadFailure(reloadError);
        }
      }
      throw error;
    } finally {
      pending = false;
      if (!signal?.aborted) setPending(false);
    }
  }

  return {
    save,
    get pending() { return pending; },
  };
}


function iconNode(name, className = "") {
  const node = element("span", { className, "aria-hidden": "true" });
  node.innerHTML = icon(name);
  return node;
}


function tonieKey(tonie) {
  return `${tonie.householdId}:${tonie.id}`;
}


function chapterDrafts(list) {
  return Array.from(list.querySelectorAll("[data-tonie-chapter]")).map((row) => ({
    id: row.dataset.tonieChapter,
    title: row.querySelector("input").value,
  }));
}


export function createToniesScreen({ request = api } = {}) {
  return function renderToniesScreen({ workspace, signal }) {
    let active = true;
    let tonies = [];
    let openKey = "";
    let staleMessage = "";
    let loadToken = 0;
    let initialLoad = true;

    const root = element("section", { className: "tonies-screen", "aria-labelledby": "tonies-title" });
    const refreshButton = element("button", {
      type: "button",
      className: "button button-secondary",
      "data-tonie-control": "",
    }, [iconNode("refresh"), element("span", { text: "Refresh Tonies" })]);
    const header = element("div", { className: "screen-heading" }, [
      element("div", {}, [
        element("h1", { id: "tonies-title", text: "Creative Tonies" }),
        element("p", { text: "Manage current cloud chapters safely. These actions never change your local library." }),
      ]),
      refreshButton,
    ]);
    const stale = element("div", { className: "stale-notice", role: "status", hidden: true });
    const list = element("ul", { className: "tonie-list", "aria-live": "polite" });

    async function load({ announceSuccess = false } = {}) {
      const token = ++loadToken;
      try {
        const fetched = await request("/api/tonies", { ...(signal ? { signal } : {}) });
        if (!active || signal?.aborted || token !== loadToken) return null;
        tonies = fetched;
        staleMessage = "";
        initialLoad = false;
        if (openKey && !tonies.some((tonie) => tonieKey(tonie) === openKey)) openKey = "";
        render();
        if (announceSuccess) {
          notify("Creative Tonies refreshed from myTonies.", { kind: "success" });
          announce("Creative Tonies refreshed from remote truth.");
        }
        return fetched;
      } catch (error) {
        if (!active || signal?.aborted || token !== loadToken) return null;
        staleMessage = error.message;
        initialLoad = false;
        render();
        throw error;
      }
    }

    const mutation = createTonieMutation({
      request,
      reload: () => load(),
      signal,
      setPending: () => render(),
      onUpdated(updated, previous) {
        loadToken += 1;
        tonies = tonies.map((tonie) => tonieKey(tonie) === tonieKey(previous) ? updated : tonie);
        staleMessage = "";
        render();
      },
      onReloadFailure(error) {
        staleMessage = error.message;
        render();
      },
    });

    async function saveChapters(tonie, chapters, successMessage) {
      try {
        const updated = await mutation.save(tonie, chapters);
        if (!updated || !active || signal?.aborted) return;
        notify(successMessage, { kind: "success" });
        announce(successMessage);
      } catch (error) {
        if (!active || signal?.aborted) return;
        notify(`${error.message} TonieFi reloaded the Tonie from myTonies.`, { kind: "failure", timeout: 0 });
      }
    }

    function chapterRow(tonie, chapter, index) {
      const titleId = `tonie-${tonie.id}-chapter-${chapter.id}`;
      const title = element("input", {
        id: titleId,
        type: "text",
        value: chapter.title || "",
        maxlength: "128",
        "aria-label": `Chapter ${index + 1} title`,
        "data-focus-key": `tonie-${chapter.id}-title`,
        "data-tonie-control": "",
      });
      const up = element("button", {
        type: "button",
        className: "icon-button tonie-move",
        "aria-label": `Move ${chapter.title || `chapter ${index + 1}`} up`,
        title: "Move up",
        disabled: index === 0,
        "data-tonie-control": "",
        "data-focus-key": `tonie-${chapter.id}-up`,
      }, [iconNode("arrowUp")]);
      const down = element("button", {
        type: "button",
        className: "icon-button tonie-move",
        "aria-label": `Move ${chapter.title || `chapter ${index + 1}`} down`,
        title: "Move down",
        disabled: index === tonie.chapters.length - 1,
        "data-tonie-control": "",
        "data-focus-key": `tonie-${chapter.id}-down`,
      }, [iconNode("arrowDown")]);
      const removeButton = element("button", {
        type: "button",
        className: "button button-secondary tonie-remove",
        "data-tonie-control": "",
        "data-focus-key": `tonie-${chapter.id}-remove`,
      }, [iconNode("trash"), element("span", { text: "Remove" })]);
      const row = element("li", {
        className: "tonie-chapter-row",
        draggable: true,
        "data-tonie-chapter": chapter.id,
        "data-tonie-control": "",
      }, [
        iconNode("grip", "tonie-grip"),
        element("span", { className: "tonie-chapter-number", text: String(index + 1) }),
        element("div", { className: "tonie-chapter-title" }, [
          element("label", { for: titleId, className: "visually-hidden", text: `Chapter ${index + 1} title` }),
          title,
          element("span", { className: "tonie-chapter-meta", text: chapter.transcoding ? "Processing" : chapter.duration || "Duration pending" }),
        ]),
        element("div", { className: "tonie-chapter-actions", role: "group", "aria-label": `Reorder ${chapter.title || `chapter ${index + 1}`}` }, [up, down]),
        removeButton,
      ]);

      title.addEventListener("change", () => saveChapters(tonie, chapterDrafts(row.parentNode), "Chapter title saved to the Tonie."));
      up.addEventListener("click", () => saveChapters(tonie, moveItem(chapterDrafts(row.parentNode), index, -1), "Chapter moved up on the Tonie."));
      down.addEventListener("click", () => saveChapters(tonie, moveItem(chapterDrafts(row.parentNode), index, 1), "Chapter moved down on the Tonie."));
      removeButton.addEventListener("click", async () => {
        const name = title.value || "this chapter";
        const confirmed = await showConfirmDialog({
          title: `Remove ${name}?`,
          message: `Remove "${name}" from "${tonie.name || "this Tonie"}"?\n\nThis cannot be undone. Your library on disk is not touched.`,
          confirmLabel: "Remove chapter",
          destructive: true,
        });
        removeButton.focus({ preventScroll: true });
        if (!confirmed) return;
        await saveChapters(
          tonie,
          chapterDrafts(row.parentNode).filter((item) => item.id !== chapter.id),
          "Chapter removed from the Tonie. Your local library was not changed.",
        );
      });
      return row;
    }

    function tonieDetail(tonie) {
      const detail = element("div", { className: "tonie-detail" });
      if (!tonie.chapters?.length) {
        detail.append(element("div", { className: "empty-state tonie-empty" }, [
          iconNode("tonie"),
          element("strong", { text: "Nothing is stored on this Tonie" }),
          element("p", { text: "Choose this Tonie from a prepared collection in Review when you are ready to send audio." }),
        ]));
        return detail;
      }

      const clearButton = element("button", {
        type: "button",
        className: "button button-danger tonie-clear",
        "data-tonie-control": "",
      }, [iconNode("trash"), element("span", { text: "Clear all chapters" })]);
      clearButton.addEventListener("click", async () => {
        const confirmed = await showConfirmDialog({
          title: `Clear ${tonie.name || "this Tonie"}?`,
          message: `Clear all ${tonie.chapters.length} chapters from "${tonie.name || "this Tonie"}"?\n\nThis cannot be undone. Your library on disk is not touched.`,
          confirmLabel: "Clear every chapter",
          destructive: true,
        });
        clearButton.focus({ preventScroll: true });
        if (!confirmed) return;
        await saveChapters(tonie, [], "Every chapter was cleared from the Tonie. Your local library was not changed.");
      });
      const intro = element("div", { className: "tonie-detail-heading" }, [
        element("p", { text: "Rename or reorder chapters here. Pointer drag and the Move buttons save the same canonical chapter list." }),
        clearButton,
      ]);
      const chapters = element("ol", { className: "tonie-chapter-list" });
      tonie.chapters.forEach((chapter, index) => chapters.append(chapterRow(tonie, chapter, index)));
      let draggedIndex = -1;
      chapters.addEventListener("dragstart", (event) => {
        const row = event.target.closest("[data-tonie-chapter]");
        draggedIndex = Array.from(chapters.children).indexOf(row);
        event.dataTransfer?.setData("text/plain", row?.dataset.tonieChapter || "");
        if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
      });
      chapters.addEventListener("dragover", (event) => event.preventDefault());
      chapters.addEventListener("drop", (event) => {
        event.preventDefault();
        const target = event.target.closest("[data-tonie-chapter]");
        const targetIndex = Array.from(chapters.children).indexOf(target);
        if (draggedIndex < 0 || targetIndex < 0 || draggedIndex === targetIndex) return;
        const items = chapterDrafts(chapters);
        const [moved] = items.splice(draggedIndex, 1);
        items.splice(targetIndex, 0, moved);
        draggedIndex = -1;
        saveChapters(tonie, items, "Chapter order saved to the Tonie.");
      });
      detail.append(intro, chapters);
      return detail;
    }

    function tonieRow(tonie) {
      const key = tonieKey(tonie);
      const open = key === openKey;
      const button = element("button", {
        type: "button",
        className: "tonie-summary",
        "aria-expanded": String(open),
        "data-focus-key": `tonie-${key}-summary`,
        "data-tonie-summary": "",
      }, [
        iconNode("tonie", "tonie-summary-mark"),
        element("span", { className: "tonie-summary-copy" }, [
          element("strong", { text: tonie.name || "Creative Tonie" }),
          element("small", { text: tonie.householdName || "Household name unavailable" }),
        ]),
        element("span", { className: "tonie-summary-facts" }, [
          element("span", { text: `${tonie.chapter_count || 0} ${tonie.chapter_count === 1 ? "chapter" : "chapters"}` }),
          element("span", { text: `${tonie.time_free || "Unknown"} free` }),
        ]),
        iconNode("chevronRight", "tonie-summary-chevron"),
      ]);
      button.addEventListener("click", () => {
        if (mutation.pending) return;
        openKey = open ? "" : key;
        render({ focusKey: `tonie-${key}-summary` });
      });
      const children = [button];
      if (open) children.push(tonieDetail(tonie));
      return element("li", { className: "tonie-row", "data-open": String(open) }, children);
    }

    function render({ focusKey = "" } = {}) {
      if (!active || signal?.aborted) return;
      const token = focusKey ? { key: focusKey } : rememberFocus(root);
      stale.hidden = !staleMessage;
      if (staleMessage) {
        const retry = element("button", { type: "button", className: "button button-secondary", text: "Retry remote read" });
        retry.addEventListener("click", () => load({ announceSuccess: true }).catch(() => {}));
        replace(stale,
          element("strong", { text: "Creative Tonie information may be stale" }),
          element("p", { text: `${staleMessage} Existing figures remain visible until a fresh myTonies read succeeds.` }),
          retry,
        );
      } else {
        replace(stale);
      }

      if (initialLoad) {
        replace(list, element("li", { className: "route-pending tonie-loading" }, [
          iconNode("tonie", "route-pending-mark"),
          element("h2", { text: "Reading Creative Tonies" }),
          element("p", { text: "Fetching the latest chapters and capacity from myTonies." }),
        ]));
      } else if (!tonies.length) {
        replace(list, element("li", { className: "empty-state tonie-list-empty" }, [
          iconNode("tonie"),
          element("strong", { text: "No Creative Tonies were found" }),
          element("p", { text: "Check the connected account in Settings, then refresh this list." }),
          element("a", { href: "/settings", className: "button button-primary", "data-route": "settings" }, [
            iconNode("settings"), element("span", { text: "Open Settings" }),
          ]),
        ]));
      } else {
        replace(list, ...tonies.map(tonieRow));
      }
      root.querySelectorAll("[data-tonie-control]").forEach((control) => {
        control.disabled = mutation.pending;
        if (control.hasAttribute("draggable")) control.draggable = !mutation.pending;
      });
      root.querySelectorAll("[data-tonie-summary]").forEach((control) => { control.disabled = mutation.pending; });
      setBusy(root, mutation.pending, "Saving Creative Tonie changes");
      restoreFocus(token, { root, fallback: refreshButton });
    }

    refreshButton.addEventListener("click", async () => {
      refreshButton.disabled = true;
      try {
        await load({ announceSuccess: true });
      } catch (error) {
        if (active && !signal?.aborted) notify(error.message, { kind: "failure", timeout: 0 });
      } finally {
        if (active && !signal?.aborted && !mutation.pending) refreshButton.disabled = false;
      }
    });

    root.append(header, stale, list);
    replace(workspace, root);
    render();
    load().catch((error) => {
      if (active && !signal?.aborted) notify(error.message, { kind: "failure", timeout: 0 });
    });
    return () => {
      active = false;
      loadToken += 1;
    };
  };
}
