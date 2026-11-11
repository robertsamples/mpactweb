/* Substructure sketcher: JSME in a modal, with a SMILES/SMARTS box beside it.
 * JSME is loaded on first use, from /vendor/jsme when vendored
 * (scripts/fetch_web_vendor.py) and from the JSME site otherwise. */

import { api } from '../api.js';
import { el } from '../util.js';

const SOURCES = ['/vendor/jsme/jsme.nocache.js',
                 'https://jsme-editor.github.io/dist/jsme/jsme.nocache.js'];

let loading = null;

function loadJsme() {
  if (window.JSApplet && window.JSApplet.JSME) return Promise.resolve();
  if (loading) return loading;
  loading = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timed out')), 20000);
    window.jsmeOnLoad = () => { clearTimeout(timer); resolve(); };
    const attempt = (index) => {
      if (index >= SOURCES.length) { clearTimeout(timer); reject(new Error('not reachable')); return; }
      const script = document.createElement('script');
      script.src = SOURCES[index];
      script.onerror = () => { script.remove(); attempt(index + 1); };
      document.head.append(script);
    };
    attempt(0);
  }).catch((error) => { loading = null; throw error; });
  return loading;
}

let dialog = null;
let applet = null;
let editorHost = null;
let quiet = false;

/** Resolves to the query string, '' when cleared, or null when cancelled. */
export function openSketcher(runId, current = '') {
  return new Promise((resolve) => {
    if (!dialog) {
      dialog = el('dialog', { class: 'sketcher' });
      document.body.append(dialog);
    }
    if (!editorHost) {
      editorHost = el('div', { class: 'sk-editor', id: 'jsme-host' },
                      [el('div', { class: 'placeholder', text: 'Loading editor…' })]);
    }
    const host = editorHost;
    const input = el('input', { type: 'text', class: 'sk-smiles', value: current,
                                placeholder: 'SMILES or SMARTS', spellcheck: false });
    const error = el('span', { class: 'sk-error' });
    let settled = false;

    const finish = (value) => {
      if (settled) return;
      settled = true;
      dialog.close();
      resolve(value);
    };

    const apply = async () => {
      const query = input.value.trim();
      if (!query) { finish(''); return; }
      error.textContent = '';
      try {
        await api.checkSubstructure(runId, query);
        finish(query);
      } catch (failure) {
        error.textContent = failure.message;
      }
    };

    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); apply(); }
    });
    input.addEventListener('change', () => {
      if (applet && input.value.trim()) {
        try { applet.readGenericMolecularInput(input.value.trim()); } catch { /* SMARTS JSME can't read */ }
      }
    });

    dialog.replaceChildren(
      el('div', { class: 'sk-head' }, [el('h3', { text: 'Substructure' })]),
      host,
      el('div', { class: 'sk-row' }, [input]),
      el('div', { class: 'sk-foot' }, [
        error,
        el('span', { class: 'spacer' }),
        el('button', { class: 'btn small', type: 'button', text: 'Clear', on: { click: () => {
          input.value = '';
          if (applet) applet.reset();
        } } }),
        el('button', { class: 'btn small', type: 'button', text: 'Cancel',
                       on: { click: () => finish(null) } }),
        el('button', { class: 'btn small primary', type: 'button', text: 'Apply',
                       on: { click: apply } }),
      ]),
    );
    dialog.onclose = () => finish(null);
    dialog.showModal();
    input.focus();

    const show = (value) => {
      quiet = true;
      try {
        if (value) applet.readGenericMolecularInput(value);
        else applet.reset();
      } catch { /* SMARTS JSME can't read */ } finally { quiet = false; }
    };

    if (applet) { show(current); return; }
    loadJsme().then(() => {
      if (applet) { show(current); return; }
      host.replaceChildren();
      applet = new window.JSApplet.JSME('jsme-host', '640px', '380px',
                                        { options: 'query,newlook' });
      show(input.value.trim());
      applet.setCallBack('AfterStructureModified', () => {
        if (quiet || !dialog.open) return;
        dialog.querySelector('.sk-smiles').value = applet.smiles();
        dialog.querySelector('.sk-error').textContent = '';
      });
    }).catch(() => {
      if (!applet) host.replaceChildren(el('div', { class: 'placeholder', text: 'Editor unavailable' }));
    });
  });
}
