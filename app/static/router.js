function normalizeRoutes(routes) {
  if (Array.isArray(routes)) {
    return routes.map((route) => ({ ...route }));
  }
  return Object.entries(routes).map(([name, path]) => ({ name, path }));
}

function splitPath(path) {
  return path.replace(/^\/+|\/+$/g, "").split("/").filter(Boolean);
}

function matchPath(pattern, pathname) {
  const expected = splitPath(pattern);
  const actual = splitPath(pathname);
  const params = {};

  if (expected.length === 0 && actual.length === 0) return params;
  for (let index = 0; index < expected.length; index += 1) {
    const segment = expected[index];
    const optional = segment.startsWith(":") && segment.endsWith("?");
    const parameter = segment.startsWith(":");
    const value = actual[index];
    if (value == null && optional) continue;
    if (value == null) return null;
    if (parameter) {
      params[segment.slice(1, optional ? -1 : undefined)] = decodeURIComponent(value);
    } else if (segment !== value) {
      return null;
    }
  }
  if (actual.length > expected.length) return null;
  return params;
}

function buildPath(pattern, params = {}) {
  const segments = splitPath(pattern).flatMap((segment) => {
    if (!segment.startsWith(":")) return [segment];
    const optional = segment.endsWith("?");
    const name = segment.slice(1, optional ? -1 : undefined);
    const value = params[name];
    if (value == null && optional) return [];
    if (value == null) throw new Error(`Missing route parameter: ${name}`);
    return [encodeURIComponent(String(value))];
  });
  return `/${segments.join("/")}`;
}

export function createRouter(routes, {
  workspace = document.getElementById("workspace"),
  onError = (error) => { throw error; },
} = {}) {
  const definitions = normalizeRoutes(routes);
  const renderers = new Map();
  let current = null;
  let cleanup = null;
  let activeController = null;
  let sequence = 0;
  let started = false;

  function cleanupFor(result) {
    if (typeof result === "function") return result;
    if (typeof result?.destroy === "function") return () => result.destroy();
    return null;
  }

  function dispose(disposable, route) {
    if (!disposable) return;
    try {
      disposable();
    } catch (error) {
      onError(error, route);
    }
  }

  function resolve(pathname = window.location.pathname) {
    for (const definition of definitions) {
      const params = matchPath(definition.path, pathname);
      if (params) return { ...definition, params };
    }
    const fallback = definitions[0];
    return fallback ? { ...fallback, params: {}, unmatched: true } : null;
  }

  function markNavigation(name) {
    document.querySelectorAll("[data-route]").forEach((control) => {
      const active = control.dataset.route === name;
      if (active) control.setAttribute("aria-current", "page");
      else control.removeAttribute("aria-current");
    });
  }

  async function render({ focus = false } = {}) {
    const route = resolve();
    if (!route || !workspace) return;
    const renderSequence = ++sequence;
    current = route;
    markNavigation(route.name);

    activeController?.abort();
    const controller = new AbortController();
    activeController = controller;
    dispose(cleanup, route);
    cleanup = null;

    const renderer = renderers.get(route.name);
    if (!renderer) {
      activeController = null;
      return;
    }
    const target = document.createElement("div");
    target.className = "route-stage";
    try {
      const result = await renderer({
        name: route.name,
        path: window.location.pathname,
        params: route.params,
        search: new URLSearchParams(window.location.search),
        signal: controller.signal,
        state: window.history.state,
        workspace: target,
        navigate,
      });
      const nextCleanup = cleanupFor(result);
      if (controller.signal.aborted || renderSequence !== sequence) {
        dispose(nextCleanup, route);
        return;
      }
      workspace.replaceChildren(target);
      cleanup = nextCleanup;
      if (focus) workspace.focus({ preventScroll: true });
      document.dispatchEvent(new CustomEvent("toniefi:routechange", { detail: route }));
    } catch (error) {
      if (!controller.signal.aborted && renderSequence === sequence) onError(error, route);
    }
  }

  function pathFor(name, params = {}) {
    const route = definitions.find((definition) => definition.name === name);
    if (!route) throw new Error(`Unknown route: ${name}`);
    return buildPath(route.path, params);
  }

  function navigate(nameOrPath, { params = {}, state = null, replace = false, focus = true } = {}) {
    const pathname = nameOrPath.startsWith("/") ? nameOrPath : pathFor(nameOrPath, params);
    const method = replace ? "replaceState" : "pushState";
    window.history[method](state, "", pathname);
    return render({ focus });
  }

  function register(name, renderer) {
    if (!definitions.some((definition) => definition.name === name)) {
      throw new Error(`Cannot register unknown route: ${name}`);
    }
    renderers.set(name, renderer);
    if (started && current?.name === name) render();
    return () => {
      if (renderers.get(name) === renderer) renderers.delete(name);
    };
  }

  function handleLink(event) {
    const link = event.target.closest("a[data-route]");
    if (!link || event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (link.target && link.target !== "_self") return;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin) return;
    event.preventDefault();
    navigate(`${url.pathname}${url.search}${url.hash}`);
  }

  function handlePopState() {
    render({ focus: true });
  }

  function start() {
    if (started) return;
    started = true;
    document.addEventListener("click", handleLink);
    window.addEventListener("popstate", handlePopState);
    const route = resolve();
    if (route?.unmatched) {
      window.history.replaceState(window.history.state, "", pathFor(route.name));
    }
    render();
  }

  function destroy() {
    document.removeEventListener("click", handleLink);
    window.removeEventListener("popstate", handlePopState);
    sequence += 1;
    activeController?.abort();
    activeController = null;
    dispose(cleanup, current);
    cleanup = null;
    started = false;
  }

  return {
    register,
    start,
    destroy,
    navigate,
    pathFor,
    refresh: () => render(),
    get current() { return current; },
  };
}
