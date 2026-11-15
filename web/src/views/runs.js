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

const KIND_LABEL = { peaktable: 'peak table', samplelist: 'sample list',
                     metadata: 'metadata', fragments: 'fragment database' };

export async function showRunsDialog() {
  const { selectRun, showView } = await import('../app.js');

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
    const title = el('div', { class: 'filename', text: run.label || run.peaktable || run.run_id });

    /** Edit the label in place: Enter saves, Escape puts it back. */
    const rename = () => {
      const input = el('input', {
        type: 'text', value: run.label || '', maxLength: 120,
        placeholder: 'Label for this run', style: { width: '100%' },
      });
      let done = false;
      const finish = async (save) => {
        if (done) return;
        done = true;
        if (save && input.value.trim() !== (run.label || '')) {
          try {
            const body = await api.renameRun(run.run_id, input.value.trim());
            if (isActive) store.set({ run: { ...store.get('run'), label: body.label } });
          } catch (error) { toast(error.message, 'error'); }
        }
        // Unless the list was closed meanwhile (the click that ended the
        // edit may have been on Close).
        if (dialog.open) showRunsDialog();
      };
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') { event.preventDefault(); finish(true); }
        // Not the dialog's own Escape, which would close the whole list.
        if (event.key === 'Escape') { event.preventDefault(); finish(false); }
      });
      input.addEventListener('blur', () => finish(true));
      title.replaceWith(input);
      input.focus();
      input.select();
    };

    list.append(el('div', {
      class: `slot ${isActive ? 'dragover' : ''}`,
      style: { alignItems: 'flex-start' },
    }, [
      el('div', { class: 'slot-value' }, [
        title,
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
          class: 'btn small', text: 'Rename', title: 'Give this run a label',
          on: { click: rename },
        }),
        el('button', {
          class: 'btn small', text: 'Reuse settings',
          title: 'Load this run\'s files, filters and settings into the form, '
                 + 'to change and run again',
          on: { click: async (event) => {
            const button = event.currentTarget;
            button.disabled = true;
            try {
              const { applyRunRequest } = await import('./setup.js');
              const missing = await applyRunRequest(await api.runRequest(run.run_id));
              dialog.close();
              showView('run');
              toast(missing.length
                ? `Settings loaded, but the ${missing.map((kind) => KIND_LABEL[kind] || kind).join(' and ')} `
                  + 'used by that run is no longer in the library - select one on File Selection'
                : 'Settings loaded - change what you like, then Run', missing.length ? 'info' : 'ok');
            } catch (error) {
              toast(error.message, 'error');
              button.disabled = false;
            }
          } },
        }),
        el('button', {
          class: 'btn small danger', text: 'Delete',
          on: { click: async () => {
            try {
              await api.deleteRun(run.run_id);
              if (isActive) {
                store.set({ run: null, catalogue: null, selectedFeature: null, flags: {} });
              }
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
