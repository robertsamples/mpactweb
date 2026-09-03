/* Feature Info: the full feature table, with the selected feature's detail
 * in the same side rail every plot tab uses.
 *
 * Closer to the desktop's search tree than a paged web table: every feature
 * is in the list at once, sorted and filtered server-side, and the arrow keys
 * walk through it. Only the table body scrolls -- the view itself doesn't --
 * so Up/Down always move the selection instead of scrolling the page out
 * from under it. The table gets the whole height; the detail lives beside it
 * rather than underneath, so the list stays as long as the window allows.
 *
 * Rows are windowed: at a few thousand features a full DOM table costs tens
 * of thousands of nodes and makes every keystroke stutter, so only the
 * visible slice is rendered between two spacer rows. The list stays
 * logically complete -- scroll position, selection and keyboard navigation
 * all address the whole set.
 *
 * Per-column filters are evaluated server-side by the vendored `treefilters`
 * module, so a filter behaves exactly as it does on the desktop.
 */

import { api } from '../api.js';
import { store } from '../state.js';
import { clear, debounce, el, num, toast } from '../util.js';

const ROW_HEIGHT = 25;      // must match `table.features tbody tr` height in app.css
const OVERSCAN = 12;        // rows rendered beyond the viewport, each side

const runId = () => (store.get('run') || {}).run_id;

export function buildFeatureInfoView() {
  const node = el('div', { class: 'plot-view features-view' });

  // include_filtered false == only features that survived filtering, which is
  // what the desktop's tree lists.
  const query = { sort: 'm/z', descending: false, filters: {}, include_filtered: false };
  let table = null;             // last response
  let rowIndex = new Map();     // compound -> row position, for keyboard nav
  let autoSearched = false;     // the one-off NPAtlas search has been attempted

  // --- table shell ------------------------------------------------------
  const headRow = el('tr');
  const filterRow = el('tr');
  const body = el('tbody');
  const topSpacer = el('tr', { class: 'spacer-row' });
  const bottomSpacer = el('tr', { class: 'spacer-row' });
  const tableEl = el('table', { class: 'data features' },
                     [el('thead', {}, [headRow, filterRow]), body]);
  const scroller = el('div', { class: 'table-scroll', tabIndex: 0 }, [tableEl]);
  const counts = el('span', { class: 'status' });

  const toolbar = el('div', { class: 'plot-toolbar' });
  const tablePane = el('div', { class: 'table-pane' }, [toolbar, scroller]);

  // The details rail is the app's own right-hand panel, shared with every
  // plot view, so a feature looks the same wherever it is inspected and
  // hiding it here hides it there too.
  const host = el('div', { class: 'plot-host' }, [tablePane]);

  // --- table rendering --------------------------------------------------

  function buildHeader() {
    clear(headRow);
    clear(filterRow);
    for (const column of table.columns) {
      const active = query.sort === column.key;
      headRow.append(el('th', {
        title: 'Click to sort',
        on: {
          click: () => {
            // First click on a numeric column sorts descending: "most hits",
            // "most abundant", "biggest fold change" are what you want from
            // those columns, whereas a name sorts naturally A-Z.
            query.descending = active ? !query.descending : column.type === 'number';
            query.sort = column.key;
            load({ keepScroll: false });
          },
        },
      }, [
        el('span', { text: column.label }),
        active ? el('span', { class: 'sort', text: query.descending ? ' ▼' : ' ▲' }) : null,
      ]));
      filterRow.append(el('th', {}, [filterControl(column)]));
    }
  }

  function filterControl(column) {
    if (column.type === 'text') {
      return el('input', {
        type: 'text', placeholder: 'contains...',
        value: (query.filters[column.key] || {}).text || '',
        on: { input: (event) => {
          query.filters[column.key] = { text: event.target.value };
          reload();
        } },
      });
    }
    if (column.type === 'number') {
      const current = query.filters[column.key] || {};
      const box = (which) => el('input', {
        type: 'number', placeholder: which, step: 'any', value: current[which] ?? '',
        on: { input: (event) => {
          const value = event.target.value === '' ? null : parseFloat(event.target.value);
          query.filters[column.key] = { ...query.filters[column.key], [which]: value };
          reload();
        } },
      });
      return el('div', { class: 'range-filter' }, [box('min'), box('max')]);
    }
    const options = (table && table.categories) || [];
    const chosen = new Set(((query.filters[column.key] || {}).allowed) || []);
    return el('select', {
      multiple: true, size: 1,
      on: { change: (event) => {
        const values = Array.from(event.target.selectedOptions).map((o) => o.value);
        query.filters[column.key] = values.length ? { allowed: values } : null;
        reload();
      } },
    }, options.map((token) => el('option', { value: token, text: token,
                                             selected: chosen.has(token) })));
  }

  /** Render only the rows currently in view, padded by two spacer rows. */
  function renderWindow() {
    if (!table) return;
    const rows = table.rows;
    const viewport = scroller.clientHeight || 400;
    const first = Math.max(0, Math.floor(scroller.scrollTop / ROW_HEIGHT) - OVERSCAN);
    const visible = Math.ceil(viewport / ROW_HEIGHT) + OVERSCAN * 2;
    const last = Math.min(rows.length, first + visible);
    const selected = store.get('selectedFeature');

    clear(body);
    topSpacer.style.height = `${first * ROW_HEIGHT}px`;
    bottomSpacer.style.height = `${Math.max(0, rows.length - last) * ROW_HEIGHT}px`;
    body.append(topSpacer);

    for (let i = first; i < last; i += 1) {
      const row = rows[i];
      const compound = row[0];
      body.append(el('tr', {
        class: compound === selected ? 'selected' : '',
        on: { click: () => store.set({ selectedFeature: compound }) },
      }, table.columns.map((column, index) => el('td', {
        class: column.type === 'number' ? 'num' : '',
        title: column.type === 'text' ? String(row[index] ?? '') : '',
        text: column.type === 'number' ? num(row[index], column.digits ?? 3)
                                       : (row[index] ?? ''),
      }))));
    }
    body.append(bottomSpacer);
  }

  function updateCounts() {
    const shown = table ? table.rows.length : 0;
    counts.textContent = query.include_filtered
      ? `${shown.toLocaleString()} features (all)`
      : `${shown.toLocaleString()} features passing all filters`;
  }

  async function load({ keepScroll = true } = {}) {
    const id = runId();
    if (!id) return;
    const scrollTop = keepScroll ? scroller.scrollTop : 0;
    try {
      // limit 0 == every row; this table is deliberately unpaged.
      table = await api.features(id, { ...query, limit: 0 });
    } catch (error) {
      clear(body).append(el('tr', {}, [el('td', { colSpan: 8 },
        [el('div', { class: 'notice error', text: error.message })])]));
      return;
    }
    rowIndex = new Map(table.rows.map((row, index) => [row[0], index]));
    buildHeader();
    buildToolbar();
    updateCounts();
    scroller.scrollTop = scrollTop;
    renderWindow();
  }

  const reload = debounce(() => load({ keepScroll: false }), 260);

  function buildToolbar() {
    clear(toolbar);
    toolbar.append(counts, el('span', { class: 'spacer' }));

    toolbar.append(el('label', { class: 'check',
                                 title: 'Features removed by the filters are hidden by default' }, [
      el('input', {
        type: 'checkbox', checked: query.include_filtered,
        on: { change: (event) => {
          query.include_filtered = event.target.checked;
          load({ keepScroll: false });
        } },
      }),
      el('span', { class: 'muted', text: 'Show filtered-out features' }),
    ]));

    toolbar.append(searchButton());
    toolbar.append(el('button', {
      class: 'btn small', type: 'button', text: 'Clear filters',
      on: { click: () => { query.filters = {}; load({ keepScroll: false }); } },
    }));
  }

  function searchButton() {
    if ((store.get('health') || {}).npatlas === false) {
      return el('span', { class: 'muted', text: 'NPAtlas not installed' });
    }
    const button = el('button', {
      class: 'btn small', type: 'button',
      text: table && table.searched ? 'Re-search NPAtlas' : 'Search NPAtlas',
    });
    button.addEventListener('click', () => runSearch(button));
    return button;
  }

  async function runSearch(button) {
    if (button) {
      button.disabled = true;
      button.textContent = 'Searching...';
    }
    try {
      const result = await api.dbsearch(runId());
      toast(`${result.matched} of ${result.searched} features matched within `
            + `${result.ppm_threshold} ppm`, 'ok');
      await load({ keepScroll: true });
    } catch (error) {
      toast(error.message, 'error');
      if (button) {
        button.disabled = false;
        button.textContent = 'Search NPAtlas';
      }
    }
  }

  /** Fill the Hits column the first time this tab is opened.
   *
   *  The desktop runs the full NPAtlas search when its Feature Info tab first
   *  appears, which is why its Hits column is never blank. Doing the same
   *  here removes the state where a feature's detail panel lists matches
   *  while the table's Hits column still reads "--". */
  async function autoSearch() {
    if (autoSearched || !table || table.searched) return;
    if ((store.get('health') || {}).npatlas === false) return;
    autoSearched = true;
    counts.textContent = 'Searching the Natural Products Atlas...';
    await runSearch(null);
  }

  // --- keyboard navigation ---------------------------------------------

  function moveSelection(delta) {
    if (!table || !table.rows.length) return;
    const current = store.get('selectedFeature');
    const at = rowIndex.has(current) ? rowIndex.get(current) : -1;
    jumpTo(Math.max(0, Math.min(table.rows.length - 1, at + delta)));
  }

  function jumpTo(index) {
    if (!table || !table.rows.length) return;
    store.set({ selectedFeature: table.rows[index][0] });
    scrollRowIntoView(index);
  }

  function scrollRowIntoView(index) {
    const headerHeight = tableEl.querySelector('thead')?.offsetHeight || 0;
    const top = index * ROW_HEIGHT;
    const viewTop = scroller.scrollTop;
    const viewBottom = viewTop + scroller.clientHeight - headerHeight - ROW_HEIGHT;
    if (top < viewTop) {
      scroller.scrollTop = top;
    } else if (top > viewBottom) {
      scroller.scrollTop = top - scroller.clientHeight + headerHeight + ROW_HEIGHT * 2;
    }
    renderWindow();
  }

  scroller.addEventListener('scroll', renderWindow, { passive: true });
  scroller.addEventListener('keydown', (event) => {
    // The filter inputs live inside this container; leave their keys alone.
    if (event.target !== scroller) return;
    const step = { ArrowDown: 1, ArrowUp: -1, PageDown: 20, PageUp: -20 }[event.key];
    if (step !== undefined) { event.preventDefault(); moveSelection(step); return; }
    if (event.key === 'Home') { event.preventDefault(); jumpTo(0); }
    if (event.key === 'End') { event.preventDefault(); jumpTo(table.rows.length - 1); }
  });

  // --- assembly ---------------------------------------------------------

  node.append(host);

  store.on('selectedFeature', () => {
    if (!node.classList.contains('active')) return;
    renderWindow();
  });

  return {
    id: 'features', label: 'Feature Info', group: 'Results', requiresRun: true,
    node, rail: true,
    onShow: async () => {
      await load({ keepScroll: false });
      await autoSearch();
      scroller.focus({ preventScroll: true });
    },
  };
}
