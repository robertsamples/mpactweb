/* Flags: coloured marks on features.
 *
 * A flag is a colour attached to a feature. Flags are kept with the run on
 * the server, so they are still there in another browser and go into the
 * feature export; here they are one store key (`flags`, compound -> colour)
 * that the table's Flag column and the plots both read. Where the yellow
 * marker says "selected now", a flag is a lasting highlight -- and there can
 * be several colours of them at once. */

import { api } from './api.js';
import { selection, store } from './state.js';
import { el, toast } from './util.js';

const COLOUR_KEY = 'mpact.flag.colour';

// Distinct from each other, from the yellow selection marker, and legible on
// the plots' grey ground. Offered as the colour picker's swatches.
export const FLAG_PRESETS = ['#f032e6', '#e6194b', '#f58231', '#3cb44b', '#42d4f4',
                             '#4363d8', '#911eb4', '#9a6324', '#ffffff', '#000000'];

const runId = () => (store.get('run') || {}).run_id;

/** The colour the next flag will have: the last one used. */
export function flagColour() {
  try {
    const saved = localStorage.getItem(COLOUR_KEY);
    if (/^#[0-9a-f]{6}$/i.test(saved || '')) return saved.toLowerCase();
  } catch { /* storage blocked */ }
  return FLAG_PRESETS[0];
}

function rememberColour(colour) {
  try { localStorage.setItem(COLOUR_KEY, colour); } catch { /* this visit only */ }
}

const EMPTY = new Map();
let cached = { source: null, map: EMPTY };

export const flags = {
  /** compound -> colour, as a Map rebuilt only when the flags change. */
  map() {
    const source = store.get('flags') || {};
    if (cached.source !== source) cached = { source, map: new Map(Object.entries(source)) };
    return cached.map;
  },

  /** The same, or nothing when "Flags in plots" is off: what plots draw. */
  shown() { return store.get('showFlags') ? this.map() : EMPTY; },

  colourOf: (compound) => (store.get('flags') || {})[compound] || null,

  count() { return this.map().size; },
};

/** Fetch a run's flags into the store. */
export async function loadFlags(id) {
  try {
    const body = await api.flags(id);
    if (runId() === id) store.set({ flags: body.flags || {} });
  } catch {
    // A server without flags, or a run that has gone: no flags to show.
    if (runId() === id) store.set({ flags: {} });
  }
}

/** Flag `ids` with `colour`, or clear their flags with `colour` null. */
export async function setFlags(ids, colour) {
  const id = runId();
  if (!id || !ids.length) return;
  try {
    const body = await api.setFlags(id, ids, colour);
    if (runId() === id) store.set({ flags: body.flags || {} });
  } catch (error) {
    toast(`Could not ${colour ? 'flag' : 'unflag'}: ${error.message}`, 'error');
  }
}

/** Flag one feature with the current colour, or take its flag off. */
export function toggleFlag(compound) {
  return setFlags([compound], flags.colourOf(compound) ? null : flagColour());
}

export function setShowFlags(value) {
  store.set({ showFlags: Boolean(value) });
  try { localStorage.setItem('mpact.flags.show', value ? '1' : '0'); } catch { /* ignore */ }
}

let presets = null;
function presetList() {
  if (!presets) {
    presets = el('datalist', { id: 'flag-presets' },
                 FLAG_PRESETS.map((colour) => el('option', { value: colour })));
    document.body.append(presets);
  }
  return presets.id;
}

/**
 * The controls that flag the selection: a colour, "Flag" and "Unflag".
 *
 * Returns `{node, sync}`; call `sync()` when the selection or the flags
 * change (it enables the buttons for what the selection allows).
 */
export function flagControls() {
  const colour = el('input', {
    type: 'color', class: 'flag-colour', value: flagColour(), list: presetList(),
    title: 'Flag colour',
    on: { change: (event) => rememberColour(event.target.value) },
  });
  const flag = el('button', {
    class: 'btn small', type: 'button', text: 'Flag',
    title: 'Mark the selected features with this colour',
    on: { click: async () => {
      rememberColour(colour.value);
      flag.disabled = true;
      await setFlags(selection.ids(), colour.value.toLowerCase());
      sync();
    } },
  });
  const unflag = el('button', {
    class: 'btn small', type: 'button', text: 'Unflag',
    title: 'Take the flag off the selected features',
    on: { click: async () => {
      unflag.disabled = true;
      await setFlags(selection.ids(), null);
      sync();
    } },
  });
  const node = el('span', { class: 'flag-controls' }, [colour, flag, unflag]);

  function sync() {
    const ids = selection.ids();
    const map = flags.map();
    node.classList.toggle('hidden', ids.length === 0);
    flag.disabled = ids.length === 0;
    unflag.disabled = !ids.some((id) => map.has(id));
    // Another set of controls may have changed the colour.
    if (document.activeElement !== colour) colour.value = flagColour();
  }
  sync();
  return { node, sync };
}

/** The "Flags in plots" switch. */
export function showFlagsToggle(label = 'Flags in plots') {
  return el('label', {
    class: 'check',
    title: 'Mark flagged features on the plots, each in its flag\'s colour',
  }, [
    el('input', {
      type: 'checkbox', checked: Boolean(store.get('showFlags')),
      on: { change: (event) => setShowFlags(event.target.checked) },
    }),
    el('span', { class: 'muted', text: label }),
  ]);
}
