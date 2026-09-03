/* Run history dialog.
 *
 * The desktop reloads a previous analysis from a `.mpct` file. Here, previous
 * runs are still on the server, so switching back to one is a click rather
 * than a re-upload -- and the `.mpct` file is still produced and downloadable
 * for the desktop app. */

import { api } from '../api.js';
import { store } from '../state.js';
import { clear, el, timeAgo, toast, duration } from '../util.js';

let dialog = null;

const STATE_CLASS = {
  complete: 'ok', failed: 'error', cancelled: 'warn',
  running: 'info', queued: 'info',
};

export async function showRunsDialog() {
  const { selectRun } = await import('../app.js');

  if (!dialog) {
    dialog = el('dialog', {
      style: {
        background: 'var(--panel)', color: 'var(--text)', border: '1px solid var(--border)',
        borderRadius: 'var(--radius)', padding: '0', width: 'min(760px, 92vw)',
      },
    });
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) dialog.close();
    });
    document.body.append(dialog);
  }

  const body = el('div', { style: { padding: '18px 20px 22px' } });
  clear(dialog).append(body);
  body.append(el('div', { class: 'btn-row', style: { marginBottom: '12px' } }, [
    el('h3', { text: 'Analysis runs', style: { margin: '0', flex: '1' } }),
    el('button', { class: 'btn small', text: 'Close', on: { click: () => dialog.close() } }),
  ]));

  let runs;
  try {
    runs = await api.listRuns();
  } catch (error) {
    body.append(el('div', { class: 'notice error', text: error.message }));
    dialog.showModal();
    return;
  }
  store.set({ runs });

  if (!runs.length) {
    body.append(el('p', { class: 'muted', text: 'No analyses in this session yet.' }));
    dialog.showModal();
    return;
  }

  const active = (store.get('run') || {}).run_id;
  const list = el('div');
  for (const run of runs) {
    const isActive = run.run_id === active;
    list.append(el('div', {
      class: `slot ${isActive ? 'dragover' : ''}`,
      style: { alignItems: 'flex-start' },
    }, [
      el('div', { class: 'slot-value' }, [
        el('div', { class: 'filename', text: run.label || run.peaktable || run.run_id }),
        el('div', { class: 'meta' }, [
          el('span', { class: `pill ${STATE_CLASS[run.state] || ''}`, text: run.state }),
          ` ${timeAgo(run.created_at)}`,
          run.started_at ? ` · took ${duration(run.started_at, run.finished_at)}` : '',
          run.peaktable ? ` · ${run.peaktable}` : '',
        ]),
      ]),
      el('div', { class: 'btn-row' }, [
        el('button', {
          class: 'btn small', text: isActive ? 'Active' : 'Open', disabled: isActive,
          on: { click: async () => {
            try {
              await selectRun(run.run_id);
              dialog.close();
              toast('Switched to that analysis', 'ok');
            } catch (error) { toast(error.message, 'error'); }
          } },
        }),
        el('button', {
          class: 'btn small danger', text: 'Delete',
          on: { click: async () => {
            try {
              await api.deleteRun(run.run_id);
              if (isActive) store.set({ run: null, catalogue: null, selectedFeature: null });
              showRunsDialog();
            } catch (error) { toast(error.message, 'error'); }
          } },
        }),
      ]),
    ]));
  }
  body.append(list);
  dialog.showModal();
}
