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

  // Header controls are built once and then only refreshed in place.
  // Rebuilding them on every reload destroyed whatever the user was typing
  // in or clicking on -- which is what made the compound box lose focus after
  // one character, and the Sets list impossible to tick more than once.
  const sortMarks = new Map();      // column key -> the arrow span
  const filterControls = new Map(); // column key -> {refresh?}
  let headerColumns = null;         // the column list the header was built for

  function buildHeader() {
    clear(headRow);
    clear(filterRow);
    sortMarks.clear();
    filterControls.clear();

    for (const column of table.columns) {
      // The marker only carries the `sort` class while it is actually
      // indicating the sort, so "the sorted column" stays a single selector.
      const mark = el('span');
      sortMarks.set(column.key, mark);
      headRow.append(el('th', {
        title: 'Click to sort',
        on: {
          click: () => {
            // First click on a numeric column sorts descending: "most hits",
            // "most abundant", "biggest fold change" are what you want from
            // those columns, whereas a name sorts naturally A-Z.
            const active = query.sort === column.key;
            query.descending = active ? !query.descending : column.type === 'number';
            query.sort = column.key;
            load({ keepScroll: false });
          },
        },
      }, [el('span', { text: column.label }), mark]));

      const control = filterControl(column);
      filterControls.set(column.key, control);
      filterRow.append(el('th', {}, [control.node]));
    }
    headerColumns = table.columns.map((column) => column.key).join('\u0000');
    refreshHeader();
  }

  /** Update what a reload can legitimately change -- the sort arrow and the
   *  set of category tokens -- without touching the controls themselves. */
  function refreshHeader() {
    for (const [key, mark] of sortMarks) {
      const active = query.sort === key;
      mark.className = active ? 'sort' : '';
      mark.textContent = active ? (query.descending ? ' ▼' : ' ▲') : '';
    }
    for (const control of filterControls.values()) {
      if (control.refresh) control.refresh(table.categories || []);
    }
  }

  function filterControl(column) {
    if (column.type === 'text') {
      return { node: el('input', {
        type: 'text', placeholder: 'contains...',
        value: (query.filters[column.key] || {}).text || '',
        on: { input: (event) => {
          query.filters[column.key] = { text: event.target.value };
          reload();
        } },
      }) };
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
      return { node: el('div', { class: 'range-filter' }, [box('min'), box('max')]) };
    }
    return categoryFilter(column);
  }

  /** A checkbox popup, the web form of the desktop's category filter (a
   *  QToolButton whose menu holds one checkable action per token). Ticking a
   *  box filters immediately and leaves the popup open, so several sets can
   *  be picked in a row. */
  function categoryFilter(column) {
    const chosen = new Set(((query.filters[column.key] || {}).allowed) || []);
    const label = el('span', { class: 'cat-label' });
    const menu = el('div', { class: 'cat-menu hidden' });
    const button = el('button', {
      class: 'cat-button', type: 'button',
      on: { click: (event) => { event.stopPropagation(); toggle(); } },
    }, [label, el('span', { class: 'cat-caret', text: '▾' })]);
    const node = el('div', { class: 'cat-filter' }, [button, menu]);
    let tokens = [];

    function syncLabel() {
      label.textContent = chosen.size === 0 ? 'All'
        : chosen.size === 1 ? [...chosen][0]
        : `${chosen.size} selected`;
      node.classList.toggle('active', chosen.size > 0);
    }

    function apply() {
      query.filters[column.key] = chosen.size ? { allowed: [...chosen] } : null;
      syncLabel();
      reload();
    }

    function toggle(open) {
      const show = open === undefined ? menu.classList.contains('hidden') : open;
      // Only one popup at a time.
      for (const other of document.querySelectorAll('.cat-menu')) {
        if (other !== menu) other.classList.add('hidden');
      }
      menu.classList.toggle('hidden', !show);
    }

    // A click anywhere else closes it, the way a menu should behave.
    document.addEventListener('click', (event) => {
      if (!node.contains(event.target)) toggle(false);
    });

    function refresh(categories) {
      const key = categories.join('\u0000');
      if (key === tokens.join('\u0000')) return;   // same tokens: leave the popup alone
      tokens = [...categories];

      // Drop a chosen token the run no longer has (a different run, say).
      let dropped = false;
      for (const token of [...chosen]) {
        if (!tokens.includes(token)) { chosen.delete(token); dropped = true; }
      }

      clear(menu);
      if (!tokens.length) {
        menu.append(el('div', { class: 'cat-empty muted', text: 'No sets in this run' }));
      }
      for (const token of tokens) {
        menu.append(el('label', { class: 'cat-option' }, [
          el('input', {
            type: 'checkbox', checked: chosen.has(token),
            on: { change: (event) => {
              if (event.target.checked) chosen.add(token);
              else chosen.delete(token);
              apply();
            } },
          }),
          el('span', { text: token }),
        ]));
      }
      if (dropped) {
        query.filters[column.key] = chosen.size ? { allowed: [...chosen] } : null;
      }
      syncLabel();
    }

    syncLabel();
    return { node, refresh };
  }

  let drawnWindow = null;   // "first:last:selected" currently in the DOM

  /** Render only the rows currently in view, padded by two spacer rows. */
  function renderWindow(force = false) {
    if (!table) return;
    const rows = table.rows;
    const viewport = scroller.clientHeight || 400;
    const first = Math.max(0, Math.floor(scroller.scrollTop / ROW_HEIGHT) - OVERSCAN);
    const visible = Math.ceil(viewport / ROW_HEIGHT) + OVERSCAN * 2;
    const last = Math.min(rows.length, first + visible);
    const selected = store.get('selectedFeature');

    // Scrolling fires far more often than the window moves: without this,
    // every scroll event rebuilt ~40 rows of ~7 cells each for nothing.
    const signature = `${first}:${last}:${selected}:${rows.length}`;
    if (!force && signature === drawnWindow) return;
    drawnWindow = signature;

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
    const columns = table.columns.map((column) => column.key).join('\u0000');
    if (columns !== headerColumns) buildHeader();
    else refreshHeader();
    buildToolbar();
    updateCounts();
    scroller.scrollTop = scrollTop;
    renderWindow(true);
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

  scroller.addEventListener('scroll', () => renderWindow(), { passive: true });
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
