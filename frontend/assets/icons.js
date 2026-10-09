// Small inline icon set (24px grid, stroke icons).
const P = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  library: '<path d="M4 4h4v16H4zM10 4h4v16h-4z"/><path d="m16.2 4.8 3.9-1 3.6 14.6-3.9 1z" transform="translate(-1.5 .8) scale(.92)"/>',
  "panel-left": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9 4v16"/>',
  moon: '<path d="M20.5 13.2A8.5 8.5 0 1 1 10.8 3.5a6.6 6.6 0 0 0 9.7 9.7z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  "arrow-up": '<path d="M12 19V5M5.5 11.5 12 5l6.5 6.5"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
  check: '<path d="m5 12.5 4.5 4.5L19.5 7"/>',
  languages: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.6 3.8 5.6 3.8 9s-1.2 6.4-3.8 9c-2.6-2.6-3.8-5.6-3.8-9S9.4 5.6 12 3z"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20v-.5A5 5 0 0 1 7.5 14.5h3a5 5 0 0 1 5 5v.5"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18.5 14.7a5 5 0 0 1 3 4.8v.5"/>',
  "chevron-down": '<path d="m6 9 6 6 6-6"/>',
  flask: '<path d="M9 3h6M10 3v6.5L4.8 18.2A2 2 0 0 0 6.5 21h11a2 2 0 0 0 1.7-2.8L14 9.5V3"/><path d="M7.5 15h9"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 3.5V8h4.5M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20.5V16h-4.5"/>',
  trash: '<path d="M4 7h16M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13"/>',
  scale: '<path d="M12 4v16M7 20h10M5 7h14M12 4l0 3"/><path d="m5 7-3 6.5a3.2 3.2 0 0 0 6 0zM19 7l-3 6.5a3.2 3.2 0 0 0 6 0z"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.8v.2"/>',
  alert: '<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.5M12 17v.2"/>',
  external: '<path d="M14 4h6v6M20 4l-8.5 8.5M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  "list-tree": '<path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
};

export function icon(name, cls = "") {
  return `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${P[name] || ""}</svg>`;
}

export function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((node) => {
    const name = node.getAttribute("data-icon");
    if (node.dataset.iconDone === name) return;
    node.querySelector(":scope > svg.i")?.remove();
    node.insertAdjacentHTML("afterbegin", icon(name));
    node.dataset.iconDone = name;
  });
}

export function setIcon(node, name) {
  node.setAttribute("data-icon", name);
  hydrateIcons(node.parentElement || document);
}
