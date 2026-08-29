import { api } from "./api.js";
import { icon } from "./icons.js";
import {
  announce,
  element,
  notify,
  rememberFocus,
  replace,
  restoreFocus,
  snapshotRefreshOutcome,
} from "./shared.js";


const PREPARATION_KINDS = new Set(["prepare_url", "upload_prepare", "librivox", "forge"]);


function resultSlug(job) {
  const direct = job?.result?.slug || job?.payload?.slug || "";
  if (typeof direct === "string" && direct) return direct;
  // A push payload carries its collections under `sources`. One collection
  // resolves to a link; several have no single collection to point at.
  const slugs = new Set((job?.payload?.sources || []).map((source) => source?.slug).filter(Boolean));
  return slugs.size === 1 ? [...slugs][0] : "";
}


export function activityAction(job) {
  const slug = resultSlug(job);
  if (job.kind === "push" && job.status === "failed" && slug) {
    return {
      kind: "collection",
      href: `/collection/${encodeURIComponent(slug)}`,
      label: "Open collection",
      guidance: "Select the collection in the Library and send it again.",
    };
  }
  const completedPreparation = PREPARATION_KINDS.has(job.kind)
    && (job.kind !== "librivox" || job.collection_stage === "forged");
  if (job.status === "done" && completedPreparation && slug) {
    return {
      kind: "collection",
      href: `/collection/${encodeURIComponent(slug)}`,
      label: "Open collection",
      guidance: "",
    };
  }
  if (job.status === "failed" && job.retryable) {
    return {
      kind: "retry",
      href: "",
      label: "Retry",
      guidance: "Retry creates a new job. This failed attempt stays in Activity.",
    };
  }
  return { kind: "none", href: "", label: "", guidance: "" };
}


export function activityHistory(snapshot = {}) {
  return (snapshot.history || []).slice(0, 40);
}


export async function retryActivityJob(jobId, { request = api, refresh } = {}) {
  const created = await request(`/api/jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST" });
  await refresh.request();
  return created;
}


function iconNode(name, className = "") {
  const node = element("span", { className, "aria-hidden": "true" });
  node.innerHTML = icon(name);
  return node;
}


function phaseLabel(phase) {
  const labels = {
    queued: "Queued",
    extracting: "Extracting",
    forging: "Forging",
    ready: "Ready to send",
    running: "Running",
    failed: "Failed",
    done: "Finished",
  };
  return labels[phase] || String(phase || "Pending");
}


function kindLabel(kind) {
  const labels = {
    prepare_url: "URL preparation",
    upload_prepare: "File preparation",
    librivox: "LibriVox preparation",
    forge: "Forge",
    push: "Creative Tonie send",
  };
  return labels[kind] || String(kind || "Background job").replaceAll("_", " ");
}


function timestamp(job) {
  const seconds = Number(job.updated_at || job.created_at || 0);
  if (!seconds) return "Time unavailable";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(seconds * 1000));
}


export function activityFacts(job, formatTime = timestamp) {
  return [
    ["Type", kindLabel(job.kind)],
    ["Phase", phaseLabel(job.phase)],
    ["Status", phaseLabel(job.status)],
    ["Updated", formatTime(job)],
  ];
}


export function createActivityScreen({ request = api, refresh } = {}) {
  if (!refresh) throw new Error("Activity requires the application refresh coordinator.");

  return function renderActivity({ workspace, signal }) {
    let active = true;
    let jobs = activityHistory(refresh.snapshot);
    const retrying = new Set();
    const root = element("section", { className: "activity-screen", "aria-labelledby": "activity-title" });
    const refreshButton = element("button", {
      type: "button",
      className: "button button-secondary",
      "data-focus-key": "activity-refresh",
    }, [iconNode("refresh"), element("span", { text: "Refresh history" })]);
    const header = element("div", { className: "screen-heading" }, [
      element("div", {}, [
        element("h1", { id: "activity-title", text: "Activity" }),
        element("p", { text: "The 40 most recent background jobs stay visible, including failures after a retry." }),
      ]),
      refreshButton,
    ]);
    const stale = element("div", { className: "stale-notice", role: "status", hidden: true });
    const summary = element("p", { className: "activity-summary", role: "status", "aria-live": "polite" });
    const list = element("ol", { className: "activity-list" });

    function actionNode(job) {
      const action = activityAction(job);
      if (action.kind === "none") return null;
      const host = element("div", { className: "activity-action" });
      if (action.guidance) host.append(element("p", { text: action.guidance }));
      if (action.kind === "collection") {
        host.append(element("a", {
          className: "button button-primary",
          href: action.href,
          "data-route": "collection",
          "data-focus-key": `activity-${job.id}-collection`,
        }, [iconNode("library"), element("span", { text: action.label })]));
      } else {
        const retry = element("button", {
          type: "button",
          className: "button button-secondary",
          disabled: retrying.has(job.id),
          "data-focus-key": `activity-${job.id}-retry`,
        }, [iconNode("retry"), element("span", { text: retrying.has(job.id) ? "Queuing retry" : action.label })]);
        retry.addEventListener("click", async () => {
          if (retrying.has(job.id)) return;
          retrying.add(job.id);
          render({ focusKey: `activity-${job.id}-retry` });
          try {
            const created = await retryActivityJob(job.id, { request, refresh });
            if (!active || signal?.aborted) return;
            notify(`Retry job ${created.id} was added. The failed attempt remains below.`, { kind: "success" });
            announce(`Retry job ${created.id} queued.`);
          } catch (error) {
            if (active && !signal?.aborted) notify(error.message, { kind: "failure", timeout: 0 });
          } finally {
            retrying.delete(job.id);
            if (active && !signal?.aborted) render({ focusKey: `activity-${job.id}-retry` });
          }
        });
        host.append(retry);
      }
      return host;
    }

    function jobRow(job) {
      const titleId = `activity-job-${job.id}`;
      const phase = phaseLabel(job.phase);
      const facts = element("dl", { className: "activity-facts" }, activityFacts(job).map(([term, description]) => (
        element("div", {}, [element("dt", { text: term }), element("dd", { text: description })])
      )));
      const messages = element("div", { className: "activity-messages" });
      if (job.progress) messages.append(element("p", { className: "activity-progress", text: job.progress }));
      if (job.error) messages.append(element("p", { className: "activity-error", role: "alert", text: job.error }));
      if (!job.progress && !job.error) messages.append(element("p", { className: "activity-progress", text: "No additional progress detail." }));
      const action = actionNode(job);
      return element("li", { className: "activity-row", "aria-labelledby": titleId }, [
        element("div", { className: "activity-row-heading" }, [
          element("div", {}, [
            element("h2", { id: titleId, text: job.label || `Job ${job.id}` }),
            element("span", { className: "activity-job-id", text: `Job ${job.id}` }),
          ]),
          element("span", { className: "status-stamp", "data-status": job.phase || job.status, text: phase }),
        ]),
        facts,
        messages,
        action,
      ]);
    }

    function render({ focusKey = "" } = {}) {
      if (!active || signal?.aborted) return;
      const token = focusKey ? { key: focusKey } : rememberFocus(root);
      summary.textContent = jobs.length
        ? `${jobs.length} recent ${jobs.length === 1 ? "job" : "jobs"}`
        : "No job history";
      if (!jobs.length) {
        replace(list, element("li", { className: "empty-state activity-empty" }, [
          iconNode("activity"),
          element("strong", { text: "Nothing has run yet" }),
          element("p", { text: "Prepare a story on Desk. Its extraction, Forge, and send history will appear here." }),
          element("a", { href: "/", className: "button button-primary", "data-route": "desk" }, [
            iconNode("desk"), element("span", { text: "Go to Desk" }),
          ]),
        ]));
      } else {
        replace(list, ...jobs.map(jobRow));
      }
      restoreFocus(token, { root, fallback: refreshButton });
    }

    function onRefresh(snapshot) {
      if (!active || signal?.aborted) return;
      const outcome = snapshotRefreshOutcome(snapshot, "history");
      stale.hidden = !outcome.stale;
      if (outcome.stale) {
        const retry = element("button", { type: "button", className: "button button-secondary", text: "Retry refresh" });
        retry.addEventListener("click", () => refreshButton.click());
        replace(stale,
          element("strong", { text: "Activity may be out of date" }),
          element("p", { text: "The last available job history remains visible." }),
          retry,
        );
      } else {
        replace(stale);
        jobs = activityHistory(snapshot);
      }
      render();
    }

    refreshButton.addEventListener("click", async () => {
      refreshButton.disabled = true;
      try {
        const snapshot = await refresh.request();
        if (!active || signal?.aborted) return;
        const outcome = snapshotRefreshOutcome(snapshot, "history");
        if (outcome.stale) notify("Activity could not refresh. The last available history remains visible.", { kind: "failure", timeout: 0 });
        else notify("Activity history refreshed.", { kind: "success" });
      } catch (error) {
        if (active && !signal?.aborted) notify(error.message, { kind: "failure", timeout: 0 });
      } finally {
        if (active && !signal?.aborted) refreshButton.disabled = false;
      }
    });

    root.append(header, stale, summary, list);
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
