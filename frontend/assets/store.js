// Threads live in this browser's localStorage (your library). Reads and writes never throw.

const KEY = "panchayat.threads.v1";
const MAX_THREADS = 60;

function read() {
  try {
    const raw = localStorage.getItem(KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch (_) {
    return [];
  }
}

function slim(thread) {
  // Keep storage small: trim each panch's full answer, drop transient fields.
  return {
    ...thread,
    turns: thread.turns.map((t) => ({
      ...t,
      panches: Object.fromEntries(
        Object.entries(t.panches || {}).map(([id, p]) => [id, { ...p, answer: (p.answer || "").slice(0, 5000) }]),
      ),
    })),
  };
}

function write(list) {
  let items = list.slice(0, MAX_THREADS);
  while (items.length) {
    try {
      localStorage.setItem(KEY, JSON.stringify(items));
      return;
    } catch (_) {
      items = items.slice(0, -1); // over quota: drop the oldest and retry
    }
  }
}

export const store = {
  list() {
    return read().sort((a, b) => (b.updated || 0) - (a.updated || 0));
  },
  get(id) {
    return read().find((t) => t.id === id) || null;
  },
  save(thread) {
    const list = read().filter((t) => t.id !== thread.id);
    list.unshift(slim({ ...thread, updated: Date.now() }));
    write(list);
  },
  remove(id) {
    write(read().filter((t) => t.id !== id));
  },
  pref(name, value) {
    try {
      if (value === undefined) return localStorage.getItem(`panchayat.${name}`);
      localStorage.setItem(`panchayat.${name}`, value);
    } catch (_) {
      return null;
    }
    return value;
  },
};
