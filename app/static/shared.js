import { icon } from "./icons.js";

let confirmationDialogId = 0;

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

export function query(selector, root = document) {
  return root.querySelector(selector);
}

export function queryAll(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}

export function element(tagName, attributes = {}, children = []) {
  const node = document.createElement(tagName);
  for (const [name, value] of Object.entries(attributes)) {
    if (value == null || value === false) continue;
    if (name === "className") {
      node.className = value;
    } else if (name === "text") {
      node.textContent = value;
    } else if (name.startsWith("on") && typeof value === "function") {
      node.addEventListener(name.slice(2).toLowerCase(), value);
    } else if (value === true) {
      node.setAttribute(name, "");
    } else {
      node.setAttribute(name, String(value));
    }
  }
  node.append(...(Array.isArray(children) ? children : [children]).filter((child) => child != null));
  return node;
}

export function replace(host, ...children) {
  host.replaceChildren(...children.flat().filter((child) => child != null));
  return host;
}

export function moveItem(items, index, offset) {
  const next = [...items];
  const target = index + offset;
  if (index < 0 || index >= next.length || target < 0 || target >= next.length) return next;
  const [item] = next.splice(index, 1);
  next.splice(target, 0, item);
  return next;
}

export function announce(message, { assertive = false } = {}) {
  const region = document.getElementById("liveRegion");
  if (!region) return;
  region.setAttribute("aria-live", assertive ? "assertive" : "polite");
  region.textContent = "";
  window.requestAnimationFrame(() => {
    region.textContent = String(message);
  });
}

export function notify(message, { kind = "info", timeout = 5000 } = {}) {
  const host = document.getElementById("notifications");
  if (!host) {
    announce(message, { assertive: kind === "failure" });
    return null;
  }

  const notice = element("div", {
    className: "notification",
    role: kind === "failure" ? "alert" : "status",
    "data-kind": kind,
  });
  const symbol = element("span", { className: "notification-icon", "aria-hidden": "true" });
  symbol.innerHTML = icon(kind === "failure" ? "alert" : kind === "success" ? "check" : "info");
  const copy = element("span", { className: "notification-copy", text: message });
  const close = element("button", {
    type: "button",
    className: "icon-button notification-close",
    "aria-label": "Dismiss notification",
    onclick: () => notice.remove(),
  });
  close.innerHTML = icon("close");
  notice.append(symbol, copy, close);
  host.append(notice);
  announce(message, { assertive: kind === "failure" });

  if (timeout > 0) window.setTimeout(() => notice.remove(), timeout);
  return notice;
}

export function rememberFocus(root = document) {
  const active = document.activeElement;
  if (!active || !root.contains(active)) return null;
  return {
    id: active.id || "",
    key: active.getAttribute("data-focus-key") || "",
    name: active.getAttribute("name") || "",
    selectionStart: typeof active.selectionStart === "number" ? active.selectionStart : null,
    selectionEnd: typeof active.selectionEnd === "number" ? active.selectionEnd : null,
  };
}

function matchingAttribute(root, name, value) {
  if (!value) return null;
  return queryAll(`[${name}]`, root).find((node) => node.getAttribute(name) === value) || null;
}

export function restoreFocus(token, { root = document, fallback = null } = {}) {
  let target = null;
  if (token?.id) target = document.getElementById(token.id);
  if (!target && token?.key) target = matchingAttribute(root, "data-focus-key", token.key);
  if (!target && token?.name) target = matchingAttribute(root, "name", token.name);
  if (!target) target = fallback;
  if (!target || typeof target.focus !== "function") return false;
  target.focus({ preventScroll: true });
  if (token?.selectionStart != null && typeof target.setSelectionRange === "function") {
    target.setSelectionRange(token.selectionStart, token.selectionEnd);
  }
  return true;
}

export function withFocusRestored(render, options = {}) {
  const token = rememberFocus(options.root || document);
  const result = render();
  restoreFocus(token, options);
  return result;
}

export function setBusy(host, busy, label = "Working") {
  host.setAttribute("aria-busy", String(Boolean(busy)));
  if (busy) host.setAttribute("aria-label", label);
  else host.removeAttribute("aria-label");
}

export function snapshotRefreshOutcome(snapshot, resource) {
  const stale = Boolean(snapshot?.stale?.includes(resource));
  return { stale, error: stale ? snapshot?.errors?.[resource] || null : null };
}

export function createMutationController({
  root,
  reload,
  onReloaded = () => {},
  onStale = () => {},
  signal = null,
}) {
  let pending = false;

  function controlsDisabled(disabled) {
    root.querySelectorAll("[data-collection-mutation]").forEach((control) => {
      if (control.tagName === "A") {
        control.setAttribute("aria-disabled", String(disabled));
        control.tabIndex = disabled ? -1 : 0;
      } else {
        control.disabled = disabled;
      }
    });
    root.querySelectorAll("[data-track-name]").forEach((row) => {
      row.draggable = !disabled;
    });
    setBusy(root, disabled, "Saving collection changes");
  }

  async function run(operation) {
    if (pending || signal?.aborted) return false;
    pending = true;
    controlsDisabled(true);
    try {
      const result = await operation();
      if (signal?.aborted) return false;
      return result;
    } catch (error) {
      if (signal?.aborted) return false;
      try {
        const truth = await reload();
        if (!signal?.aborted) onReloaded(truth);
      } catch (reloadError) {
        if (!signal?.aborted) onStale(reloadError);
      }
      throw error;
    } finally {
      pending = false;
      if (!signal?.aborted) controlsDisabled(false);
    }
  }

  return {
    run,
    sync: () => controlsDisabled(pending),
    get pending() { return pending; },
  };
}

export function createPersistentAudioPlayer({
  host,
  notifyFailure = (message) => notify(message, { kind: "failure", timeout: 0 }),
} = {}) {
  if (!host) throw new Error("The persistent audio player host is missing.");
  const label = element("strong", { id: "audioTrackLabel", text: "No chapter selected" });
  const dismiss = element("button", {
    type: "button",
    className: "button button-secondary audio-dismiss",
    "aria-label": "Dismiss chapter player",
  }, [element("span", { text: "Dismiss" })]);
  const heading = element("div", { className: "audio-player-heading" }, [
    element("div", {}, [element("span", { className: "audio-player-caption", text: "Now previewing" }), label]),
    dismiss,
  ]);
  const audio = element("audio", {
    id: "persistentAudioPlayer",
    controls: true,
    preload: "metadata",
    "aria-labelledby": "audioTrackLabel",
  });

  function reserveWorkspace(visible) {
    document.body?.classList.toggle("audio-player-visible", visible);
  }

  function dismissPlayer() {
    audio.pause();
    audio.currentTime = 0;
    audio.removeAttribute("src");
    audio.load();
    label.textContent = "No chapter selected";
    host.removeAttribute("aria-label");
    host.hidden = true;
    reserveWorkspace(false);
  }

  dismiss.addEventListener("click", dismissPlayer);
  replace(host, heading, audio);

  return {
    play({ src, label: trackLabel }) {
      host.hidden = false;
      reserveWorkspace(true);
      label.textContent = trackLabel;
      host.setAttribute("aria-label", `Audio player: ${trackLabel}`);
      audio.setAttribute("aria-label", `Chapter preview: ${trackLabel}`);
      audio.src = src;
      audio.setAttribute("src", src);
      const started = audio.play();
      if (started?.catch) {
        started.catch(() => notifyFailure("Playback could not start automatically. Use the audio player controls to try again."));
      }
    },
    dismiss: dismissPlayer,
  };
}

export function showConfirmDialog({
  title,
  message,
  confirmLabel,
  cancelLabel = "Cancel",
  destructive = false,
}) {
  const host = document.getElementById("dialogHost");
  if (!host) return Promise.resolve(false);

  return new Promise((resolve) => {
    confirmationDialogId += 1;
    const titleId = `confirmation-title-${confirmationDialogId}`;
    const dialog = element("dialog", {
      className: "confirmation-dialog",
      "aria-labelledby": titleId,
    });
    const heading = element("h2", { id: titleId, text: title });
    const copy = element("p", { text: message });
    const actions = element("div", { className: "dialog-actions" });
    const cancel = element("button", { type: "button", className: "button button-secondary", text: cancelLabel });
    const confirm = element("button", {
      type: "button",
      className: destructive ? "button button-danger" : "button button-primary",
      text: confirmLabel,
    });

    const finish = (answer) => {
      dialog.close();
      dialog.remove();
      resolve(answer);
    };
    cancel.addEventListener("click", () => finish(false));
    confirm.addEventListener("click", () => finish(true));
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      finish(false);
    });

    actions.append(cancel, confirm);
    dialog.append(heading, copy, actions);
    host.append(dialog);
    dialog.showModal();
    cancel.focus();
  });
}
