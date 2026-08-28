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
  withFocusRestored,
} from "./shared.js";

const MAX_SOURCES = 50;
const DESK_JOB_KINDS = new Set(["prepare_url", "librivox", "upload_prepare", "forge"]);

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

export function buildLibrivoxPayload(bookId, options = {}) {
  return { book_id: String(bookId), options: normalizedOptions(options) };
}

export function forgeDefinitionValues(options = {}) {
  const selected = normalizedOptions(options);
  const trims = [];
  if (selected.trim_head) trims.push(`${selected.trim_head} sec start`);
  if (selected.trim_tail) trims.push(`${selected.trim_tail} sec end`);
  return {
    loudness: selected.normalize ? "−16 LUFS, −1.5 dBTP ceiling" : "Off",
    titleCleanup: selected.clean_titles ? "On" : "Off",
    chapters: selected.use_chapters ? "Preserved" : "Ignored",
    oversized: selected.split_oversized ? "Split" : "Kept whole",
    trimming: trims.length ? trims.join(", ") : "Off",
  };
}

export function forgeProfileStatus(options = {}) {
  const selected = normalizedOptions(options);
  const safe = Object.entries(DEFAULT_FORGE_OPTIONS)
    .every(([name, value]) => selected[name] === value);
  return safe
    ? { label: "Safe maximum", status: "success" }
    : { label: "Custom settings", status: "warning" };
}

export function moveSourceEntries(entries, id, offset) {
  const next = [...entries];
  const index = next.findIndex((entry) => entry.id === id);
  const target = index + offset;
  if (index < 0 || target < 0 || target >= next.length) return next;
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export function removeSourceEntry(entries, id) {
  const index = entries.findIndex((entry) => entry.id === id);
  if (index < 0) return { entries: [...entries], nextFocusId: "" };
  const next = entries.filter((entry) => entry.id !== id);
  return {
    entries: next,
    nextFocusId: next[Math.min(index, next.length - 1)]?.id || "",
  };
}

export async function submitUploadBatch({ files, title, options, request }) {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  form.append("title", String(title || "").trim());
  form.append("options", JSON.stringify(normalizedOptions(options)));
  return request("/api/uploads/prepare", { method: "POST", body: form });
}

export function uploadPolicyText(status) {
  const files = Number(status?.upload_max_files || 0);
  const size = status?.upload_max_human;
  const retention = Number(status?.upload_stage_retention_seconds || 0);
  if (!files || !size || !retention) return "Upload limits are loading from TonieFi.";
  const hours = retention / 3600;
  const retentionLabel = Number.isInteger(hours) ? `${hours} hours` : `${retention} seconds`;
  return `Up to ${files} files and ${size} total become one collection. Failed uploads remain available for retry for ${retentionLabel}.`;
}

export function truthfulWorkProgress(job) {
  const value = Number(job?.progress_percent);
  if (Number.isFinite(value) && value >= 0 && value <= 100) {
    return { mode: "determinate", percent: value };
  }
  return { mode: "indeterminate", percent: null };
}

export function deskRefreshNotice(snapshot) {
  const stale = Array.isArray(snapshot?.stale) && snapshot.stale.some((name) => name === "jobs" || name === "collections");
  if (!stale) return { stale: false, label: "", message: "" };
  const messages = snapshot.stale
    .filter((name) => name === "jobs" || name === "collections")
    .map((name) => snapshot.errors?.[name]?.message)
    .filter(Boolean);
  return {
    stale: true,
    label: "Work cart may be out of date",
    message: `${messages.join(" ") || "Work cart data could not refresh."} Showing the last available information.`,
  };
}

export function staleRefreshAnnouncement(previousKey, notice) {
  const key = notice.stale ? `${notice.label}|${notice.message}` : "";
  if (key === previousKey) return { key, message: "" };
  if (!key) {
    return {
      key,
      message: previousKey ? "Work cart information is current again." : "",
    };
  }
  return { key, message: `${notice.label}. ${notice.message}` };
}

function jobSlug(job) {
  return job.result?.slug || job.payload?.slug || "";
}

function workPhase(job, collection) {
  if (job.status === "queued") return "queued";
  if (job.status === "running") {
    if (job.phase === "forging" || job.kind === "forge") return "forging";
    if (job.phase === "extracting" || job.kind === "librivox" || job.kind === "prepare_url") {
      return "extracting";
    }
    return "queued";
  }
  if (collection?.stage === "forged") return "ready";
  if (job.status === "failed") return "failed";
  return "queued";
}

function workRelevance(job, collection) {
  if (job.status === "queued" || job.status === "running") return "active";
  if (collection?.stage === "forged") return "collection";
  if (job.status === "failed" && job.retryable && collection?.stage === "extracted") {
    return "recovery";
  }
  return "history";
}

function sourceLabel(job, collection) {
  return job.payload?.url || collection?.url || collection?.source || job.label || "Local collection";
}

export function buildWorkCartItems(jobs, collections, limit = 7) {
  const collectionBySlug = new Map(collections.map((collection) => [collection.slug, collection]));
  const represented = new Set();
  const items = [];

  const active = jobs.filter((job) => job.status === "queued" || job.status === "running");
  const failed = jobs.filter((job) => job.status === "failed");
  const completed = jobs.filter((job) => job.status !== "queued" && job.status !== "running" && job.status !== "failed");

  for (const job of [...active, ...failed, ...completed]) {
    if (!DESK_JOB_KINDS.has(job.kind)) continue;
    if (job.status !== "queued" && job.status !== "running" && items.length >= limit) continue;
    const slug = jobSlug(job);
    if (slug && represented.has(slug)) continue;
    const collection = slug ? collectionBySlug.get(slug) : null;
    if (workRelevance(job, collection) === "collection") continue;
    if (job.status === "done" && collection?.stage !== "forged") continue;
    if (slug) represented.add(slug);
    const phase = workPhase(job, collection);
    const workProgress = truthfulWorkProgress(job);
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
      canRetry: Boolean(job.retryable),
      trackCount: Number(collection?.track_count) || 0,
      duration: collection?.total_duration || "",
      hasCover: Boolean(collection?.cover),
      progressMode: workProgress.mode,
      progressPercent: workProgress.percent,
    });
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
  const profileBadge = element("span", {
    className: "status-stamp",
    "data-status": "success",
    text: "Safe maximum",
  });
  const headingRow = element("div", { className: "desk-section-heading" }, [
    iconNode("forge", "desk-section-icon"),
    heading,
    profileBadge,
  ]);
  const definitionNodes = {};
  const definition = (key, label) => {
    const value = element("dd");
    definitionNodes[key] = value;
    return element("div", {}, [element("dt", { text: label }), value]);
  };
  const definitions = element("dl", { className: "forge-definition-list" }, [
    definition("loudness", "Loudness"),
    definition("titleCleanup", "Title cleanup"),
    definition("chapters", "Chapter markers"),
    definition("oversized", "Oversized tracks"),
    definition("trimming", "Automatic trimming"),
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
  const section = element("section", { className: "forge-summary", "aria-labelledby": "forge-summary-title" }, [
    headingRow,
    definitions,
    disclosure,
  ]);
  const updateDefinitions = () => {
    const options = readForgeOptions(section);
    const values = forgeDefinitionValues(options);
    Object.entries(values).forEach(([key, value]) => {
      definitionNodes[key].textContent = value;
    });
    const profile = forgeProfileStatus(options);
    profileBadge.textContent = profile.label;
    profileBadge.dataset.status = profile.status;
  };
  controls.addEventListener("input", updateDefinitions);
  controls.addEventListener("change", updateDefinitions);
  updateDefinitions();
  return section;
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

function workCartRow(item, { request, requestRefresh, navigate, signal }) {
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
    body.append(element("p", { className: "work-cart-error", text: item.error || "Preparation stopped before this collection was ready." }));
  } else {
    body.append(progress);
    if (item.phase !== "ready") {
      const progressAttributes = item.progressMode === "determinate"
        ? {
          role: "progressbar",
          "aria-label": `${item.title} preparation progress`,
          "aria-valuemin": "0",
          "aria-valuemax": "100",
          "aria-valuenow": String(item.progressPercent),
          style: `--work-progress:${item.progressPercent}%`,
        }
        : { "aria-label": `${details.label}, progress amount is not available` };
      body.append(element("span", {
        className: "work-cart-progress-track",
        "data-mode": item.progressMode,
        ...progressAttributes,
      }, [
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
      "data-focus-key": `${item.key}-primary`,
    }, [element("span", { text: "Review" }), iconNode("chevronRight")]));
  } else {
    const detailsLink = element("a", {
      className: "work-cart-link",
      href: "/activity",
      "data-route": "activity",
      "data-focus-key": `${item.key}-primary`,
    }, [element("span", { text: "View details" }), iconNode("chevronRight")]);
    actions.append(detailsLink);
  }
  if (item.canRetry) {
    const retry = element("button", {
      type: "button",
      className: "button button-danger work-cart-retry",
      "data-focus-key": `${item.key}-retry`,
    }, [
      iconNode("retry"),
      element("span", { text: "Retry" }),
    ]);
    retry.addEventListener("click", async () => {
      retry.disabled = true;
      try {
        await request(`/api/jobs/${item.jobId}/retry`, { method: "POST" });
        if (signal?.aborted) return;
        notify(`${item.title} is queued again.`, { kind: "success" });
        await requestRefresh();
      } catch (error) {
        if (signal?.aborted) return;
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

export function createLiveWorkCart({ request, requestRefresh, navigate, signal = null }) {
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
  const staleLabel = element("strong", { text: "Work cart may be out of date" });
  const staleMessage = element("span");
  const retryRefresh = element("button", {
    type: "button",
    className: "button button-secondary work-cart-refresh",
    text: "Retry refresh",
    "data-focus-key": "work-cart-refresh",
  });
  retryRefresh.addEventListener("click", async () => {
    retryRefresh.disabled = true;
    try {
      await requestRefresh();
    } finally {
      if (!signal?.aborted) retryRefresh.disabled = false;
    }
  });
  const staleNotice = element("div", { className: "work-cart-stale", hidden: true }, [
    iconNode("alert"),
    element("div", {}, [staleLabel, staleMessage]),
    retryRefresh,
  ]);
  const liveStatus = element("p", {
    className: "visually-hidden",
    role: "status",
    "aria-live": "polite",
    "aria-atomic": "true",
  });
  const host = element("aside", { className: "live-work-cart", "aria-labelledby": "work-cart-title" }, [
    header,
    staleNotice,
    empty,
    list,
    liveStatus,
  ]);
  let priorPhases = null;
  let priorStaleKey = "";

  function onRefresh(snapshot) {
    if (signal?.aborted) return;
    const items = buildWorkCartItems(snapshot.jobs, snapshot.collections);
    const notice = deskRefreshNotice(snapshot);
    const staleAnnouncement = staleRefreshAnnouncement(priorStaleKey, notice);
    priorStaleKey = staleAnnouncement.key;
    staleNotice.hidden = !notice.stale;
    staleLabel.textContent = notice.label;
    staleMessage.textContent = notice.message;
    count.textContent = String(items.length);
    count.setAttribute("aria-label", `${items.length} ${items.length === 1 ? "collection" : "collections"} in the work cart`);
    empty.hidden = items.length > 0;
    list.hidden = items.length === 0;
    withFocusRestored(() => {
      replace(list, ...items.map((item) => workCartRow(item, { request, requestRefresh, navigate, signal })));
    }, { root: host });
    const phases = new Map(items.map((item) => [item.key, item.phase]));
    const announcements = [];
    if (staleAnnouncement.message) announcements.push(staleAnnouncement.message);
    if (priorPhases) {
      const changed = items.filter((item) => priorPhases.get(item.key) && priorPhases.get(item.key) !== item.phase);
      if (changed.length) {
        announcements.push(changed.map((item) => `${item.title}: ${phaseDetails(item.phase).label}.`).join(" "));
      }
    }
    if (announcements.length) liveStatus.textContent = announcements.join(" ");
    priorPhases = phases;
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
              body: JSON.stringify(buildLibrivoxPayload(book.id, readForgeOptions(root))),
            });
            if (signal.aborted) return;
            notify(`${book.title} is in the work cart.`, { kind: "success" });
            announce(`${book.title} is queued for extraction and Forge.`);
            await requestRefresh();
          } catch (error) {
            if (signal.aborted) return;
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
      if (!signal.aborted) setBusy(searchResults, false);
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
  const uploadStatus = element("p", {
    className: "field-help",
    role: "status",
    text: "Upload limits are loading from TonieFi.",
  });
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
    try {
      uploadStatus.textContent = `Uploading ${files.length} ${files.length === 1 ? "file" : "files"} as one collection...`;
      await submitUploadBatch({
        files,
        title: uploadTitle.value,
        options: readForgeOptions(root),
        request,
      });
      if (signal.aborted) return;
      fileInput.value = "";
      uploadTitle.value = "";
      uploadStatus.textContent = `${files.length} ${files.length === 1 ? "file is" : "files are"} in the work cart.`;
      notify("Your uploaded collection is in the work cart.", { kind: "success" });
      await requestRefresh();
    } catch (error) {
      if (signal.aborted) return;
      uploadStatus.textContent = error.message;
      notify(error.message, { kind: "failure", timeout: 0 });
    } finally {
      if (!signal.aborted) uploadButton.disabled = false;
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
  const host = element("section", { className: "secondary-intake", "aria-labelledby": "secondary-intake-title" }, [
    heading,
    intro,
    element("div", { className: "secondary-intake-grid" }, [librivox, uploads]),
  ]);
  return {
    host,
    onRefresh(snapshot) {
      if (!signal?.aborted && !uploadButton.disabled) {
        uploadStatus.textContent = uploadPolicyText(snapshot.status);
      }
    },
  };
}

export function createDeskScreen({
  request = api,
  refresh,
} = {}) {
  if (!refresh) throw new Error("Desk requires the application refresh coordinator.");

  return function renderDesk({ workspace, navigate, signal }) {
    let entries = [];
    let sourceId = 0;
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

    function newSourceEntry(value) {
      sourceId += 1;
      return { id: `source-${sourceId}`, value };
    }

    function moveSource(id, offset) {
      entries = moveSourceEntries(entries, id, offset);
      renderSources({ focusKey: `${id}-input` });
    }

    function sourceRow(row, index, total) {
      const inputId = `source-row-${row.id}`;
      const errorId = `${inputId}-error`;
      const input = element("input", {
        id: inputId,
        type: "url",
        value: row.value,
        "data-focus-key": `${row.id}-input`,
        "aria-label": `Source URL ${index + 1}`,
        "aria-invalid": row.error ? "true" : "false",
        "aria-describedby": row.error ? errorId : null,
        spellcheck: "false",
        autocapitalize: "off",
        autocomplete: "off",
      });
      input.addEventListener("change", () => {
        if (!input.value.trim()) {
          const removed = removeSourceEntry(entries, row.id);
          entries = removed.entries;
          renderSources({ focusKey: removed.nextFocusId ? `${removed.nextFocusId}-input` : "" });
          return;
        }
        entries[index].value = input.value;
        renderSources({ focusKey: `${row.id}-input` });
      });
      const moveUp = element("button", {
        type: "button",
        className: "icon-button",
        "aria-label": `Move source ${index + 1} up`,
        "data-focus-key": `${row.id}-move-up`,
        disabled: index === 0,
      });
      moveUp.innerHTML = icon("arrowUp");
      moveUp.addEventListener("click", () => moveSource(row.id, -1));
      const moveDown = element("button", {
        type: "button",
        className: "icon-button",
        "aria-label": `Move source ${index + 1} down`,
        "data-focus-key": `${row.id}-move-down`,
        disabled: index === total - 1,
      });
      moveDown.innerHTML = icon("arrowDown");
      moveDown.addEventListener("click", () => moveSource(row.id, 1));
      const remove = element("button", {
        type: "button",
        className: "icon-button source-remove",
        "aria-label": `Remove source ${index + 1}`,
        "data-focus-key": `${row.id}-remove`,
      });
      remove.innerHTML = icon("close");
      remove.addEventListener("click", () => {
        const removed = removeSourceEntry(entries, row.id);
        entries = removed.entries;
        renderSources({ focusKey: removed.nextFocusId ? `${removed.nextFocusId}-input` : "" });
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
      const parsed = parseSourceLines(entries);
      entries = parsed.rows.map((row, index) => ({ id: entries[index].id, value: row.value }));
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
      const token = focusKey ? { key: focusKey } : rememberFocus(intake);
      replace(sourceList, ...parsed.rows.map((row, index) => sourceRow({ ...row, id: entries[index].id }, index, parsed.rows.length)));
      restoreFocus(token, { root: intake });
    }

    addSources.addEventListener("click", () => {
      const added = sourceValues(paste.value);
      if (!added.length) {
        paste.setCustomValidity("Paste at least one source URL.");
        paste.reportValidity();
        return;
      }
      paste.setCustomValidity("");
      entries.push(...added.map((value) => newSourceEntry(value)));
      paste.value = "";
      renderSources({ focusKey: `${entries[entries.length - added.length].id}-input` });
    });
    clearSources.addEventListener("click", () => {
      entries = [];
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
    form.append(sourceHeading, pasteLabel, pasteControls, validation, sourceList, prepareButton, forgeSummary, actionNote);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (submitting) return;
      let payload;
      try {
        payload = buildPreparePayload(entries, readForgeOptions(root));
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
        if (signal.aborted) return;
        animateSubmission(acceptedRows, root, cart.host);
        const count = payload.sources.length;
        entries = [];
        renderSources();
        notify(`${count} ${count === 1 ? "story is" : "stories are"} in the work cart.`, { kind: "success" });
        announce(`${count} ${count === 1 ? "story was" : "stories were"} accepted for preparation.`);
        await refresh.request();
      } catch (error) {
        if (signal.aborted) return;
        notify(error.message, { kind: "failure", timeout: 0 });
        validation.dataset.kind = "failure";
        validation.textContent = error.message;
      } finally {
        if (!signal.aborted) {
          submitting = false;
          renderSources();
        }
      }
    });

    const secondary = createSecondaryIntake({ root, request, requestRefresh: () => refresh.request(), signal });
    intake.append(heading, lead, form, secondary.host);
    const cart = createLiveWorkCart({ request, requestRefresh: () => refresh.request(), navigate, signal });
    root.append(intake, cart.host);
    replace(workspace, root);
    renderSources();

    const onRefresh = (snapshot) => {
      cart.onRefresh(snapshot);
      secondary.onRefresh(snapshot);
    };
    const unsubscribe = refresh.subscribe(onRefresh);
    cart.onRefresh(refresh.snapshot);
    secondary.onRefresh(refresh.snapshot);
    return () => unsubscribe();
  };
}
