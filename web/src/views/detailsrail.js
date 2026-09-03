/* The selected-feature details rail shown beside every plot.
 *
 * The desktop answers "what did I just click?" with the feature labels in
 * the main window plus a Details button that opens the compound window. The
 * web equivalent is this rail: it sits next to the plot, fills in as soon as
 * a feature is selected anywhere, and links through to the full Feature Info
 * tab for spectra and database matches.
 *
 * One rail instance per plot view; they all read the same `selectedFeature`
 * store key, so a pick on any plot updates whichever rail is on screen.
 */

import { api } from '../api.js';
import { store } from '../state.js';
import { clear, el, num, toast } from '../util.js';
import { abundance as drawAbundance } from '../plots/review.js';

const runId = () => (store.get('run') || {}).run_id;

// Fields worth showing at a glance, in the order the desktop's labels and
// details window present them.
const FIELDS = [
  ['m/z', 'm/z', 4],
  ['Retention time (min)', 'Retention time', 3],
  ['kmd', 'Mass defect', 4],
  ['max', 'Max abundance', 0],
  ['fc', 'Fold change', 3],
  ['logfc', 'log2 FC', 3],
  ['-logp', '-log10 p', 3],
  ['-logq', '-log10 q', 3],
  ['median CV', 'Median CV', 3],
  ['hits', 'DB hits', 0],
];

export function createDetailsRail(onOpenFeatureInfo) {
  const node = el('aside', { class: 'details-rail empty' });
  let token = 0;          // guards against a slow response for a stale pick

  const showEmpty = () => {
    node.classList.add('empty');
    clear(node).append(el('p', { class: 'muted', style: { textAlign: 'center' } },
      ['Click a point, cell or row to see the feature here.']));
  };

  const render = async () => {
    const compound = store.get('selectedFeature');
    const id = runId();
    if (!compound || !id) { showEmpty(); return; }

    const mine = ++token;
    node.classList.remove('empty');
    clear(node).append(
      el('div', { class: 'rail-head' }, [
        el('h4', { text: compound }),
        el('button', {
          class: 'btn small', text: '×', title: 'Clear selection',
          on: { click: () => store.set({ selectedFeature: null }) },
        }),
      ]),
      el('div', { class: 'muted', text: 'Loading…' }));

    let detail;
    try {
      detail = await api.feature(id, compound);
    } catch (error) {
      if (mine !== token) return;
      clear(node).append(el('div', { class: 'notice error', text: error.message }));
      return;
    }
    if (mine !== token) return;   // a newer selection won

    const list = el('dl', { class: 'kv' });
    for (const [key, label, digits] of FIELDS) {
      const value = detail.fields[key];
      if (value === undefined || value === null) continue;
      list.append(el('dt', { text: label }));
      list.append(el('dd', { text: num(value, digits) }));
    }
    const groups = detail.fields.groups;
    if (groups) {
      list.append(el('dt', { text: 'Groups' }));
      list.append(el('dd', { text: String(groups).trim() }));
    }

    const mini = el('div', { class: 'mini-plot' });
    clear(node).append(
      el('div', { class: 'rail-head' }, [
        el('h4', { text: compound }),
        el('button', {
          class: 'btn small', text: '×', title: 'Clear selection',
          on: { click: () => store.set({ selectedFeature: null }) },
        }),
      ]),
      list,
      mini,
      el('div', { class: 'rail-actions' }, [
        el('button', {
          class: 'btn small', text: 'Full details, spectrum & matches',
          on: { click: () => onOpenFeatureInfo && onOpenFeatureInfo() },
        }),
      ]));

    // Abundance-by-group is the one plot worth repeating here: it is what
    // makes a picked point interpretable without switching tabs.
    try {
      const payload = await api.abundance(id, compound);
      if (mine !== token) return;
      drawAbundance(mini, payload, { compact: true });
    } catch {
      if (mine !== token) return;
      mini.innerHTML = '<div class="placeholder">No abundance data '
        + '(this feature did not pass filtering).</div>';
    }
  };

  showEmpty();
  return {
    node,
    render,
    /** Redraw only when this rail's view is on screen; the others catch up
     *  when their view is next shown. */
    refreshIfVisible(viewNode) {
      if (viewNode.classList.contains('active')) render();
    },
  };
}
