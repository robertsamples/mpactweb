/* The selection status bar along the bottom of the window.
 *
 * The desktop keeps the picked feature and the picked injection on screen in
 * the main window (`lbl_featurename`, `lbl_featuremz`, `lbl_featurert`,
 * `lbl_injname`). This is that: a persistent readout rather than a toast
 * that disappears before you have read it.
 *
 * What it shows follows what the open view actually selects. The feature
 * plots and the table pick features, so they get the feature's identity; the
 * ordination and the sample correlations pick an injection (the desktop only
 * updates `lbl_injname` from those), so they get the injection and the
 * biological group it belongs to. Carrying both sets everywhere would leave
 * half the bar stale on every view.
 */

import { api } from '../api.js';
import { store } from '../state.js';
import { clear, el, num, settle } from '../util.js';

const runId = () => (store.get('run') || {}).run_id;

// Views whose click target is an injection rather than a feature.
const SAMPLE_VIEWS = new Set(['multivariate', 'groupanalysis']);

export function createStatusBar() {
  const node = el('footer', { class: 'statusbar' });
  let token = 0;

  const slot = (label, value, kind = '') => [
    el('span', { class: 'slot-label', text: label }),
    el('span', { class: `value ${kind}`.trim(), text: value }),
  ];

  const render = (detail) => {
    const sampleView = SAMPLE_VIEWS.has(store.get('view'));
    const compound = store.get('selectedFeature');
    const sample = store.get('selectedSample');
    clear(node);

    if (sampleView) {
      const group = store.get('selectedSampleGroup');
      if (group) node.append(...slot('Group:', group));
      node.append(...slot('Injection/Sample:', sample || 'none selected',
                          sample ? '' : 'none'));
    } else if (!compound) {
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
    }

    node.append(el('span', { class: 'spacer' }));
    const anything = sampleView ? sample : compound;
    if (anything) {
      node.append(el('button', {
        type: 'button', text: 'Clear selection', title: 'Deselect',
        on: { click: () => store.set(sampleView
          ? { selectedSample: null, selectedSampleGroup: null }
          : { selectedFeature: null }) },
      }));
    }
  };

  // The numbers are fetched once the selection settles: a held arrow key in
  // the feature table would otherwise request every row it passes.
  const fill = settle(async (mine, id, compound) => {
    if (mine !== token) return;
    try {
      const detail = await api.feature(id, compound);
      if (mine === token) render(detail);
    } catch {
      /* Feature not in this run (e.g. after switching runs) -- the id alone
         is still the honest thing to show. */
    }
  }, 140);

  const refresh = () => {
    const compound = store.get('selectedFeature');
    const id = runId();
    const mine = ++token;
    // Draw the id straight away, then fill in the numbers when they arrive --
    // the bar should never lag behind the click.
    render(null);
    if (SAMPLE_VIEWS.has(store.get('view')) || !compound || !id) return;
    fill(mine, id, compound);
  };

  store.on(['selectedFeature', 'selectedSample', 'selectedSampleGroup', 'run', 'view'],
           refresh);
  refresh();
  return { node, refresh };
}
