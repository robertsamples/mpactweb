/* Feature Info: the searchable feature table plus the selected feature's
 * details, abundance plot, MS2 spectrum and NPAtlas matches.
 *
 * This is the desktop's feature search tree (searchtree.py) and its compound
 * details dialog (ui_featureinfo.py) on one page. The per-column filters use
 * the same rules as the desktop -- they are evaluated server-side by the same
 * vendored `treefilters` module -- and selecting a row here selects the
 * feature everywhere else, exactly as `highlight_feature` does. */

import { api } from '../api.js';
import { store } from '../state.js';
import { clear, debounce, el, num, toast } from '../util.js';
import { abundance as drawAbundance, spectrum as drawSpectrum } from '../plots/review.js';

const PAGE_SIZE = 200;

const runId = () => (store.get('run') || {}).run_id;

export function buildFeatureInfoView() {
  const node = el('div');
  const tableHost = el('div');
  const detailHost = el('div');

  const query = { offset: 0, limit: PAGE_SIZE, sort: 'm/z', descending: false,
                  filters: {}, only_searched: false };
  let table = null;

  // --- feature table ----------------------------------------------------

  const loadTable = async () => {
    const id = runId();
    if (!id) return;
    try {
      table = await api.features(id, query);
    } catch (error) {
      clear(tableHost).append(el('div', { class: 'notice error', text: error.message }));
      return;
    }
    renderTable();
  };

  const reload = debounce(() => { query.offset = 0; loadTable(); }, 260);

  function filterRow(column) {
    if (column.type === 'text') {
      return el('input', {
        type: 'text', placeholder: 'contains…', style: { width: '100%' },
        value: (query.filters[column.key] || {}).text || '',
        on: { input: (event) => {
          query.filters[column.key] = { text: event.target.value };
          reload();
        } },
      });
    }
    if (column.type === 'number') {
      const current = query.filters[column.key] || {};
      const box = (which, placeholder) => el('input', {
        type: 'number', placeholder, style: { width: 'calc(50% - 2px)', minWidth: '48px' },
        step: 'any',
        value: current[which] ?? '',
        on: { input: (event) => {
          const value = event.target.value === '' ? null : parseFloat(event.target.value);
          query.filters[column.key] = { ...query.filters[column.key], [which]: value };
          reload();
        } },
      });
      return el('div', { style: { display: 'flex', gap: '2px' } }, [box('min', 'min'), box('max', 'max')]);
    }
    // Category: the space-joined `groups` tokens, as a multi-select.
    const options = (table && table.categories) || [];
    const chosen = new Set(((query.filters[column.key] || {}).allowed) || []);
    return el('select', {
      multiple: true, size: 1, style: { width: '100%', height: '26px' },
      on: { change: (event) => {
        const values = Array.from(event.target.selectedOptions).map((option) => option.value);
        query.filters[column.key] = values.length ? { allowed: values } : null;
        reload();
      } },
    }, options.map((token) => el('option', { value: token, text: token, selected: chosen.has(token) })));
  }

  function renderTable() {
    clear(tableHost);
    if (!table) return;

    const head = el('tr');
    for (const column of table.columns) {
      const active = query.sort === column.key;
      head.append(el('th', {
        title: 'Click to sort',
        on: { click: () => {
          query.descending = active ? !query.descending : false;
          query.sort = column.key;
          query.offset = 0;
          loadTable();
        } },
      }, [
        el('span', { text: column.label }),
        active ? el('span', { class: 'sort', text: query.descending ? ' ▼' : ' ▲' }) : null,
      ]));
    }

    const filters = el('tr');
    for (const column of table.columns) filters.append(el('th', {}, [filterRow(column)]));

    const body = el('tbody');
    const selected = store.get('selectedFeature');
    for (const row of table.rows) {
      const compound = row[0];
      body.append(el('tr', {
        class: compound === selected ? 'selected' : '',
        on: { click: () => store.set({ selectedFeature: compound }) },
      }, table.columns.map((column, index) => el('td', {
        class: column.type === 'number' ? 'num' : '',
        text: column.type === 'number' ? num(row[index], column.digits ?? 3) : (row[index] ?? ''),
      }))));
    }

    const shownFrom = table.total ? query.offset + 1 : 0;
    const shownTo = Math.min(query.offset + query.limit, table.total);

    tableHost.append(el('div', { class: 'panel' }, [
      el('h3', {}, [
        el('span', { text: 'Features' }),
        el('span', { class: 'hint', text: `${shownFrom}–${shownTo} of ${table.total.toLocaleString()}` }),
      ]),
      el('div', { class: 'table-wrap' }, [
        el('table', { class: 'data' }, [el('thead', {}, [head, filters]), body]),
      ]),
      el('div', { class: 'btn-row', style: { marginTop: '10px' } }, [
        el('button', {
          class: 'btn small', text: '‹ Previous', disabled: query.offset === 0,
          on: { click: () => { query.offset = Math.max(0, query.offset - query.limit); loadTable(); } },
        }),
        el('button', {
          class: 'btn small', text: 'Next ›', disabled: shownTo >= table.total,
          on: { click: () => { query.offset += query.limit; loadTable(); } },
        }),
        el('button', {
          class: 'btn small', text: 'Clear filters',
          on: { click: () => { query.filters = {}; query.offset = 0; loadTable(); } },
        }),
        searchButton(),
        el('label', { class: 'check', title: 'Only features that passed filtering are searched' }, [
          el('input', {
            type: 'checkbox', checked: query.only_searched,
            on: { change: (event) => {
              query.only_searched = event.target.checked;
              query.offset = 0;
              loadTable();
            } },
          }),
          el('span', { class: 'muted', text: 'Searched features only' }),
        ]),
      ]),
    ]));
  }

  /** Fill the Hits column by running the NPAtlas search over every filtered
   *  feature -- the desktop does this when its Feature Info tab first opens.
   *  It is an explicit button here because it costs seconds and needs the
   *  33MB reference table. */
  function searchButton() {
    if ((store.get('health') || {}).npatlas === false) {
      return el('span', { class: 'muted',
                          text: 'NPAtlas not installed on this server' });
    }
    const button = el('button', { class: 'btn small', text: 'Search NPAtlas (all features)' });
    button.addEventListener('click', async () => {
      button.disabled = true;
      button.textContent = 'Searching…';
      try {
        const body = await api.dbsearch(runId());
        toast(`${body.matched} of ${body.searched} features matched within `
              + `${body.ppm_threshold} ppm`, 'ok');
        // Only features that survived filtering are searched, so unfiltered
        // rows would show a blank Hits column. Narrow to the searched set,
        // which is exactly what the desktop's feature tree lists.
        query.only_searched = true;
        query.offset = 0;
        await loadTable();
      } catch (error) {
        toast(error.message, 'error');
        button.disabled = false;
        button.textContent = 'Search NPAtlas (all features)';
      }
    });
    return button;
  }

  // --- selected feature -------------------------------------------------

  const renderDetail = async () => {
    const compound = store.get('selectedFeature');
    const id = runId();
    clear(detailHost);
    if (!compound || !id) {
      detailHost.append(el('div', { class: 'panel' }, [
        el('h3', { text: 'No feature selected' }),
        el('p', { class: 'muted',
                  text: 'Pick a row here, or click a point on any plot — the selection is shared '
                        + 'across every tab.' }),
      ]));
      return;
    }

    // Identity block, filled immediately so the panel never looks empty.
    const identity = el('div', { class: 'panel' }, [
      el('h3', {}, [el('span', { text: compound })]),
      el('div', { class: 'muted', text: 'Loading…' }),
    ]);
    detailHost.append(identity);

    const abundancePanel = el('div', { class: 'panel' }, [
      el('h3', { text: 'Abundance' }),
      el('div', { class: 'plot-surface', style: { minHeight: '300px' } }),
    ]);
    detailHost.append(abundancePanel);

    const spectrumPanel = el('div', { class: 'panel' }, [
      el('h3', { text: 'MS2 spectrum' }),
      el('div', { class: 'plot-surface', style: { minHeight: '250px' } }),
    ]);
    detailHost.append(spectrumPanel);

    const matchPanel = el('div', { class: 'panel' }, [
      el('h3', { text: 'Database matches' }),
      el('div', { class: 'muted', text: 'Searching NPAtlas…' }),
    ]);
    detailHost.append(matchPanel);

    // Details.
    try {
      const detail = await api.feature(id, compound);
      const fields = detail.fields;
      const list = el('dl', { class: 'kv' });
      const show = [
        ['m/z', 'm/z', 4], ['Retention time (min)', 'Retention time', 3],
        ['kmd', 'Mass defect', 4], ['max', 'Max abundance', 0],
        ['fc', 'Fold change', 3], ['logfc', 'log2 FC', 3],
        ['-logp', '-log10 p', 3], ['-logq', '-log10 q', 3],
        ['median CV', 'Median CV', 3], ['average CV', 'Mean CV', 3],
        ['groups', 'Groups', null], ['hits', 'DB hits', 0],
      ];
      for (const [key, label, digits] of show) {
        if (fields[key] === undefined || fields[key] === null) continue;
        list.append(el('dt', { text: label }));
        list.append(el('dd', { text: digits === null ? String(fields[key]) : num(fields[key], digits) }));
      }
      clear(identity).append(el('h3', { text: compound }), list);
    } catch (error) {
      clear(identity).append(el('h3', { text: compound }),
                             el('div', { class: 'notice error', text: error.message }));
    }

    // Abundance.
    try {
      const payload = await api.abundance(id, compound);
      drawAbundance(abundancePanel.querySelector('.plot-surface'), payload);
    } catch (error) {
      abundancePanel.querySelector('.plot-surface').innerHTML =
        `<div class="placeholder">${error.message}</div>`;
    }

    // Spectrum (only when the run had a fragment database).
    try {
      const payload = await api.spectrum(id, compound);
      drawSpectrum(spectrumPanel.querySelector('.plot-surface'), payload);
    } catch (error) {
      spectrumPanel.querySelector('.plot-surface').innerHTML =
        `<div class="placeholder">${error.message}</div>`;
    }

    // NPAtlas matches.
    try {
      const payload = await api.matches(id, compound);
      renderMatches(matchPanel, payload, id);
    } catch (error) {
      clear(matchPanel).append(el('h3', { text: 'Database matches' }),
                               el('div', { class: 'notice warn', text: error.message }));
    }
  };

  function renderMatches(panel, payload, id) {
    clear(panel);
    panel.append(el('h3', {}, [
      el('span', { text: 'Database matches' }),
      el('span', { class: 'hint',
                   text: `m/z ${num(payload.mz, 4)} ± ${payload.ppm_threshold} ppm` }),
    ]));
    if (!payload.matches.length) {
      panel.append(el('p', { class: 'muted', text: 'No NPAtlas match within the ppm window.' }));
      return;
    }

    const structure = el('div', { class: 'structure hidden' });
    const body = el('tbody');
    for (const match of payload.matches.slice(0, 200)) {
      body.append(el('tr', {
        title: 'Show structure',
        on: { click: () => {
          if (!match.compound_smiles) { toast('No SMILES for this match', 'info'); return; }
          structure.classList.remove('hidden');
          clear(structure).append(el('img', {
            src: api.structureUrl(id, match.compound_smiles),
            alt: match.compound_name || 'structure',
            on: { error: () => { clear(structure).append(
              el('div', { class: 'muted', text: 'Structure rendering is unavailable on this server.' })); } },
          }));
        } },
      }, [
        el('td', { text: match.compound_name || '' }),
        el('td', { text: match.compound_molecular_formula || '' }),
        el('td', { class: 'num', text: num(match.compound_accurate_mass, 4) }),
        el('td', { class: 'num', text: num(match.ppm, 2) }),
        el('td', { text: `${match.genus || ''} ${match.origin_species || ''}`.trim() }),
      ]));
    }

    panel.append(el('div', { class: 'table-wrap', style: { maxHeight: '260px' } }, [
      el('table', { class: 'data' }, [
        el('thead', {}, [el('tr', {}, ['Name', 'Formula', 'Accurate mass', 'ppm', 'Origin']
          .map((label) => el('th', { text: label })))]),
        body,
      ]),
    ]));
    panel.append(structure);
    panel.append(el('p', { class: 'muted', style: { marginTop: '8px' },
                          text: 'Click a match to render its structure.' }));
  }

  // --- assembly ---------------------------------------------------------

  node.append(el('h2', { text: 'Feature Info' }));
  node.append(el('p', { class: 'lede' },
    ['Every feature in the run, filterable per column. The selected feature is '
     + 'highlighted on every plot.']));
  node.append(el('div', { class: 'split' }, [tableHost, detailHost]));

  store.on('selectedFeature', () => {
    if (!node.classList.contains('active')) return;
    renderTable();
    renderDetail();
  });

  return {
    id: 'features', label: 'Feature Info', group: 'Results', requiresRun: true, node,
    onShow: async () => { await loadTable(); renderDetail(); },
  };
}
