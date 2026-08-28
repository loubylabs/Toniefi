import { api } from "./api.js";
import { icon } from "./icons.js";
import {
  announce,
  element,
  notify,
  rememberFocus,
  replace,
  restoreFocus,
  setBusy,
  showConfirmDialog,
} from "./shared.js";

export function filterCollectionsByTitle(collections, query) {
  const needle = String(query || "").trim().toLocaleLowerCase();
  if (!needle) return collections.slice();
  return collections.filter((collection) => String(collection.title || "").toLocaleLowerCase().includes(needle));
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

export function createLibraryScreen({
  request = api,
  refresh,
} = {}) {
  if (!refresh) throw new Error("Library requires the application refresh coordinator.");

  return function renderLibrary({ workspace }) {
    let collections = refresh.snapshot.collections || [];
    let query = "";
    const root = element("section", { className: "library-screen", "aria-labelledby": "library-title" });
    const titleGroup = element("div", {}, [
      element("h1", { id: "library-title", text: "Library" }),
      element("p", { text: "Every local collection stays available as an ordinary folder of audio files." }),
    ]);
    const rescan = element("button", {
      type: "button",
      className: "button button-secondary library-rescan",
      "data-focus-key": "library-rescan",
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

    function collectionRow(collection, index, shown) {
      const titleId = `library-collection-${collection.slug}`;
      const open = element("a", {
        className: "button button-primary",
        href: `/review/${encodeURIComponent(collection.slug)}`,
        "data-route": "review",
        "data-focus-key": `library-${collection.slug}-open`,
      }, [iconNode("review"), element("span", { text: "Open for review" })]);
      const removeButton = element("button", {
        type: "button",
        className: "button button-secondary library-delete",
        "data-focus-key": `library-${collection.slug}-delete`,
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
          await request(`/api/collections/${encodeURIComponent(collection.slug)}`, { method: "DELETE" });
          collections = collections.filter((item) => item.slug !== collection.slug);
          render({ focusKey: fallback ? `library-${fallback.slug}-open` : "library-search" });
          notify(`${collection.title || "The collection"} and its local audio files were deleted.`, { kind: "success" });
          announce(`${collection.title || "Collection"} deleted from the local library.`);
          await refresh.request();
        } catch (error) {
          removeButton.disabled = false;
          notify(error.message, { kind: "failure", timeout: 0 });
          removeButton.focus({ preventScroll: true });
        }
      });
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
        element("div", { className: "library-row-actions" }, [open, removeButton]),
      ]);
      return element("li", { className: "library-row", "aria-labelledby": titleId }, [collectionCover(collection), body]);
    }

    function render({ focusKey = "" } = {}) {
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
        replace(list, ...shown.map((collection, index) => collectionRow(collection, index, shown)));
      }
      restoreFocus(token, { root, fallback: search });
    }

    function onRefresh(snapshot) {
      const failed = snapshot.stale?.includes("collections");
      stale.hidden = !failed;
      stale.textContent = failed
        ? "Library information could not refresh. Showing the last available local collection index."
        : "";
      if (!failed) collections = snapshot.collections || [];
      render();
    }

    search.addEventListener("input", () => {
      query = search.value;
      render({ focusKey: "library-search" });
    });

    rescan.addEventListener("click", async () => {
      rescan.disabled = true;
      setBusy(root, true, "Rescanning local collection folders");
      const current = collections.slice();
      const outcomes = await Promise.allSettled(current.map((collection) => (
        request(`/api/collections/${encodeURIComponent(collection.slug)}?refresh=true`)
      )));
      const failures = outcomes.filter((outcome) => outcome.status === "rejected");
      try {
        await refresh.request();
      } finally {
        setBusy(root, false);
        rescan.disabled = false;
        rescan.focus({ preventScroll: true });
      }
      if (failures.length) {
        notify(`${failures.length} ${failures.length === 1 ? "folder" : "folders"} could not be rescanned. The last available details remain visible.`, {
          kind: "failure",
          timeout: 0,
        });
      } else {
        notify("Local collection folders rescanned.", { kind: "success" });
        announce("Library rescan complete.");
      }
    });

    root.append(header, searchField, stale, summary, list);
    replace(workspace, root);
    render();
    const unsubscribe = refresh.subscribe(onRefresh);
    refresh.request();
    return () => unsubscribe();
  };
}
