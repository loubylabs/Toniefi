// The smallest DOM these browser tests need: enough of Element and Document for
// the screen modules to render, focus, and dispatch events under plain node
// --test, with no jsdom and no bundler. Shared by every DOM test file so there
// is one harness to keep honest rather than a copy per suite.

function textOf(node) {
  if (typeof node === "string") return node;
  return `${node._textContent || ""}${node.childNodes.map(textOf).join("")}`;
}

function matches(node, selector) {
  if (selector.endsWith(":last-child")) {
    const base = selector.slice(0, -":last-child".length);
    return matches(node, base) && node.parentNode?.childNodes.at(-1) === node;
  }
  if (selector.startsWith(".")) return node.className.split(/\s+/).includes(selector.slice(1));
  if (selector.startsWith("#")) return node.id === selector.slice(1);
  if (selector.startsWith("[") && selector.endsWith("]")) {
    return node.hasAttribute(selector.slice(1, -1));
  }
  return node.tagName === selector.toUpperCase();
}

class MiniElement {
  constructor(tagName, ownerDocument) {
    this.tagName = tagName.toUpperCase();
    // Every node holds this back-reference so it can reach the document, and
    // the document holds `body` right back, so the whole tree is reachable
    // from any single node. A failing assert.equal() on two nodes hands both
    // to util.inspect to build its diff, and util.inspect's cycle guard only
    // remembers nodes already open on the CURRENT path, not nodes visited
    // earlier anywhere else in the same call. Left enumerable, ownerDocument
    // gives that walk a way back into the whole tree from every node in it,
    // so the same shared subtrees get freshly re-walked over and over and
    // the process hangs. Non-enumerable keeps `el.ownerDocument` reading
    // exactly as before; it only stops enumeration (util.inspect's default
    // property walk, JSON.stringify, Object.keys, {...el}) from following
    // it, which is the one thing that was turning a failed comparison into
    // a hang instead of a message.
    Object.defineProperty(this, "ownerDocument", {
      value: ownerDocument,
      writable: true,
      enumerable: false,
      configurable: true,
    });
    this.attributes = new Map();
    this.childNodes = [];
    this.parentNode = null;
    this.listeners = new Map();
    this.className = "";
    this.id = "";
    this._textContent = "";
    this.dataset = {};
    this.disabled = false;
    this.hidden = false;
    this.checked = false;
    this.selected = false;
    this.value = "";
    this.name = "";
    this.type = "";
    this.tabIndex = 0;
    this.draggable = false;
  }

  // Live-DOM `children` skips text nodes; `childNodes` does not. The desk's
  // source tray reads `children` to index its rows, so a text child must not
  // shift those indexes.
  get children() {
    return this.childNodes.filter((child) => typeof child !== "string");
  }

  set textContent(value) {
    this._textContent = String(value ?? "");
    this.childNodes = [];
  }

  get textContent() {
    return textOf(this);
  }

  get classList() {
    return {
      add: (...names) => {
        const values = new Set(this.className.split(/\s+/).filter(Boolean));
        names.forEach((name) => values.add(name));
        this.className = [...values].join(" ");
      },
      remove: (...names) => {
        const removed = new Set(names);
        this.className = this.className.split(/\s+/).filter((name) => name && !removed.has(name)).join(" ");
      },
      contains: (name) => this.className.split(/\s+/).includes(name),
      toggle: (name, force) => {
        if (force) this.classList.add(name);
        else this.classList.remove(name);
      },
    };
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
    if (name === "value") this.value = String(value);
    if (name === "name") this.name = String(value);
    if (name === "type") this.type = String(value);
    if (name === "disabled") this.disabled = true;
    if (name === "checked") this.checked = true;
    if (name === "selected") this.selected = true;
    if (name === "draggable") this.draggable = String(value) === "true";
    if (name.startsWith("data-")) {
      const key = name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      this.dataset[key] = String(value);
    }
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  toggleAttribute(name, force) {
    if (force) this.setAttribute(name, "");
    else this.removeAttribute(name);
  }

  append(...children) {
    for (const child of children) {
      if (child && typeof child !== "string") child.parentNode = this;
      this.childNodes.push(child);
    }
  }

  insertBefore(node, reference) {
    if (node && typeof node !== "string") node.parentNode = this;
    const index = this.childNodes.indexOf(reference);
    if (index < 0) this.childNodes.push(node);
    else this.childNodes.splice(index, 0, node);
    return node;
  }

  prepend(...children) {
    for (const child of children) {
      if (child && typeof child !== "string") child.parentNode = this;
    }
    this.childNodes.unshift(...children);
  }

  replaceChildren(...children) {
    this.childNodes = [];
    this.append(...children);
  }

  remove() {
    if (!this.parentNode) return;
    this.parentNode.childNodes = this.parentNode.childNodes.filter((child) => child !== this);
    this.parentNode = null;
  }

  contains(target) {
    if (target === this) return true;
    return this.childNodes.some((child) => typeof child !== "string" && child.contains(target));
  }

  querySelectorAll(selector) {
    const selectors = selector.split(",").map((item) => item.trim());
    const found = [];
    const visit = (node) => {
      if (typeof node === "string") return;
      if (selectors.some((item) => matches(node, item))) found.push(node);
      node.childNodes.forEach(visit);
    };
    this.childNodes.forEach(visit);
    return found;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  addEventListener(name, listener) {
    const listeners = this.listeners.get(name) || [];
    listeners.push(listener);
    this.listeners.set(name, listeners);
  }

  removeEventListener(name, listener) {
    const listeners = this.listeners.get(name) || [];
    this.listeners.set(name, listeners.filter((item) => item !== listener));
  }

  async dispatchEvent(event) {
    event.target ||= this;
    event.currentTarget = this;
    event.preventDefault ||= () => { event.defaultPrevented = true; };
    const results = (this.listeners.get(event.type) || []).map((listener) => listener(event));
    await Promise.all(results);
    return !event.defaultPrevented;
  }

  click() {
    return this.dispatchEvent({ type: "click" });
  }

  focus() {
    // A disabled element cannot take focus in a real browser. Tests that
    // want to prove a disabled control stays inert must not be able to
    // fake success by focusing it anyway.
    if (this.disabled) return;
    this.ownerDocument.activeElement = this;
  }

  showModal() {
    this.setAttribute("open", "");
  }

  close() {
    this.removeAttribute("open");
  }

  setCustomValidity() {}
  reportValidity() {}
}

class MiniDocument {
  constructor() {
    this.body = new MiniElement("body", this);
    this.activeElement = null;
    this.hidden = false;
  }

  createElement(tagName) {
    return new MiniElement(tagName, this);
  }

  getElementById(id) {
    if (this.body.id === id) return this.body;
    return this.body.querySelectorAll(`#${id}`)[0] || null;
  }

  querySelectorAll(selector) {
    return this.body.querySelectorAll(selector);
  }
}

export function buttonWithText(root, text) {
  return root.querySelectorAll("button").find((button) => button.textContent.includes(text));
}

export function installDom() {
  const originalDocument = globalThis.document;
  const originalWindow = globalThis.window;
  const document = new MiniDocument();
  globalThis.document = document;
  const workspace = document.createElement("main");
  workspace.id = "workspace";
  const dialogs = document.createElement("div");
  dialogs.id = "dialogHost";
  // announce() writes through the live region on the next frame, so the region
  // has to exist for that code path to run at all, and `spoken` records what a
  // screen reader would have heard, in order.
  const liveRegion = document.createElement("div");
  liveRegion.id = "liveRegion";
  const spoken = [];
  globalThis.window = {
    requestAnimationFrame: (callback) => {
      callback();
      if (liveRegion.textContent) spoken.push(liveRegion.textContent);
    },
  };
  document.body.append(workspace, dialogs, liveRegion);
  return {
    document,
    workspace,
    liveRegion,
    spoken,
    restore() {
      globalThis.document = originalDocument;
      globalThis.window = originalWindow;
    },
  };
}

export async function flush() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}
