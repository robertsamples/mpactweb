/* Saving feature tables as CSV.
 *
 * Two exports, one endpoint: the Feature Info view as it is listed (its
 * filters, its order), and the current selection wherever it was made -- a
 * lasso on the volcano plot, Ctrl-clicked rows, a heatmap band. Both carry
 * every per-feature column the run produced plus each feature's best
 * database matches. */

import { api } from './api.js';
import { selection, store } from './state.js';
import { saveBlob, toast } from './util.js';

const runId = () => (store.get('run') || {}).run_id;

/** Export with the given table options; `button` is disabled meanwhile. */
export async function exportFeatures(options, button) {
  const id = runId();
  if (!id) return;
  if (button) button.disabled = true;
  try {
    const { blob, name } = await api.exportFeatures(id, options);
    saveBlob(blob, name);
  } catch (error) {
    toast(`Could not export: ${error.message}`, 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

/** Export the selected features. They may include features the filters
 *  removed (picked on the Data Review plot), so nothing is left out, and the
 *  active hit filter still decides which match counts as each one's best. */
export function exportSelected(button, order = {}) {
  const ids = selection.ids();
  if (!ids.length) return Promise.resolve();
  return exportFeatures({
    sort: 'm/z', descending: false, ...order,
    include_filtered: true, hit_filter: store.get('hitFilter'), ids,
  }, button);
}
