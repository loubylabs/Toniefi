import { api } from "./api.js";
import { icon } from "./icons.js";
import {
  announce,
  element,
  exactDuration,
  notify,
  replace,
  setBusy,
  showConfirmDialog,
  snapshotRefreshOutcome,
} from "./shared.js";


export function credentialView(credentials = {}, connectionResult = null) {
  const source = credentials.source || "none";
  const configured = Boolean(credentials.configured);
  const state = connectionResult?.state === "connected"
    ? "connected"
    : connectionResult?.state === "failed"
      ? "failed"
      : configured ? "configured" : "unconfigured";
  const label = state === "connected"
    ? "Connected"
    : state === "failed"
      ? "Connection failed"
      : configured ? "Configured" : "Unconfigured";
  return {
    state,
    label,
    sourceLabel: source === "environment"
      ? "Environment variables"
      : source === "saved"
        ? "Locally saved in SQLite"
        : "None",
    username: credentials.username || "",
    fieldsDisabled: source === "environment",
    saveLabel: source === "saved" ? "Replace local credentials" : "Save local credentials",
    explanation: source === "environment"
      ? configured
        ? "Environment credentials are active. Local values cannot override them."
        : "Environment credentials are incomplete. Set both TONIES_USERNAME and TONIES_PASSWORD before TonieFi can connect."
      : source === "saved"
        ? "These credentials are stored as plain text in TonieFi's local SQLite database."
        : "Add a myTonies username and password to manage Creative Tonies.",
  };
}


export function toolPresentation(tool) {
  return tool.available
    ? { icon: "check", label: "Available", state: "available" }
    : { icon: "alert", label: "Missing", state: "missing" };
}


export function settingsFacts(status = {}) {
  const limit = Number(status.tonie_limit_seconds || 0);
  const usable = Number(status.usable_limit_seconds || 0);
  return {
    version: status.version || "Unavailable",
    build: status.build || "Unavailable",
    limit: status.tonie_limit_human || exactDuration(limit),
    usable: exactDuration(usable),
    headroom: exactDuration(Math.max(0, limit - usable)),
    libraryPath: status.library_dir || "Unavailable",
    tools: Object.entries(status.tools || {}).map(([name, available]) => ({
      name,
      available: Boolean(available),
    })),
  };
}


function iconNode(name, className = "") {
  const node = element("span", { className, "aria-hidden": "true" });
  node.innerHTML = icon(name);
  return node;
}


export function createSettingsScreen({ request = api, refresh } = {}) {
  if (!refresh) throw new Error("Settings requires the application refresh coordinator.");

  return function renderSettings({ workspace, signal }) {
    let active = true;
    let status = refresh.snapshot.status;
    let pending = false;
    let connectionResult = null;
    const root = element("section", { className: "settings-screen", "aria-labelledby": "settings-title" });
    const header = element("div", { className: "screen-heading" }, [
      element("div", {}, [
        element("h1", { id: "settings-title", text: "Settings" }),
        element("p", { text: "Connect one household account and inspect the local system TonieFi depends on." }),
      ]),
    ]);
    const stale = element("div", { className: "stale-notice", role: "status", hidden: true });
    const account = element("section", { className: "settings-section account-settings", "aria-labelledby": "account-settings-title" });
    const system = element("section", { className: "settings-section system-settings", "aria-labelledby": "system-settings-title" });
    const disclosures = element("section", { className: "settings-section disclosure-settings", "aria-labelledby": "disclosure-settings-title" });

    const username = element("input", {
      id: "settings-username",
      name: "username",
      type: "email",
      autocomplete: "username",
      inputmode: "email",
    });
    const password = element("input", {
      id: "settings-password",
      name: "password",
      type: "password",
      autocomplete: "current-password",
    });
    const save = element("button", { type: "submit", className: "button button-primary" }, [
      iconNode("database"), element("span", { text: "Save local credentials" }),
    ]);
    const testConnection = element("button", { type: "button", className: "button button-secondary" }, [
      iconNode("cloud"), element("span", { text: "Test connection" }),
    ]);
    const removeSaved = element("button", { type: "button", className: "button button-secondary" }, [
      iconNode("trash"), element("span", { text: "Remove locally saved credentials" }),
    ]);
    const environmentNotice = element("p", { className: "settings-environment-notice", hidden: true });
    const formResult = element("p", { className: "settings-form-result", role: "status", "aria-live": "polite" });
    const form = element("form", { className: "credential-form" }, [
      element("div", { className: "settings-form-grid" }, [
        element("div", { className: "form-field" }, [
          element("label", { for: "settings-username", text: "myTonies username" }),
          username,
        ]),
        element("div", { className: "form-field" }, [
          element("label", { for: "settings-password", text: "myTonies password" }),
          password,
        ]),
      ]),
      environmentNotice,
      element("p", { className: "credential-warning" }, [
        iconNode("alert"),
        element("span", { text: "Credentials saved here are stored as plain text in the local SQLite database. Protect access to the TonieFi data directory." }),
      ]),
      element("p", { className: "credential-warning" }, [
        iconNode("alert"),
        element("span", { text: "The current private myTonies login method does not support accounts with two-factor authentication." }),
      ]),
      formResult,
      element("div", { className: "settings-form-actions" }, [save, testConnection, removeSaved]),
    ]);

    function setPending(next) {
      pending = next;
      for (const control of [username, password, save, testConnection, removeSaved]) control.disabled = pending;
      setBusy(account, pending, "Updating account settings");
    }

    async function refreshStatus() {
      const snapshot = await refresh.request();
      const outcome = snapshotRefreshOutcome(snapshot, "status");
      if (outcome.stale) throw outcome.error || new Error("Settings status could not refresh.");
      status = snapshot.status;
      render();
      return status;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (pending) return;
      const user = username.value.trim();
      const secret = password.value;
      if (!user || !secret) {
        formResult.dataset.state = "failed";
        formResult.textContent = "Enter both the myTonies username and password before saving.";
        return;
      }
      setPending(true);
      try {
        await request("/api/settings/credentials", {
          method: "POST",
          body: JSON.stringify({ username: user, password: secret }),
          ...(signal ? { signal } : {}),
        });
        connectionResult = null;
        password.value = "";
        await refreshStatus();
        formResult.dataset.state = "success";
        formResult.textContent = "Locally saved credentials updated. Use Test connection to verify them.";
        notify("Locally saved myTonies credentials updated.", { kind: "success" });
        announce("Locally saved credentials updated.");
      } catch (error) {
        if (!active || signal?.aborted) return;
        formResult.dataset.state = "failed";
        formResult.textContent = error.message;
        notify(error.message, { kind: "failure", timeout: 0 });
      } finally {
        if (active && !signal?.aborted) {
          setPending(false);
          render();
        }
      }
    });

    testConnection.addEventListener("click", async () => {
      if (pending) return;
      setPending(true);
      formResult.textContent = "Testing the current credential source with myTonies.";
      try {
        const result = await request("/api/settings/test", { method: "POST", ...(signal ? { signal } : {}) });
        if (!active || signal?.aborted) return;
        connectionResult = {
          state: "connected",
          message: `Connection succeeded as ${result.email || "the configured account"}.`,
          testedAt: new Date(),
        };
        announce("myTonies connection test succeeded.");
      } catch (error) {
        if (!active || signal?.aborted) return;
        connectionResult = { state: "failed", message: error.message, testedAt: new Date() };
        notify(error.message, { kind: "failure", timeout: 0 });
        announce("myTonies connection test failed.", { assertive: true });
      } finally {
        if (active && !signal?.aborted) {
          setPending(false);
          render();
          testConnection.focus({ preventScroll: true });
        }
      }
    });

    removeSaved.addEventListener("click", async () => {
      if (pending) return;
      const confirmed = await showConfirmDialog({
        title: "Remove locally saved credentials?",
        message: "The saved username and password will be removed from TonieFi's SQLite database. Environment credentials, if configured, remain active and cannot be removed here.",
        confirmLabel: "Remove saved credentials",
        destructive: true,
      });
      removeSaved.focus({ preventScroll: true });
      if (!confirmed) return;
      setPending(true);
      try {
        const credentials = await request("/api/settings/credentials", { method: "DELETE", ...(signal ? { signal } : {}) });
        password.value = "";
        status = { ...(status || {}), credentials };
        await refreshStatus();
        connectionResult = null;
        formResult.dataset.state = "success";
        formResult.textContent = credentials.source === "environment"
          ? "Local credentials removed. Environment credentials remain active."
          : "Locally saved credentials removed.";
        notify(formResult.textContent, { kind: "success" });
        announce(formResult.textContent);
      } catch (error) {
        if (!active || signal?.aborted) return;
        formResult.dataset.state = "failed";
        formResult.textContent = error.message;
        notify(error.message, { kind: "failure", timeout: 0 });
      } finally {
        if (active && !signal?.aborted) {
          setPending(false);
          render();
        }
      }
    });

    function renderAccount() {
      const view = credentialView(status?.credentials, connectionResult);
      const testState = view.state;
      const tested = connectionResult?.testedAt
        ? ` Tested ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(connectionResult.testedAt)}.`
        : "";
      const statusCopy = connectionResult?.message
        ? `${connectionResult.message}${tested}`
        : view.state === "configured"
          ? `Configured as ${view.username || "the current account"}. Test the connection to verify access in this browser session.`
          : "No complete myTonies credential pair is configured.";
      const statusBlock = element("div", { className: "account-connection-status", "data-state": testState }, [
        iconNode(testState === "failed" ? "alert" : testState === "connected" ? "check" : "account"),
        element("div", {}, [
          element("span", { className: "status-stamp", "data-status": testState, text: testState === "failed" ? "Connection failed" : view.label }),
          element("p", { text: statusCopy }),
        ]),
      ]);
      const sourceFacts = element("dl", { className: "settings-facts account-facts" }, [
        element("div", {}, [element("dt", { text: "Credential source" }), element("dd", { text: view.sourceLabel })]),
        element("div", {}, [element("dt", { text: "Configured username" }), element("dd", { text: view.username || "None" })]),
      ]);
      if (document.activeElement !== username) username.value = view.username;
      username.disabled = pending || view.fieldsDisabled;
      password.disabled = pending || view.fieldsDisabled;
      save.disabled = pending || view.fieldsDisabled;
      testConnection.disabled = pending || !status?.credentials?.configured;
      removeSaved.disabled = pending;
      save.querySelector("span:last-child").textContent = view.saveLabel;
      environmentNotice.hidden = !view.fieldsDisabled;
      environmentNotice.textContent = view.explanation;
      replace(account,
        element("div", { className: "settings-section-heading" }, [
          iconNode("account"),
          element("div", {}, [
            element("h2", { id: "account-settings-title", text: "myTonies account" }),
            element("p", { text: "Credentials are used only for Creative Tonie reads and confirmed cloud writes." }),
          ]),
        ]),
        statusBlock,
        sourceFacts,
        form,
      );
    }

    function renderSystem() {
      const facts = settingsFacts(status);
      const toolRows = facts.tools.map((tool) => {
        const presentation = toolPresentation(tool);
        return element("li", { "data-state": presentation.state }, [
          iconNode(presentation.icon),
          element("span", {}, [
            element("strong", { text: tool.name }),
            element("small", { text: presentation.label }),
          ]),
        ]);
      });
      replace(system,
        element("div", { className: "settings-section-heading" }, [
          iconNode("settings"),
          element("div", {}, [
            element("h2", { id: "system-settings-title", text: "Local system" }),
            element("p", { text: "Capacity, storage, and audio tools reported by this TonieFi process." }),
          ]),
        ]),
        element("dl", { className: "settings-facts system-facts" }, [
          element("div", {}, [element("dt", { text: "Version" }), element("dd", { text: facts.version })]),
          element("div", { className: "settings-code-fact" }, [element("dt", { text: "Build" }), element("dd", { text: facts.build })]),
          element("div", {}, [element("dt", { text: "Creative Tonie limit" }), element("dd", { text: facts.limit })]),
          element("div", {}, [element("dt", { text: "Usable audio" }), element("dd", { text: facts.usable })]),
          element("div", {}, [element("dt", { text: "Safety headroom" }), element("dd", { text: facts.headroom })]),
          element("div", { className: "settings-code-fact" }, [element("dt", { text: "Library path" }), element("dd", { text: facts.libraryPath })]),
        ]),
        element("div", { className: "tool-status" }, [
          element("h3", { text: "Required audio tools" }),
          element("ul", {}, toolRows.length ? toolRows : [element("li", { text: "Tool status unavailable." })]),
        ]),
      );
    }

    function renderDisclosures() {
      replace(disclosures,
        element("div", { className: "settings-section-heading" }, [
          iconNode("info"),
          element("div", {}, [
            element("h2", { id: "disclosure-settings-title", text: "Service disclosures" }),
            element("p", { text: "Important limits of the Creative Tonie connection." }),
          ]),
        ]),
        element("div", { className: "disclosure-list" }, [
          element("p", {}, [
            element("strong", { text: "Private API." }),
            element("span", { text: " TonieFi uses the same private, unsupported myTonies API used by the web app. Its endpoints can change without notice." }),
          ]),
          element("p", {}, [
            element("strong", { text: "No affiliation." }),
            element("span", { text: " TonieFi is not affiliated with, endorsed by, or supported by tonies or Boxine." }),
          ]),
        ]),
      );
    }

    function render() {
      if (!active || signal?.aborted) return;
      if (!status) {
        replace(account, element("div", { className: "route-pending settings-loading" }, [
          iconNode("settings", "route-pending-mark"),
          element("h2", { text: "Reading settings" }),
          element("p", { text: "Checking account source, limits, paths, and audio tools." }),
        ]));
        replace(system);
        return;
      }
      renderAccount();
      renderSystem();
      renderDisclosures();
      setBusy(account, pending, "Updating account settings");
    }

    function onRefresh(snapshot) {
      if (!active || signal?.aborted) return;
      const outcome = snapshotRefreshOutcome(snapshot, "status");
      stale.hidden = !outcome.stale;
      if (outcome.stale) {
        const retry = element("button", { type: "button", className: "button button-secondary", text: "Retry status" });
        retry.addEventListener("click", () => refresh.request());
        replace(stale,
          element("strong", { text: "Settings status may be stale" }),
          element("p", { text: "The last available account and system details remain visible." }),
          retry,
        );
      } else {
        replace(stale);
        status = snapshot.status;
      }
      render();
    }

    root.append(header, stale, account, system, disclosures);
    replace(workspace, root);
    renderDisclosures();
    render();
    const unsubscribe = refresh.subscribe(onRefresh);
    refresh.request().catch((error) => {
      if (active && !signal?.aborted) notify(error.message, { kind: "failure", timeout: 0 });
    });
    return () => {
      active = false;
      unsubscribe();
    };
  };
}
