/* Application state and change notification.
 *
 * One mutable store with topic-scoped subscribers. Views subscribe to the
 * topics they render from, so (for example) picking a feature on the m/z-vs-RT
 * plot updates the Feature Info panel and the highlight on every other plot
 * without those views knowing about each other -- the desktop's
 * `highlight_feature` broadcast, expressed as a subscription.
 *
 * The setup form is mirrored into localStorage so a page reload during a long
 * run doesn't lose a carefully configured analysis. Uploaded files live
 * server-side, so only their ids are stored.
 */

const STORAGE_KEY = 'mpact.setup.v1';

const initial = () => ({
  health: null,
  demo: null,
  files: [],
  inputs: { peaktable: null, samplelist: null, metadata: null, fragments: null },
  groups: [],
  groupWarnings: [],
  groupError: null,
  settings: null,           // filled from /api/runs/defaults
  groupsets: [],            // [{name, src, incl, excl, colour}]
  selectedGroupset: -1,
  run: null,                // {run_id, state, stage, ...}
  runs: [],
  catalogue: null,          // /plots response for the active run
  selectedFeature: null,
  view: 'files',
});

class Store {
  constructor() {
    this.data = initial();
    this.listeners = new Map();
  }

  get(key) { return this.data[key]; }

  /** Merge `patch` and notify every affected topic once. */
  set(patch) {
    const changed = [];
    for (const [key, value] of Object.entries(patch)) {
      if (this.data[key] !== value) changed.push(key);
      this.data[key] = value;
    }
    if (changed.length) this.emit(changed);
    return this.data;
  }

  on(keys, handler) {
    for (const key of [].concat(keys)) {
      if (!this.listeners.has(key)) this.listeners.set(key, new Set());
      this.listeners.get(key).add(handler);
    }
    return () => { for (const key of [].concat(keys)) this.listeners.get(key)?.delete(handler); };
  }

  emit(keys) {
    const called = new Set();
    for (const key of [].concat(keys)) {
      for (const handler of this.listeners.get(key) || []) {
        // A handler subscribed to several changed topics runs once per batch.
        if (called.has(handler)) continue;
        called.add(handler);
        try { handler(this.data, keys); } catch (error) { console.error(error); }
      }
    }
  }

  // --- setup persistence -------------------------------------------------

  persistSetup() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        inputs: this.data.inputs,
        settings: this.data.settings,
        groupsets: this.data.groupsets,
      }));
    } catch { /* private browsing / quota -- persistence is a convenience */ }
  }

  restoreSetup() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch { return null; }
  }

  clearSetup() {
    try { localStorage.removeItem(STORAGE_KEY); } catch { /* ignore */ }
  }

  // --- groupset helpers --------------------------------------------------
  // Mirrors groupsets.GroupSetModel: the selected index is always valid, and
  // a new set starts with every biological group in `excl` (the desktop's
  // "Available groups" bucket), so an untouched set matches nothing until the
  // user drags groups into "May be in" / "Must be in".

  addGroupset(name, allGroups) {
    const groupsets = [...this.data.groupsets,
                       { name: name || `Feature Set ${this.data.groupsets.length + 1}`,
                         src: [], incl: [], excl: [...allGroups],
                         colour: nextColour(this.data.groupsets.length) }];
    this.set({ groupsets, selectedGroupset: groupsets.length - 1 });
    this.persistSetup();
  }

  removeGroupset(index) {
    const groupsets = this.data.groupsets.filter((_, i) => i !== index);
    const selected = groupsets.length ? Math.max(0, Math.min(index, groupsets.length - 1)) : -1;
    this.set({ groupsets, selectedGroupset: selected });
    this.persistSetup();
  }

  updateGroupset(index, patch) {
    const groupsets = this.data.groupsets.map((item, i) =>
      (i === index ? { ...item, ...patch } : item));
    this.set({ groupsets });
    this.persistSetup();
  }

  /** Keep every set's buckets consistent with a new biological-group list:
   *  drop groups that no longer exist, and park newly discovered ones in
   *  `excl` (Available), which is where the desktop puts them too. */
  reconcileGroupsets(groups) {
    const known = new Set(groups);
    const groupsets = this.data.groupsets.map((item) => {
      const src = item.src.filter((g) => known.has(g));
      const incl = item.incl.filter((g) => known.has(g));
      const assigned = new Set([...src, ...incl]);
      const excl = groups.filter((g) => !assigned.has(g));
      return { ...item, src, incl, excl };
    });
    this.set({ groupsets });
  }
}

const PALETTE = ['#d62728', '#1f77b4', '#2ca02c', '#9467bd', '#ff7f0e',
                 '#17becf', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22'];
export const nextColour = (index) => PALETTE[index % PALETTE.length];

export const store = new Store();
