/* The selection status bar along the bottom of the window.
 *
 * The desktop keeps the picked feature and the picked injection on screen in
 * the main window (`lbl_featurename`, `lbl_featuremz`, `lbl_featurert`,
 * `lbl_injname`). This is that: a persistent readout rather than a toast
 * that disappears before you have read it.
 *
 * It listens to the store, so it fills in no matter where the selection was
 * made -- a scatter point, a heatmap cell, a table row.
 */

import { api } from '../api.js';
import { store } from '../state.js';
import { clear, el, num } from '../util.js';

const runId = () => (store.get('run') || {}).run_id;

export function createStatusBar() {
  const node = el('footer', { class: 'statusbar' });
  let token = 0;

  const slot = (label, value, kind = '') => [
    el('span', { class: 'slot-label', text: label }),
    el('span', { class: `value ${kind}`.trim(), text: value }),
  ];

  const render = async (detail) => {
    const compound = store.get('selectedFeature');
    const sample = store.get('selectedSample');
    clear(node);

    if (!compound) {
      node.append(...slot('Feature:', 'none selected', 'none'));
    } else if (!detail) {
      node.append(...slot('Feature:', compound, 'feature'));
    } else {
      const f = detail.fields || {};
      node.append(...slot('Feature:', compound, 'feature'));
      if (f['m/z'] != null) node.append(...slot('m/z:', num(f['m/z'], 4)));
      if (f['Retention time (min)'] != null) {
        node.append(...slot('RT:', num(f['Retention time (min)'], 3)));
      }
      if (f.fc != null) node.append(...slot('FC:', num(f.fc, 3)));
      if (f.groups) node.append(...slot('Groups:', String(f.groups).trim()));
    }

    if (sample) node.append(...slot('Injection/Sample:', sample));

    node.append(el('span', { class: 'spacer' }));
    if (compound || sample) {
      node.append(el('button', {
        type: 'button', text: 'Clear selection', title: 'Deselect',
        on: { click: () => store.set({ selectedFeature: null, selectedSample: null }) },
      }));
    }
  };

  const refresh = async () => {
    const compound = store.get('selectedFeature');
    const id = runId();
    const mine = ++token;
    // Draw the id straight away, then fill in the numbers when they arrive --
    // the bar should never lag behind the click.
    render(null);
    if (!compound || !id) return;
    try {
      const detail = await api.feature(id, compound);
      if (mine === token) render(detail);
    } catch {
      /* Feature not in this run (e.g. after switching runs) -- the id alone
         is still the honest thing to show. */
    }
  };

  store.on(['selectedFeature', 'selectedSample', 'run'], refresh);
  refresh();
  return { node, refresh };
}
