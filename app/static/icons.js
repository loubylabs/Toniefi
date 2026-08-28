const paths = {
  account: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18"/>',
  activity: '<path d="M3 12h3l2-6 4 12 3-9 2 3h4"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 17h.01"/>',
  arrowDown: '<path d="M12 5v14M6 13l6 6 6-6"/>',
  arrowUp: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  brand: '<path d="M7 4h10l2 3-2 3 2 3-2 3 2 3H5l2-3-2-3 2-3-2-3z"/><path d="M8 8h8M8 12h8M8 16h8"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  chevronRight: '<path d="m9 18 6-6-6-6"/>',
  clean: '<path d="M4 7h10M7 4v6M10 14h10M17 11v6M4 20h10M8 17v6"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  cloud: '<path d="M7 18h10a4 4 0 0 0 .7-7.94A6 6 0 0 0 6.2 9.2 4.5 4.5 0 0 0 7 18Z"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/>',
  desk: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  file: '<path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5M9 13h6M9 17h6"/>',
  forge: '<path d="M5 4h14M8 4v5l-4 4v7h16v-7l-4-4V4M7 13h10"/>',
  grip: '<circle cx="8" cy="6" r="1" fill="currentColor" stroke="none"/><circle cx="16" cy="6" r="1" fill="currentColor" stroke="none"/><circle cx="8" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="16" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="8" cy="18" r="1" fill="currentColor" stroke="none"/><circle cx="16" cy="18" r="1" fill="currentColor" stroke="none"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  library: '<path d="M3 5a4 4 0 0 1 4-2c2.5 0 5 2 5 4v14c0-2-2.5-4-5-4a4 4 0 0 0-4 2zM21 5a4 4 0 0 0-4-2c-2.5 0-5 2-5 4v14c0-2 2.5-4 5-4a4 4 0 0 1 4 2z"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l2-2a5 5 0 0 0-7.07-7.07l-1.15 1.15M14 11a5 5 0 0 0-7.54-.54l-2 2a5 5 0 0 0 7.07 7.07l1.15-1.15"/>',
  loudness: '<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 10v4"/>',
  more: '<circle cx="5" cy="12" r="1.25" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.25" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.25" fill="currentColor" stroke="none"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  play: '<path d="m8 5 11 7-11 7z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5M19 12a7 7 0 0 0-12-5L4 10M5 12a7 7 0 0 0 12 5l3-3"/>',
  retry: '<path d="M20 7v5h-5M20 12a8 8 0 1 1-2.34-5.66L20 8"/>',
  review: '<path d="M6 3h12v18l-6-4-6 4z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m16 16 5 5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1.1V21h-4v-.09A1.7 1.7 0 0 0 8.6 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1.1-.4H3v-4h.09A1.7 1.7 0 0 0 4.6 8.6a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .4-1.1V3h4v.09A1.7 1.7 0 0 0 15.4 4.6a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.4 9c.1.38.32.72.6 1 .3.3.7.4 1.1.4H21v4h-.09A1.7 1.7 0 0 0 19.4 15Z"/>',
  shield: '<path d="M12 3 20 6v6c0 5-3.4 8-8 9-4.6-1-8-4-8-9V6z"/><path d="m8 12 3 3 5-6"/>',
  split: '<path d="M8 4H4v4M4 4l6 6M16 20h4v-4M20 20l-6-6M16 4h4v4M20 4l-6 6M8 20H4v-4M4 20l6-6"/>',
  tonie: '<path d="M12 3c-2.2 0-4 1.8-4 4v1c-2.2.8-4 3-4 5.5V21h16v-7.5C20 11 18.2 8.8 16 8V7c0-2.2-1.8-4-4-4Z"/><path d="M9 12h6M8 17h8"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5M5 20h14"/>',
};

function escapeAttribute(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

export function icon(name, { label = "", className = "" } = {}) {
  const drawing = paths[name];
  if (!drawing) throw new Error(`Unknown icon: ${name}`);
  const accessibility = label
    ? `role="img" aria-label="${escapeAttribute(label)}"`
    : 'aria-hidden="true"';
  const classAttribute = className ? ` class="${escapeAttribute(className)}"` : "";
  return `<svg${classAttribute} ${accessibility} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" focusable="false">${drawing}</svg>`;
}
