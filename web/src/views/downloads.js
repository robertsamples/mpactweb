/* Downloads: every file the pipeline wrote for this run.
 *
 * Same set the desktop leaves in its output directory (docs/user-guide/
 * outputs.md), including the `.mpct` session file -- so an analysis run here
 * can be downloaded and reopened in the desktop app, and the GNPS2 exports
 * (`_filtered_source`, `_reindexed`) can be submitted directly. */

import { api } from '../api.js';
import { store } from '../state.js';
import { bytes, clear, el } from '../util.js';

const runId = () => (store.get('run') || {}).run_id;

export function buildDownloadsView() {
  const node = el('div');
  const host = el('div');

  const load = async () => {
    const id = runId();
    clear(host);
    if (!id) return;
    let items;
    try {
      items = await api.downloads(id);
    } catch (error) {
      host.append(el('div', { class: 'notice error', text: error.message }));
      return;
    }

    if (!items.length) {
      host.append(el('div', { class: 'notice warn', text: 'This run produced no output files.' }));
      return;
    }

    const body = el('tbody');
    for (const item of items) {
      body.append(el('tr', {}, [
        el('td', {}, [el('a', { href: api.downloadUrl(id, item.key), text: item.name,
                                style: { color: 'var(--accent)' } })]),
        el('td', { text: item.description }),
        el('td', { class: 'num', text: bytes(item.size) }),
      ]));
    }

    host.append(el('div', { class: 'panel' }, [
      el('h3', {}, [el('span', { text: 'Output files' }),
                    el('span', { class: 'hint', text: `${items.length} files` })]),
      el('div', { class: 'table-wrap' }, [
        el('table', { class: 'data' }, [
          el('thead', {}, [el('tr', {}, ['File', 'Contents', 'Size']
            .map((label) => el('th', { text: label })))]),
          body,
        ]),
      ]),
      el('div', { class: 'btn-row', style: { marginTop: '12px' } }, [
        el('a', { class: 'btn primary', href: api.downloadAllUrl(id), text: 'Download all (.zip)' }),
      ]),
    ]));

    const hasSession = items.some((item) => item.key === 'session');
    if (hasSession) {
      host.append(el('div', { class: 'notice info' }, [
        'The ', el('code', { text: '.mpct' }), ' session file opens in the MPACT desktop app, '
        + 'use it to continue this exact analysis there.',
      ]));
    }
    if (items.some((item) => item.key === 'reindexed')) {
      host.append(el('div', { class: 'notice info' }, [
        'The ', el('code', { text: '_reindexed' }), ' fragment database and ',
        el('code', { text: '_filtered_source' }), ' peak table are the pair GNPS2 expects: '
        + 'their entry indices line up with the filtered peak table\'s row order.',
      ]));
    }
  };

  node.append(el('h2', { text: 'Downloads' }));
  node.append(el('p', { class: 'lede' },
    ['Every file this analysis produced.']));
  node.append(host);

  return { id: 'downloads', label: 'Downloads', group: 'Results', requiresRun: true,
           node, onShow: load };
}
