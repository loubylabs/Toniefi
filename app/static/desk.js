import { api } from "./api.js";
import { icon } from "./icons.js";
import {
  announce,
  element,
  notify,
  replace,
  setBusy,
  withFocusRestored,
} from "./shared.js";

const MAX_SOURCES = 50;
const DESK_JOB_KINDS = new Set(["prepare_url", "librivox", "forge"]);

export const DEFAULT_FORGE_OPTIONS = Object.freeze({
  use_chapters: true,
  normalize: true,
  clean_titles: true,
  trim_head: 0,
  trim_tail: 0,
  split_oversized: true,
});

function sourceValues(lines) {
  const values = Array.isArray(lines)
    ? lines.map((line) => typeof line === "object" ? line.value : line)
    : String(lines ?? "").split(/\r?\n/);
  return values.map((value) => String(value ?? "").trim()).filter(Boolean);
}

function hasSupportedScheme(value) {
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") && Boolean(url.hostname);
  } catch (_error) {
    return false;
  }
}

export function parseSourceLines(lines) {
  const values = sourceValues(lines);
  const seen = new Map();
  let uniqueCount = 0;
  const rows = values.map((value, index) => {
    if (seen.has(value)) {
      return { value, error: `This source duplicates row ${seen.get(value) + 1}.` };
    }
    seen.set(value, index);
    uniqueCount += 1;
    if (!hasSupportedScheme(value)) {
      return { value, error: "Use an HTTP or HTTPS source URL." };
    }
    if (uniqueCount > MAX_SOURCES) {
      return { value, error: "Only 50 unique source URLs can be prepared at once." };
    }
    return { value, error: "" };
  });
  return {
    rows,
    uniqueCount,
    valid: rows.length > 0 && rows.every((row) => !row.error),
  };
}

function normalizedOptions(options = {}) {
  const merged = { ...DEFAULT_FORGE_OPTIONS, ...options };
  return {
    use_chapters: Boolean(merged.use_chapters),
    normalize: Boolean(merged.normalize),
    clean_titles: Boolean(merged.clean_titles),
    trim_head: Number(merged.trim_head) || 0,
    trim_tail: Number(merged.trim_tail) || 0,
    split_oversized: Boolean(merged.split_oversized),
  };
}

export function buildPreparePayload(lines, options = {}) {
  const parsed = parseSourceLines(lines);
  if (!parsed.valid) throw new Error("Fix every source before preparing this batch.");
  return {
    sources: parsed.rows.map((row) => ({ url: row.value })),
    options: normalizedOptions(options),
  };
}

export function buildForgePayload(slug, options = {}) {
  const normalized = normalizedOptions(options);
  return {
    slug,
    normalize: normalized.normalize,
    clean_titles: normalized.clean_titles,
    trim_head: normalized.trim_head,
    trim_tail: normalized.trim_tail,
    split_oversized: normalized.split_oversized,
  };
}

function jobSlug(job) {
  return job.result?.slug || job.payload?.slug || "";
}

function workPhase(job, collection) {
  if (job.status === "failed") return "failed";
  if (collection?.stage === "forged" || job.phase === "ready") return "ready";
  if (job.status === "queued") return "queued";
  if (job.phase === "forging" || job.kind === "forge") return "forging";
  if (job.phase === "extracting" || job.kind === "librivox" || job.kind === "prepare_url") {
    return "extracting";
  }
  return "queued";
}

function sourceLabel(job, collection) {
  return job.payload?.url || collection?.url || collection?.source || job.label || "Local collection";
}

export function buildWorkCartItems(jobs, collections, limit = 7) {
  const collectionBySlug = new Map(collections.map((collection) => [collection.slug, collection]));
  const represented = new Set();
  const items = [];

  for (const job of jobs) {
    if (!DESK_JOB_KINDS.has(job.kind)) continue;
    const slug = jobSlug(job);
    if (slug && represented.has(slug)) continue;
    const collection = slug ? collectionBySlug.get(slug) : null;
    if (slug) represented.add(slug);
    const phase = workPhase(job, collection);
    items.push({
      key: `job-${job.id}`,
      jobId: job.id,
      kind: job.kind,
      phase,
      title: collection?.title || job.label || "Untitled collection",
      source: sourceLabel(job, collection),
      progress: job.progress || (phase === "queued" ? "Waiting for a worker" : ""),
      error: job.error || "",
      slug,
      canRetry: job.status === "failed",
      trackCount: Number(collection?.track_count) || 0,
      duration: collection?.total_duration || "",
      hasCover: Boolean(collection?.cover),
    });
    if (items.length >= limit) return items;
  }

  for (const collection of collections) {
    if (items.length >= limit) break;
    if (collection.stage !== "forged" || represented.has(collection.slug)) continue;
    represented.add(collection.slug);
    items.push({
      key: `collection-${collection.slug}`,
      jobId: null,
      kind: "collection",
      phase: "ready",
      title: collection.title || "Untitled collection",
      source: collection.url || collection.source || "Local collection",
      progress: "Ready for your review",
      error: "",
      slug: collection.slug,
      canRetry: false,
      trackCount: Number(collection.track_count) || 0,
      duration: collection.total_duration || "",
      hasCover: Boolean(collection.cover),
    });
  }
  return items;
}

function iconNode(name, className = "") {
  const node = element("span", { className, "aria-hidden": "true" });
  node.innerHTML = icon(name);
  return node;
}

function phaseDetails(phase) {
  return {
    queued: { label: "Queued", icon: "clock" },
    extracting: { label: "Extracting", icon: "arrowDown" },
    forging: { label: "Forging", icon: "forge" },
    ready: { label: "Ready to review", icon: "check" },
    failed: { label: "Failed", icon: "alert" },
  }[phase] || { label: "Queued", icon: "clock" };
}

function initials(title) {
  const words = String(title || "Story").trim().split(/\s+/).filter(Boolean);
  return words.slice(0, 2).map((word) => word[0]?.toUpperCase() || "").join("") || "ST";
}

function optionControl({ name, label, checked }) {
  const input = element("input", { type: "checkbox", name });
  input.checked = checked;
  return element("label", { className: "forge-option-toggle" }, [input, element("span", { text: label })]);
}

function createForgeSummary() {
  const heading = element("h2", { id: "forge-summary-title", text: "Forge defaults" });
  const headingRow = element("div", { className: "desk-section-heading" }, [
    iconNode("forge", "desk-section-icon"),
    heading,
    element("span", { className: "status-stamp", "data-status": "success", text: "Safe maximum" }),
  ]);
  const definitions = element("dl", { className: "forge-definition-list" }, [
    element("div", {}, [element("dt", { text: "Loudness" }), element("dd", { text: "−16 LUFS, −1.5 dBTP ceiling" })]),
    element("div", {}, [element("dt", { text: "Title cleanup" }), element("dd", { text: "On" })]),
    element("div", {}, [element("dt", { text: "Chapter markers" }), element("dd", { text: "Preserved" })]),
    element("div", {}, [element("dt", { text: "Oversized tracks" }), element("dd", { text: "Split" })]),
    element("div", {}, [element("dt", { text: "Automatic trimming" }), element("dd", { text: "Off" })]),
  ]);
  const controls = element("fieldset", { className: "forge-option-controls" }, [
    element("legend", { className: "visually-hidden", text: "Edit Forge defaults" }),
    optionControl({ name: "normalize", label: "Normalize loudness", checked: true }),
    optionControl({ name: "clean_titles", label: "Clean source noise from titles", checked: true }),
    optionControl({ name: "use_chapters", label: "Preserve chapter markers", checked: true }),
    optionControl({ name: "split_oversized", label: "Split oversized tracks", checked: true }),
    element("label", { className: "forge-number-control" }, [
      element("span", { text: "Trim start, seconds" }),
      element("input", { type: "number", name: "trim_head", min: "0", step: "0.1", value: "0", inputmode: "decimal" }),
    ]),
    element("label", { className: "forge-number-control" }, [
      element("span", { text: "Trim end, seconds" }),
      element("input", { type: "number", name: "trim_tail", min: "0", step: "0.1", value: "0", inputmode: "decimal" }),
    ]),
  ]);
  const disclosure = element("details", { className: "forge-edit-disclosure" }, [
    element("summary", {}, [iconNode("settings"), element("span", { text: "Edit defaults" })]),
    controls,
  ]);
  return element("section", { className: "forge-summary", "aria-labelledby": "forge-summary-title" }, [
    headingRow,
    definitions,
    disclosure,
  ]);
}

function readForgeOptions(root) {
  const value = (name) => root.querySelector(`[name="${name}"]`);
  return normalizedOptions({
    use_chapters: value("use_chapters")?.checked,
    normalize: value("normalize")?.checked,
    clean_titles: value("clean_titles")?.checked,
    trim_head: value("trim_head")?.value,
    trim_tail: value("trim_tail")?.value,
    split_oversized: value("split_oversized")?.checked,
  });
}

function workCartRow(item, { request, requestRefresh, navigate }) {
  const details = phaseDetails(item.phase);
  const row = element("li", { className: "work-cart-row", "data-phase": item.phase });
  const cover = item.hasCover && item.slug
    ? element("img", {
      className: "work-cart-cover",
      src: `/api/collections/${encodeURIComponent(item.slug)}/cover`,
      alt: "",
      loading: "lazy",
    })
    : element("span", { className: "work-cart-cover work-cart-cover-fallback", text: initials(item.title), "aria-hidden": "true" });
  const stamp = element("span", { className: "status-stamp", "data-status": item.phase }, [
    iconNode(details.icon),
    element("span", { text: details.label }),
  ]);
  const heading = element("h3", { text: item.title });
  const header = element("div", { className: "work-cart-row-header" }, [heading, stamp]);
  const source = element("p", { className: "work-cart-source", text: item.source });
  const facts = element("p", { className: "work-cart-facts" });
  if (item.trackCount) facts.append(element("span", { text: `${item.trackCount} ${item.trackCount === 1 ? "chapter" : "chapters"}` }));
  if (item.duration) facts.append(element("span", { text: item.duration }));
  const progress = element("p", { className: "work-cart-progress", text: item.progress || details.label });
  const body = element("div", { className: "work-cart-row-body" }, [header, source]);
  if (facts.childNodes.length) body.append(facts);
  if (item.phase === "failed") {
    body.append(element("p", { className: "work-cart-error", role: "alert", text: item.error || "Preparation stopped before this collection was ready." }));
  } else {
    body.append(progress);
    if (item.phase !== "ready") {
      body.append(element("span", { className: "work-cart-progress-track", "aria-hidden": "true" }, [
        element("span", { className: "work-cart-progress-fill" }),
      ]));
    }
  }

  const actions = element("div", { className: "work-cart-actions" });
  if (item.phase === "ready" && item.slug) {
    actions.append(element("a", {
      className: "work-cart-link work-cart-review-link",
      href: `/review/${encodeURIComponent(item.slug)}`,
      "data-route": "review",
    }, [element("span", { text: "Review" }), iconNode("chevronRight")]));
  } else {
    const detailsLink = element("a", {
      className: "work-cart-link",
      href: "/activity",
      "data-route": "activity",
    }, [element("span", { text: "View details" }), iconNode("chevronRight")]);
    actions.append(detailsLink);
  }
  if (item.canRetry) {
    const retry = element("button", { type: "button", className: "button button-danger work-cart-retry" }, [
      iconNode("retry"),
      element("span", { text: "Retry" }),
    ]);
    retry.addEventListener("click", async () => {
      retry.disabled = true;
      try {
        await request(`/api/jobs/${item.jobId}/retry`, { method: "POST" });
        notify(`${item.title} is queued again.`, { kind: "success" });
        await requestRefresh();
      } catch (error) {
        retry.disabled = false;
        notify(error.message, { kind: "failure", timeout: 0 });
      }
    });
    actions.append(retry);
  }
  body.append(actions);
  row.append(cover, body);
  return row;
}

function createLiveWorkCart({ request, requestRefresh, navigate }) {
  const list = element("ul", { className: "work-cart-list" });
  const empty = element("div", { className: "empty-state work-cart-empty" }, [
    iconNode("desk"),
    element("strong", { text: "The cart is clear" }),
    element("p", { text: "Prepared stories and work in progress will stay visible here." }),
  ]);
  const count = element("span", { className: "work-cart-count", text: "0" });
  const heading = element("h2", { id: "work-cart-title", text: "Live work cart" });
  const header = element("header", { className: "work-cart-heading" }, [
    iconNode("desk", "desk-section-icon"),
    heading,
    count,
  ]);
  const host = element("aside", { className: "live-work-cart", "aria-labelledby": "work-cart-title" }, [header, empty, list]);

  function onRefresh(snapshot) {
    const items = buildWorkCartItems(snapshot.jobs, snapshot.collections);
    count.textContent = String(items.length);
    count.setAttribute("aria-label", `${items.length} ${items.length === 1 ? "collection" : "collections"} in the work cart`);
    empty.hidden = items.length > 0;
    list.hidden = items.length === 0;
    withFocusRestored(() => {
      replace(list, ...items.map((item) => workCartRow(item, { request, requestRefresh, navigate })));
    }, { root: host });
  }

  return { host, onRefresh };
}

function animateSubmission(rows, root, cart) {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const target = cart.getBoundingClientRect();
  rows.slice(0, 8).forEach((row, index) => {
    const source = row.getBoundingClientRect();
    const slip = element("span", { className: "accepted-source-slip", text: row.querySelector("input")?.value || "Story accepted" });
    slip.style.setProperty("--slip-left", `${source.left}px`);
    slip.style.setProperty("--slip-top", `${source.top}px`);
    slip.style.setProperty("--slip-width", `${Math.min(source.width, 420)}px`);
    slip.style.setProperty("--slip-x", `${target.left - source.left + 24}px`);
    slip.style.setProperty("--slip-y", `${target.top - source.top + 72 + (index * 8)}px`);
    slip.style.setProperty("--slip-delay", `${index * 45}ms`);
    root.append(slip);
    slip.addEventListener("animationend", () => slip.remove(), { once: true });
    window.setTimeout(() => slip.remove(), 1000);
  });
}

function createSecondaryIntake({ root, request, requestRefresh, signal }) {
  const heading = element("h2", { id: "secondary-intake-title", text: "Other ways to add stories" });
  const intro = element("p", { className: "secondary-intake-copy", text: "Public-domain LibriVox books and your own audio files use the same Forge defaults, then stop at Review Shelf." });

  const searchInput = element("input", {
    id: "librivox-query",
    type: "search",
    name: "query",
    placeholder: "Search by title",
    autocomplete: "off",
  });
  const searchButton = element("button", { type: "submit", className: "button button-secondary" }, [
    iconNode("search"), element("span", { text: "Search LibriVox" }),
  ]);
  const searchResults = element("div", { className: "librivox-results", "aria-live": "polite" });
  const searchForm = element("form", { className: "librivox-search" }, [
    element("label", { for: "librivox-query", text: "Find a public-domain audiobook" }),
    element("div", { className: "secondary-inline-form" }, [searchInput, searchButton]),
    searchResults,
  ]);
  searchForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = searchInput.value.trim();
    if (!query) {
      searchInput.setCustomValidity("Enter a book title to search.");
      searchInput.reportValidity();
      return;
    }
    searchInput.setCustomValidity("");
    setBusy(searchResults, true, "Searching LibriVox");
    replace(searchResults, element("p", { className: "field-help", text: "Searching LibriVox..." }));
    try {
      const books = await request(`/api/librivox/search?q=${encodeURIComponent(query)}`);
      if (signal.aborted) return;
      if (!books.length) {
        replace(searchResults, element("p", { className: "empty-state", text: "No books matched. Try fewer words from the beginning of the title." }));
        return;
      }
      const list = element("ul", { className: "librivox-result-list" });
      books.forEach((book) => {
        const importButton = element("button", { type: "button", className: "button button-secondary", text: "Import and prepare" });
        importButton.addEventListener("click", async () => {
          importButton.disabled = true;
          try {
            await request("/api/librivox/import", {
              method: "POST",
              body: JSON.stringify({ book_id: String(book.id) }),
            });
            notify(`${book.title} is in the work cart.`, { kind: "success" });
            announce(`${book.title} is queued for extraction and Forge.`);
            await requestRefresh();
          } catch (error) {
            importButton.disabled = false;
            notify(error.message, { kind: "failure", timeout: 0 });
          }
        });
        const facts = [book.authors, `${book.num_sections} sections`, book.total_duration].filter(Boolean).join(" · ");
        list.append(element("li", {}, [
          element("div", {}, [element("strong", { text: book.title }), element("small", { text: facts })]),
          importButton,
        ]));
      });
      replace(searchResults, list);
    } catch (error) {
      if (!signal.aborted) replace(searchResults, element("p", { className: "inline-error", role: "alert", text: error.message }));
    } finally {
      setBusy(searchResults, false);
    }
  });

  const uploadTitle = element("input", { id: "upload-title", name: "title", placeholder: "Collection title, optional" });
  const fileInput = element("input", {
    id: "upload-files",
    type: "file",
    name: "files",
    multiple: true,
    accept: "audio/*,.mp3,.m4a,.m4b,.ogg,.opus,.wav,.flac,.aac",
  });
  const uploadStatus = element("p", { className: "field-help", role: "status", text: "All selected files become one collection in the order shown by your device." });
  const uploadButton = element("button", { type: "submit", className: "button button-secondary" }, [
    iconNode("upload"), element("span", { text: "Upload and prepare" }),
  ]);
  const uploadForm = element("form", { className: "local-upload-form" }, [
    element("label", { for: "upload-title", text: "Collection title" }),
    uploadTitle,
    element("label", { for: "upload-files", text: "Audio files" }),
    fileInput,
    uploadStatus,
    uploadButton,
  ]);
  uploadForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const files = Array.from(fileInput.files || []);
    if (!files.length) {
      fileInput.setCustomValidity("Choose one or more audio files.");
      fileInput.reportValidity();
      return;
    }
    fileInput.setCustomValidity("");
    uploadButton.disabled = true;
    let slug = "";
    try {
      for (let index = 0; index < files.length; index += 1) {
        uploadStatus.textContent = `Uploading file ${index + 1} of ${files.length}: ${files[index].name}`;
        const form = new FormData();
        form.append("file", files[index]);
        if (slug) form.append("slug", slug);
        else if (uploadTitle.value.trim()) form.append("title", uploadTitle.value.trim());
        const collection = await request("/api/ingest/upload", { method: "POST", body: form });
        slug = collection.slug;
      }
      uploadStatus.textContent = "Upload complete. Queuing Forge cleanup...";
      await request("/api/forge", {
        method: "POST",
        body: JSON.stringify(buildForgePayload(slug, readForgeOptions(root))),
      });
      fileInput.value = "";
      uploadTitle.value = "";
      uploadStatus.textContent = `${files.length} ${files.length === 1 ? "file" : "files"} uploaded. Forge is queued.`;
      notify("Your uploaded collection is in the work cart.", { kind: "success" });
      await requestRefresh();
    } catch (error) {
      uploadStatus.textContent = error.message;
      notify(error.message, { kind: "failure", timeout: 0 });
    } finally {
      uploadButton.disabled = false;
    }
  });

  const librivox = element("details", { className: "secondary-intake-panel" }, [
    element("summary", {}, [iconNode("library"), element("span", { text: "Search LibriVox" })]),
    searchForm,
  ]);
  const uploads = element("details", { className: "secondary-intake-panel" }, [
    element("summary", {}, [iconNode("upload"), element("span", { text: "Upload audio files" })]),
    uploadForm,
  ]);
  return element("section", { className: "secondary-intake", "aria-labelledby": "secondary-intake-title" }, [
    heading,
    intro,
    element("div", { className: "secondary-intake-grid" }, [librivox, uploads]),
  ]);
}

export function createDeskScreen({
  request = api,
  refresh,
} = {}) {
  if (!refresh) throw new Error("Desk requires the application refresh coordinator.");

  return function renderDesk({ workspace, navigate, signal }) {
    let values = [];
    let submitting = false;
    const root = element("div", { className: "desk-screen" });
    const intake = element("section", { className: "desk-intake", "aria-labelledby": "desk-title" });
    const heading = element("h1", { id: "desk-title", text: "Prepare your next stories" });
    const lead = element("p", { className: "desk-lead", text: "Add up to 50 source links. Each story extracts and passes through Forge independently." });
    const pasteLabel = element("label", { for: "source-paste", text: "Paste source URLs, one per line" });
    const paste = element("textarea", {
      id: "source-paste",
      rows: "4",
      placeholder: "https://www.youtube.com/watch?v=...\nhttps://example.com/another-story",
      spellcheck: "false",
      autocapitalize: "off",
      autocomplete: "off",
    });
    const addSources = element("button", { type: "button", className: "button button-secondary" }, [
      iconNode("plus"), element("span", { text: "Add to tray" }),
    ]);
    const clearSources = element("button", { type: "button", className: "desk-clear-button", text: "Clear all", hidden: true });
    const sourceCount = element("span", { className: "source-count", text: "0 / 50" });
    const sourceList = element("ol", { className: "source-row-list", "aria-label": "Source URLs" });
    const validation = element("p", { className: "source-validation", role: "status", "aria-live": "polite", text: "Add at least one HTTP or HTTPS source URL." });
    const prepareButton = element("button", { type: "submit", className: "button button-primary desk-prepare-button", disabled: true }, [
      iconNode("forge"), element("span", { text: "Prepare stories" }),
    ]);
    const form = element("form", { className: "source-intake-form", novalidate: true });

    function moveSource(index, offset) {
      const target = index + offset;
      if (target < 0 || target >= values.length) return;
      [values[index], values[target]] = [values[target], values[index]];
      renderSources({ focusKey: `source-${target}` });
    }

    function sourceRow(row, index, total) {
      const inputId = `source-row-${index}`;
      const errorId = `${inputId}-error`;
      const input = element("input", {
        id: inputId,
        type: "url",
        value: row.value,
        "data-focus-key": `source-${index}`,
        "aria-invalid": row.error ? "true" : "false",
        "aria-describedby": row.error ? errorId : null,
        spellcheck: "false",
        autocapitalize: "off",
        autocomplete: "off",
      });
      input.addEventListener("change", () => {
        values[index] = input.value;
        renderSources({ focusKey: `source-${index}` });
      });
      const moveUp = element("button", {
        type: "button",
        className: "icon-button",
        "aria-label": `Move source ${index + 1} up`,
        disabled: index === 0,
      });
      moveUp.innerHTML = icon("arrowUp");
      moveUp.addEventListener("click", () => moveSource(index, -1));
      const moveDown = element("button", {
        type: "button",
        className: "icon-button",
        "aria-label": `Move source ${index + 1} down`,
        disabled: index === total - 1,
      });
      moveDown.innerHTML = icon("arrowDown");
      moveDown.addEventListener("click", () => moveSource(index, 1));
      const remove = element("button", {
        type: "button",
        className: "icon-button source-remove",
        "aria-label": `Remove source ${index + 1}`,
      });
      remove.innerHTML = icon("close");
      remove.addEventListener("click", () => {
        values.splice(index, 1);
        renderSources({ focusKey: `source-${Math.min(index, values.length - 1)}` });
      });
      const controls = element("span", { className: "source-row-controls" }, [moveUp, moveDown, remove]);
      const field = element("div", { className: "source-row-field" }, [input]);
      if (row.error) field.append(element("span", { id: errorId, className: "inline-error", text: row.error }));
      return element("li", { className: "source-row", "data-invalid": Boolean(row.error) }, [
        iconNode("link", "source-row-icon"),
        field,
        controls,
      ]);
    }

    function renderSources({ focusKey = "" } = {}) {
      const parsed = parseSourceLines(values);
      values = parsed.rows.map((row) => row.value);
      sourceCount.textContent = `${parsed.uniqueCount} / ${MAX_SOURCES}`;
      clearSources.hidden = parsed.rows.length === 0;
      const errorCount = parsed.rows.filter((row) => row.error).length;
      validation.dataset.kind = errorCount ? "failure" : parsed.valid ? "success" : "empty";
      validation.textContent = errorCount
        ? `${errorCount} ${errorCount === 1 ? "source needs" : "sources need"} attention. Nothing will be submitted yet.`
        : parsed.valid
          ? `${parsed.uniqueCount} ${parsed.uniqueCount === 1 ? "story is" : "stories are"} ready to prepare.`
          : "Add at least one HTTP or HTTPS source URL.";
      prepareButton.disabled = !parsed.valid || submitting;
      const label = parsed.uniqueCount === 1 ? "Prepare 1 story" : `Prepare ${parsed.uniqueCount} stories`;
      prepareButton.querySelector("span:last-child").textContent = label;
      withFocusRestored(() => {
        replace(sourceList, ...parsed.rows.map((row, index) => sourceRow(row, index, parsed.rows.length)));
      }, {
        root: intake,
        fallback: focusKey ? sourceList.querySelector(`[data-focus-key="${focusKey}"]`) : null,
      });
    }

    addSources.addEventListener("click", () => {
      const added = sourceValues(paste.value);
      if (!added.length) {
        paste.setCustomValidity("Paste at least one source URL.");
        paste.reportValidity();
        return;
      }
      paste.setCustomValidity("");
      values.push(...added);
      paste.value = "";
      renderSources({ focusKey: `source-${values.length - added.length}` });
    });
    clearSources.addEventListener("click", () => {
      values = [];
      renderSources();
      paste.focus();
    });

    const sourceHeading = element("div", { className: "source-list-heading" }, [
      element("div", {}, [iconNode("link", "desk-section-icon"), element("h2", { text: "Add sources" })]),
      element("div", {}, [sourceCount, clearSources]),
    ]);
    const pasteControls = element("div", { className: "source-paste-controls" }, [paste, addSources]);
    const forgeSummary = createForgeSummary();
    const actionNote = element("p", { className: "desk-action-note" }, [
      iconNode("info"),
      element("span", { text: "Preparation stops at Review Shelf. No Creative Tonie changes happen here." }),
    ]);
    form.append(sourceHeading, pasteLabel, pasteControls, sourceList, validation, forgeSummary, prepareButton, actionNote);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (submitting) return;
      let payload;
      try {
        payload = buildPreparePayload(values, readForgeOptions(root));
      } catch (error) {
        validation.dataset.kind = "failure";
        validation.textContent = error.message;
        sourceList.querySelector('[aria-invalid="true"]')?.focus();
        return;
      }
      submitting = true;
      prepareButton.disabled = true;
      const acceptedRows = Array.from(sourceList.children);
      try {
        await request("/api/prepare", { method: "POST", body: JSON.stringify(payload) });
        animateSubmission(acceptedRows, root, cart.host);
        const count = payload.sources.length;
        values = [];
        renderSources();
        notify(`${count} ${count === 1 ? "story is" : "stories are"} in the work cart.`, { kind: "success" });
        announce(`${count} ${count === 1 ? "story was" : "stories were"} accepted for preparation.`);
        await refresh.request();
      } catch (error) {
        notify(error.message, { kind: "failure", timeout: 0 });
        validation.dataset.kind = "failure";
        validation.textContent = error.message;
      } finally {
        submitting = false;
        renderSources();
      }
    });

    const secondary = createSecondaryIntake({ root, request, requestRefresh: () => refresh.request(), signal });
    intake.append(heading, lead, form, secondary);
    const cart = createLiveWorkCart({ request, requestRefresh: () => refresh.request(), navigate });
    root.append(intake, cart.host);
    replace(workspace, root);
    renderSources();

    const unsubscribe = refresh.subscribe(cart.onRefresh);
    cart.onRefresh(refresh.snapshot);
    return () => unsubscribe();
  };
}
