/* Toniefi front end. No framework, no build step -- one file the server
   hands over as-is.

   The wizard is five steps: paste -> extract -> forge -> review -> send.
   Steps 2-5 all operate on one collection slug, so `state.slug` is the
   thread running through the whole flow. */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const STEPS = [
  { n: 1, label: "Paste" },
  { n: 2, label: "Extract" },
  { n: 3, label: "Forge" },
  { n: 4, label: "Review" },
  { n: 5, label: "Send" },
];

const state = {
  status: null,
  step: 1,
  reached: 1,      // furthest step unlocked, so you can click back and forth
  probe: null,     // what step 1 found
  slug: null,      // the collection steps 3-5 act on
  collection: null,
  tonies: [],
  toniesStale: false, // a push changed free space; refetch before reading it
  openTonie: null, // "<householdId>:<tonieId>" of the expanded Tonie, if any
  pollTimer: null,
};

/* One chapter write at a time. A whole-list PUT does not compose with
   another one, and a blur that fires `change` followed by the click that
   caused the blur hands out two of them. */
let savingTonie = false;

/* ------------------------------------------------------------- plumbing */

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) message = body.detail;
    } catch (_) { /* non-JSON error body */ }
    throw new Error(message);
  }
  return res.status === 204 ? null : res.json();
}

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), kind === "bad" ? 8000 : 4000);
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function busy(host, message) {
  host.innerHTML = message
    ? `<div class="progress-line"><span class="spinner"></span><span>${esc(message)}</span></div>`
    : "";
}

/* ----------------------------------------------------------------- tabs */

$$("nav button").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$("nav button").forEach((b) => b.classList.toggle("active", b === btn));
    $$("main section").forEach((s) => { s.hidden = s.id !== `tab-${btn.dataset.tab}`; });
    if (btn.dataset.tab === "tonies") loadTonies();
    if (btn.dataset.tab === "jobs") loadJobs();
    if (btn.dataset.tab === "library") loadCollections();
  });
});

function showTab(name) {
  const btn = $$("nav button").find((b) => b.dataset.tab === name);
  if (btn) btn.click();
}

/* -------------------------------------------------------------- stepper */

function renderStepper() {
  $("#stepper").innerHTML = STEPS.map((s, i) => {
    const cls = [
      s.n === state.step ? "active" : "",
      s.n < state.step ? "done" : "",
      s.n <= state.reached ? "clickable" : "",
    ].join(" ");
    return `${i ? '<span class="sep"></span>' : ""}
      <div class="step ${cls}" data-step-to="${s.n}">
        <span class="n">${s.n}</span><span>${s.label}</span>
      </div>`;
  }).join("");

  $("#stepper").querySelectorAll("[data-step-to]").forEach((el) => {
    const target = Number(el.dataset.stepTo);
    if (target <= state.reached) el.addEventListener("click", () => goto(target));
  });
}

function goto(step) {
  state.step = step;
  state.reached = Math.max(state.reached, step);
  $$(".step-panel").forEach((p) => { p.hidden = Number(p.dataset.step) !== step; });
  renderStepper();
  showTab("new");
  if (step === 4) renderReview();
  if (step === 5) renderSend();
}

document.addEventListener("click", (e) => {
  const target = e.target.closest("[data-goto]");
  if (target) goto(Number(target.dataset.goto));
});

/* --------------------------------------------------------------- status */

async function loadStatus() {
  state.status = await api("/api/status");
  const s = state.status;
  const missing = Object.entries(s.tools).filter(([, ok]) => !ok).map(([n]) => n);
  $("#statusLine").textContent =
    `${s.tonie_limit_human} per Tonie` + (missing.length ? ` - missing: ${missing.join(", ")}` : "");
  $("#credLine").textContent = s.credentials.configured
    ? `signed in as ${s.credentials.username}`
    : "no myTonies account configured";
  if (s.credentials.username) $("#credUser").value = s.credentials.username;
}

/* ================================================== step 1: paste ===== */

$("#probeBtn").addEventListener("click", probeUrl);
$("#pasteUrl").addEventListener("keydown", (e) => { if (e.key === "Enter") probeUrl(); });

async function probeUrl() {
  const url = $("#pasteUrl").value.trim();
  if (!url) return;
  const host = $("#probeResult");
  busy(host, "Reading that link...");
  try {
    const info = await api("/api/probe", { method: "POST", body: JSON.stringify({ url }) });
    state.probe = { ...info, url };
    const facts = [
      `<span class="badge">${esc(info.duration)}</span>`,
      `<span class="badge">${info.tonies_needed} Tonie${info.tonies_needed === 1 ? "" : "s"}</span>`,
    ];
    if (info.chapter_count) {
      facts.push(`<span class="badge good">${info.chapter_count} chapters</span>`);
    } else {
      facts.push(`<span class="badge warn">no chapter markers</span>`);
    }
    if (info.is_playlist) facts.push(`<span class="badge">playlist of ${info.item_count}</span>`);

    host.innerHTML = `
      <div class="preview">
        ${info.thumbnail ? `<img src="${esc(info.thumbnail)}" alt="">` : ""}
        <div class="body">
          <h4>${esc(info.title)}</h4>
          <div class="note">${esc(info.uploader)}</div>
          <div class="facts">${facts.join("")}</div>
          <div style="margin-top:14px">
            <button class="btn" id="toExtract">Continue</button>
          </div>
        </div>
      </div>`;
    $("#toExtract").addEventListener("click", () => {
      $("#extractTitle").value = info.title;
      $("#optChapters").checked = info.chapter_count > 0;
      renderExtractSummary();
      goto(2);
    });
  } catch (err) {
    host.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  }
}

/* ------------------------------------------------------------ librivox */

$("#lvSearch").addEventListener("click", searchLibrivox);
$("#lvQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") searchLibrivox(); });

async function searchLibrivox() {
  const q = $("#lvQuery").value.trim();
  if (!q) return;
  const host = $("#lvResults");
  busy(host, "Searching LibriVox...");
  try {
    const hits = await api(`/api/librivox/search?q=${encodeURIComponent(q)}`);
    if (!hits.length) {
      host.innerHTML = `<div class="empty">No matches. LibriVox matches from the start of
        the title, so try fewer words.</div>`;
      return;
    }
    host.innerHTML = hits.map((b) => `
      <div class="item" style="cursor:default">
        <div class="row tight">
          <span class="title grow">${esc(b.title)}</span>
          <span class="badge">${b.tonies_needed} Tonie${b.tonies_needed === 1 ? "" : "s"}</span>
          <button class="btn small" data-lv="${esc(b.id)}">Import</button>
        </div>
        <div class="meta">${esc(b.authors)} - ${b.num_sections} sections - ${esc(b.total_duration)}</div>
      </div>`).join("");

    host.querySelectorAll("[data-lv]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        const { job_id } = await api("/api/librivox/import", {
          method: "POST", body: JSON.stringify({ book_id: btn.dataset.lv }),
        });
        goto(2);
        busy($("#extractProgress"), "Downloading from LibriVox...");
        watchJob(job_id, {
          host: $("#extractProgress"),
          onDone: (job) => { state.slug = job.result.slug; goto(3); },
        });
      });
    });
  } catch (err) {
    host.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  }
}

/* -------------------------------------------------------------- upload */

$("#uploadGo").addEventListener("click", async () => {
  const files = Array.from($("#fileInput").files || []);
  if (!files.length) return toast("Pick at least one file", "bad");
  const title = $("#uploadTitle").value.trim();
  let slug = null;
  try {
    for (const file of files) {
      const form = new FormData();
      form.append("file", file);
      if (slug) form.append("slug", slug);
      else if (title) form.append("title", title);
      const result = await api("/api/ingest/upload", { method: "POST", body: form });
      slug = result.slug; // keep every file in one collection
    }
    $("#fileInput").value = "";
    state.slug = slug;
    toast(`Uploaded ${files.length} file(s)`, "good");
    goto(3);
  } catch (err) { toast(err.message, "bad"); }
});

/* ================================================ step 2: extract ===== */

function renderExtractSummary() {
  const info = state.probe;
  if (!info) { $("#extractSummary").innerHTML = ""; return; }
  $("#extractSummary").innerHTML = `
    <div class="note">${esc(info.title)} - ${esc(info.duration)}${
      info.chapter_count ? `, ${info.chapter_count} chapter markers` : ", no chapter markers"
    }</div>`;
}

$("#extractGo").addEventListener("click", async () => {
  if (!state.probe) return toast("Paste a link first", "bad");
  const host = $("#extractProgress");
  $("#extractGo").disabled = true;
  busy(host, "Queued...");
  try {
    const { job_id } = await api("/api/ingest/url", {
      method: "POST",
      body: JSON.stringify({
        url: state.probe.url,
        title: $("#extractTitle").value.trim() || null,
        use_chapters: $("#optChapters").checked,
      }),
    });
    watchJob(job_id, {
      host,
      onDone: (job) => {
        state.slug = job.result.slug;
        $("#extractGo").disabled = false;
        goto(3);
      },
      onFail: () => { $("#extractGo").disabled = false; },
    });
  } catch (err) {
    $("#extractGo").disabled = false;
    busy(host, "");
    toast(err.message, "bad");
  }
});

/* ================================================== step 3: forge ===== */

$("#forgeSkip").addEventListener("click", () => goto(4));

$("#forgeGo").addEventListener("click", async () => {
  if (!state.slug) return toast("Nothing extracted yet", "bad");
  const host = $("#forgeProgress");
  $("#forgeGo").disabled = true;
  busy(host, "Queued...");
  try {
    const { job_id } = await api("/api/forge", {
      method: "POST",
      body: JSON.stringify({
        slug: state.slug,
        normalize: $("#optNormalize").checked,
        clean_titles: $("#optClean").checked,
        split_oversized: $("#optSplit").checked,
        trim_head: Number($("#optTrimHead").value) || 0,
        trim_tail: Number($("#optTrimTail").value) || 0,
      }),
    });
    watchJob(job_id, {
      host,
      onDone: () => { $("#forgeGo").disabled = false; goto(4); },
      onFail: () => { $("#forgeGo").disabled = false; },
    });
  } catch (err) {
    $("#forgeGo").disabled = false;
    busy(host, "");
    toast(err.message, "bad");
  }
});

/* ================================================= step 4: review ===== */

async function renderReview() {
  if (!state.slug) return;
  const data = await api(`/api/collections/${encodeURIComponent(state.slug)}?refresh=true`);
  state.collection = data;

  $("#reviewTitle").value = data.title;
  const cover = $("#reviewCover");
  if (data.cover) {
    cover.src = `/api/collections/${encodeURIComponent(state.slug)}/cover`;
    cover.hidden = false;
  } else {
    cover.hidden = true;
  }

  const facts = [
    `<span class="badge">${data.track_count} chapters</span>`,
    `<span class="badge">${esc(data.total_duration)}</span>`,
    data.fits_one_tonie
      ? `<span class="badge good">fits one Tonie</span>`
      : `<span class="badge warn">${data.tonies_needed} Tonies</span>`,
  ];
  if (data.has_oversized) facts.push(`<span class="badge bad">has an over-long track</span>`);
  if (data.uploader) facts.push(`<span class="badge">${esc(data.uploader)}</span>`);
  $("#reviewFacts").innerHTML = facts.join("");

  renderPlanInto($("#reviewPlan"), data, false);
  renderTrackList(data);
}

function renderPlanInto(host, data, withButtons) {
  const limit = state.status.usable_limit_seconds;
  const groups = data.plan || [];
  if (!groups.length) {
    host.innerHTML = `<div class="empty">No audio in this collection yet.</div>`;
    return;
  }
  host.innerHTML = groups.map((g) => {
    const pct = Math.min(100, (g.seconds / limit) * 100);
    const over = g.seconds > limit;
    return `<div class="group">
      <div class="head">
        <span class="title grow">Tonie ${g.index}</span>
        <span class="badge ${over ? "bad" : "good"}">${esc(g.duration)}</span>
        ${withButtons ? `<button class="btn small" data-push="${g.index}">Send this one</button>` : ""}
      </div>
      <div style="padding:0 14px"><div class="bar"><i class="${over ? "over" : ""}"
        style="width:${pct}%"></i></div></div>
      <ol>${g.tracks.map((t) =>
        `<li>${esc(t.title)}<span class="d">${esc(t.duration)}</span></li>`).join("")}</ol>
    </div>`;
  }).join("");
}

function renderTrackList(data) {
  const host = $("#reviewTracks");
  host.innerHTML = data.tracks.map((t, i) => `
    <li draggable="true" data-name="${esc(t.name)}">
      <span class="grip">&#8942;&#8942;</span>
      <span class="idx">${i + 1}</span>
      <input class="tt" value="${esc(t.title)}">
      <span class="dur">${esc(t.duration)}${t.oversized ? " &#9888;" : ""}</span>
      <button class="btn ghost small" data-play="${esc(t.name)}">Play</button>
      <button class="btn danger small" data-del="${esc(t.name)}">Remove</button>
    </li>`).join("");

  wireDragAndDrop(host, () => persistOrder(host));

  host.querySelectorAll("input.tt").forEach((input) => {
    input.addEventListener("change", async () => {
      const name = input.closest("li").dataset.name;
      await api(`/api/collections/${encodeURIComponent(state.slug)}/tracks/${encodeURIComponent(name)}`,
        { method: "PATCH", body: JSON.stringify({ title: input.value }) });
      toast("Renamed", "good");
    });
  });

  host.querySelectorAll("[data-play]").forEach((btn) => {
    btn.addEventListener("click", () => play(btn.dataset.play));
  });

  host.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!window.confirm("Remove this chapter and delete its file?")) return;
      await api(`/api/collections/${encodeURIComponent(state.slug)}/tracks/${encodeURIComponent(btn.dataset.del)}`,
        { method: "DELETE" });
      renderReview();
    });
  });
}

function wireDragAndDrop(host, onDrop) {
  let dragged = null;
  /* `dragend` fires on every drag, including one cancelled with Escape,
     dropped outside the list, or dropped back onto itself. Only the `drop`
     handler knows an item actually moved, so it says so and `dragend` saves
     nothing otherwise. */
  let moved = false;
  host.querySelectorAll("li").forEach((li) => {
    li.addEventListener("dragstart", () => { dragged = li; moved = false; li.classList.add("dragging"); });
    li.addEventListener("dragend", () => {
      li.classList.remove("dragging");
      host.querySelectorAll("li").forEach((x) => x.classList.remove("over"));
      if (moved) onDrop();
    });
    li.addEventListener("dragover", (e) => {
      e.preventDefault();
      if (li !== dragged) li.classList.add("over");
    });
    li.addEventListener("dragleave", () => li.classList.remove("over"));
    li.addEventListener("drop", (e) => {
      e.preventDefault();
      li.classList.remove("over");
      if (!dragged || li === dragged) return;
      const items = Array.from(host.querySelectorAll("li"));
      const from = items.indexOf(dragged);
      const to = items.indexOf(li);
      host.insertBefore(dragged, from < to ? li.nextSibling : li);
      moved = true;
    });
  });
}

async function persistOrder(host) {
  const names = Array.from(host.querySelectorAll("li")).map((li) => li.dataset.name);
  const current = (state.collection?.tracks || []).map((t) => t.name);
  if (names.join(" ") === current.join(" ")) return; // nothing actually moved
  try {
    await api(`/api/collections/${encodeURIComponent(state.slug)}/reorder`, {
      method: "POST", body: JSON.stringify({ names }),
    });
    await renderReview();
    toast("Order saved", "good");
  } catch (err) { toast(err.message, "bad"); }
}

function play(name) {
  let player = $("#player");
  if (!player) {
    player = document.createElement("audio");
    player.id = "player";
    player.controls = true;
    player.style.cssText = "position:fixed;left:20px;bottom:20px;width:320px;z-index:50";
    document.body.appendChild(player);
  }
  player.src = `/api/collections/${encodeURIComponent(state.slug)}/tracks/${encodeURIComponent(name)}/audio`;
  player.play();
}

$("#reviewRename").addEventListener("click", async () => {
  await api(`/api/collections/${encodeURIComponent(state.slug)}`, {
    method: "PATCH", body: JSON.stringify({ title: $("#reviewTitle").value }),
  });
  toast("Renamed", "good");
});

/* =================================================== step 5: send ===== */

async function renderSend() {
  if (!state.collection) await renderReview();
  renderPlanInto($("#sendPlan"), state.collection, true);
  $("#sendPlan").querySelectorAll("[data-push]").forEach((btn) => {
    btn.addEventListener("click", () => choosePushTarget(Number(btn.dataset.push)));
  });
}

$("#sendRefresh").addEventListener("click", async () => {
  await loadTonies(); // fetches unconditionally, and clears the stale flag
  toast("Refreshed", "good");
});

async function choosePushTarget(groupIndex) {
  /* This prompt is the only place the free-space figures are read, so it is
     the place that has to pay for a finished push having moved them. */
  if (!state.tonies.length || state.toniesStale) {
    try {
      state.tonies = await api("/api/tonies");
      state.toniesStale = false;
    } catch (err) { return toast(err.message, "bad"); }
  }
  if (!state.tonies.length) return toast("No Creative Tonies on this account", "bad");

  const options = state.tonies
    .map((t, i) => `${i + 1}) ${t.name || "Creative Tonie"} - ${t.chapter_count} chapters, ${t.time_free} free`)
    .join("\n");
  const pick = window.prompt(
    `Send Tonie ${groupIndex} to which Creative Tonie?\n\n${options}\n\nEnter a number:`, "1");
  if (!pick) return;
  const tonie = state.tonies[Number(pick) - 1];
  if (!tonie) return toast("That was not one of the options", "bad");

  const replace = window.confirm(
    `Replace everything currently on "${tonie.name}"?\n\nOK = replace, Cancel = add to the end.`);

  const host = $("#sendProgress");
  busy(host, "Queued...");
  try {
    const { job_id } = await api("/api/push", {
      method: "POST",
      body: JSON.stringify({
        slug: state.slug,
        household_id: tonie.householdId,
        tonie_id: tonie.id,
        group_index: groupIndex,
        replace,
      }),
    });
    watchJob(job_id, {
      host,
      onDone: (job) => {
        host.innerHTML = `<div class="progress-line"><span class="badge good">done</span>
          <span>${esc(job.result.chapters)} chapters now on ${esc(job.result.tonie)}</span></div>`;
        /* The cached free space is now wrong. Flag it rather than emptying
           the list, which renderTonies would draw as a Tonie-less account. */
        state.toniesStale = true;
      },
    });
  } catch (err) {
    busy(host, "");
    toast(err.message, "bad");
  }
}

/* ------------------------------------------------------------- library */

async function loadCollections() {
  const items = await api("/api/collections");
  const host = $("#collections");
  if (!items.length) {
    host.innerHTML = `<div class="empty">Nothing here yet. Start on the <b>New</b> tab.</div>`;
    return;
  }
  host.innerHTML = items.map((c) => {
    const badge = c.fits_one_tonie
      ? `<span class="badge good">fits one Tonie</span>`
      : `<span class="badge warn">${c.tonies_needed} Tonies</span>`;
    return `<div class="item" data-slug="${esc(c.slug)}">
      <div class="row tight"><span class="title grow">${esc(c.title)}</span>${badge}</div>
      <div class="meta">${c.track_count} chapters - ${esc(c.total_duration)}${
        c.source ? ` - ${esc(c.source)}` : ""}${c.stage ? ` - ${esc(c.stage)}` : ""}</div>
    </div>`;
  }).join("");

  host.querySelectorAll(".item").forEach((el) => {
    el.addEventListener("click", () => {
      state.slug = el.dataset.slug;
      state.reached = 5;
      goto(4);
    });
  });
}

$("#refreshLibrary").addEventListener("click", async () => {
  const items = await api("/api/collections");
  for (const c of items) {
    await api(`/api/collections/${encodeURIComponent(c.slug)}?refresh=true`);
  }
  await loadCollections();
  toast("Library rescanned", "good");
});

/* -------------------------------------------------------------- tonies */

const tonieKey = (t) => `${t.householdId}:${t.id}`;

async function loadTonies() {
  const host = $("#tonieList");
  busy(host, "Loading...");
  try {
    state.tonies = await api("/api/tonies");
    state.toniesStale = false;
  } catch (err) {
    host.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    return;
  }
  if (!state.tonies.length) {
    host.innerHTML = `<div class="empty">No Creative Tonies found on this account.</div>`;
    return;
  }
  renderTonies();
}

function renderTonies() {
  const host = $("#tonieList");
  host.innerHTML = state.tonies.map((t) => {
    const open = state.openTonie === tonieKey(t);
    return `
    <div class="item tonie" data-key="${esc(tonieKey(t))}">
      <div class="tonie-head">
        <div class="row tight">
          <span class="caret">${open ? "&#9662;" : "&#9656;"}</span>
          <span class="title grow">${esc(t.name || "Creative Tonie")}</span>
          <span class="badge">${t.chapter_count} chapters - ${esc(t.time_free)} free</span>
        </div>
        <div class="meta">${esc(t.householdName || "")}</div>
      </div>
      ${open ? tonieBody(t) : ""}
    </div>`;
  }).join("");

  wireTonies();
}

/* The panel's enabled state is a pure function of `savingTonie`, re-derived
   on every render by wireTonies. `savingTonie` is module state while
   `.saving` and `disabled` live on DOM nodes, so stamping them once when a
   save starts loses them the moment anything re-renders the list: the header
   toggle, Refresh, or the My Tonies nav button, none of which the lock
   disables. Every render of a panel runs through wireTonies, so this is the
   only place the lock is ever applied. */
function lockTonieBody() {
  const body = $("#tonieList .tonie-body");
  if (!body) return;
  body.classList.add("saving");
  body.querySelectorAll("input, button").forEach((el) => { el.disabled = true; });
}

function tonieBody(t) {
  if (!t.chapters.length) {
    return `<div class="tonie-body"><div class="empty">Nothing on this Tonie yet.</div></div>`;
  }
  return `
    <div class="tonie-body">
      <div class="row tight tonie-actions">
        <span class="grow"></span>
        <button class="btn danger small" data-clear="1">Clear all</button>
      </div>
      <ul class="tracklist">
        ${t.chapters.map((c, i) => `
          <li draggable="true" data-id="${esc(c.id)}">
            <span class="grip">&#8942;&#8942;</span>
            <span class="idx">${i + 1}</span>
            <input class="tt" value="${esc(c.title)}">
            ${c.transcoding ? `<span class="badge warn">processing</span>` : ""}
            <span class="dur">${esc(c.duration)}</span>
            <button class="btn danger small" data-del="${esc(c.id)}">Remove</button>
          </li>`).join("")}
      </ul>
    </div>`;
}

function wireTonies() {
  const host = $("#tonieList");

  /* The toggle lives on .tonie-head, and .tonie-body is its SIBLING rather
     than its child, so a click on a title input, a grip or a button cannot
     bubble into the toggle and collapse the row mid-edit. */
  host.querySelectorAll(".tonie-head").forEach((head) => {
    head.addEventListener("click", () => {
      const key = head.closest(".tonie").dataset.key;
      state.openTonie = state.openTonie === key ? null : key;
      renderTonies();
    });
  });

  // Re-derive the lock, so no re-render can hand back a live-looking panel.
  if (savingTonie) lockTonieBody();

  const open = state.tonies.find((t) => tonieKey(t) === state.openTonie);
  if (!open) return;
  const list = host.querySelector(".tonie-body ul.tracklist");
  if (!list) return;

  const fromDom = () => Array.from(list.querySelectorAll("li")).map((li) => ({
    id: li.dataset.id,
    title: li.querySelector("input.tt").value,
  }));

  wireDragAndDrop(list, () => saveChapters(open, fromDom()));

  list.querySelectorAll("input.tt").forEach((input) => {
    input.addEventListener("change", () => saveChapters(open, fromDom()));
  });

  list.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const chapter = open.chapters.find((c) => c.id === btn.dataset.del);
      if (!window.confirm(
        `Remove "${chapter.title}" from "${open.name || "this Tonie"}"?\n\n` +
        `This cannot be undone. Your library on disk is not touched.`)) return;
      saveChapters(open, fromDom().filter((c) => c.id !== btn.dataset.del));
    });
  });

  const clear = host.querySelector("[data-clear]");
  if (clear) {
    clear.addEventListener("click", () => {
      if (!window.confirm(
        `Clear all ${open.chapters.length} chapters from ` +
        `"${open.name || "this Tonie"}"?\n\n` +
        `This cannot be undone. Your library on disk is not touched.`)) return;
      saveChapters(open, []);
    });
  }
}

async function saveChapters(tonie, chapters) {
  // Dropped, never queued: two whole-list writes do not compose. Say so out
  // loud, because the dropped action may have been an accepted Remove.
  if (savingTonie) {
    toast("A save is already running, try again in a moment", "bad");
    return;
  }

  const url = `/api/tonies/${encodeURIComponent(tonie.householdId)}`
    + `/${encodeURIComponent(tonie.id)}/chapters`;
  let updated = null;
  let failure = null;
  try {
    savingTonie = true;
    lockTonieBody();
    updated = await api(url, {
      method: "PUT",
      body: JSON.stringify({
        // Titles travel with the ids: the server refuses the write if a
        // chapter was renamed elsewhere since this list was drawn.
        base: tonie.chapters.map((c) => ({ id: c.id, title: c.title })),
        chapters,
      }),
    });
  } catch (err) {
    failure = err;
  } finally {
    savingTonie = false;
  }

  /* Both redraws below sit outside the lock on purpose. A render while
     `savingTonie` is still true would correctly come back disabled, and
     nothing after the `finally` would re-enable it. */
  if (failure) {
    toast(failure.message, "bad");
    await loadTonies(); // redraw from the truth, never from what we hoped
    return;
  }
  // No fix-ups here. The response is already a full /api/tonies entry,
  // household fields included, because the server stamps them.
  state.tonies = state.tonies.map((t) => (tonieKey(t) === tonieKey(tonie) ? updated : t));
  renderTonies();
  toast("Saved to the Tonie", "good");
}

$("#refreshTonies").addEventListener("click", loadTonies);

/* ---------------------------------------------------------------- jobs */

async function loadJobs() {
  const items = await api("/api/jobs");
  const host = $("#jobList");
  if (!items.length) {
    host.innerHTML = `<div class="empty">Nothing has run yet.</div>`;
    return;
  }
  host.innerHTML = items.map((j) => {
    const cls = { done: "good", failed: "bad", running: "warn" }[j.status] || "";
    const detail = j.error || j.progress || "";
    return `<div class="item" style="cursor:default">
      <div class="row tight">
        <span class="title grow">${esc(j.label)}</span>
        <span class="badge ${cls}">${esc(j.status)}</span>
      </div>
      ${detail ? `<div class="meta">${esc(detail)}</div>` : ""}
    </div>`;
  }).join("");
}

function watchJob(jobId, { host, onDone, onFail } = {}) {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    let job;
    try { job = await api(`/api/jobs/${jobId}`); }
    catch (_) { return; }

    if (host && job.status === "running") busy(host, job.progress || "Working...");
    if (!$("#tab-jobs").hidden) loadJobs();

    if (job.status === "done") {
      clearInterval(state.pollTimer);
      if (host) busy(host, "");
      toast(`${job.label} - finished`, "good");
      if (onDone) onDone(job);
    } else if (job.status === "failed") {
      clearInterval(state.pollTimer);
      if (host) host.innerHTML = `<div class="empty">${esc(job.error)}</div>`;
      toast(`${job.label} - ${job.error}`, "bad");
      if (onFail) onFail(job);
    }
  }, 2000);
}

/* ------------------------------------------------------------ settings */

$("#credSave").addEventListener("click", async () => {
  try {
    await api("/api/settings/credentials", {
      method: "POST",
      body: JSON.stringify({ username: $("#credUser").value, password: $("#credPass").value }),
    });
    toast("Saved", "good");
    loadStatus();
  } catch (err) { toast(err.message, "bad"); }
});

$("#credTest").addEventListener("click", async () => {
  try {
    const result = await api("/api/settings/test", { method: "POST" });
    toast(`Signed in as ${result.email || "your account"}`, "good");
  } catch (err) { toast(err.message, "bad"); }
});

/* ---------------------------------------------------------------- boot */

(async function boot() {
  renderStepper();
  try {
    await loadStatus();
  } catch (err) { toast(err.message, "bad"); }
})();
