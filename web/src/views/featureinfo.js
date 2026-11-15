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
import { openSketcher } from './sketcher.js';
import { taxonPicker } from './taxonpicker.js';

const ROW_HEIGHT = 25;      // must match `table.features tbody tr` height in app.css
const OVERSCAN = 12;        // rows rendered beyond the viewport, each side

const runId = () => (store.get('run') || {}).run_id;

const DEFAULT_CHROMOPHORE_MIN = 14;

const emptyHitFilter = () => ({
  name: '', taxon: { level: 'genus', value: '' }, substructure: '',
  activity: [], chromophore_min: DEFAULT_CHROMOPHORE_MIN,
});

const hitFilterActive = (spec) => Boolean(spec.name.trim() || spec.taxon.value.trim()
  || spec.substructure || spec.activity.length);

/* Whether the filter panel was left open. Read through a guard: with storage
 * blocked (some privacy modes throw on any access) a bare read here stopped
 * the whole view from being built. */
const FILTERS_KEY = 'mpact.features.filters';
function filtersWereOpen() {
  try { return localStorage.getItem(FILTERS_KEY) !== 'closed'; } catch { return true; }
}
function rememberFilters(open) {
  try { localStorage.setItem(FILTERS_KEY, open ? 'open' : 'closed'); } catch { /* this visit only */ }
}

/* One listener closes whichever popup is open when a click lands outside it.
 * Each popup used to add its own to the document, and the header and hit bar
 * are rebuilt (on Clear, on a new run), so they accumulated. */
document.addEventListener('click', (event) => {
  for (const menu of document.querySelectorAll('.features-view .cat-menu:not(.hidden)')) {
    if (!menu.parentElement || !menu.parentElement.contains(event.target)) {
      menu.classList.add('hidden');
    }
  }
});

export function buildFeatureInfoView() {
  const node = el('div', { class: 'plot-view features-view' });

  // include_filtered false == only features that survived filtering, which is
  // what the desktop's tree lists.
  const query = { sort: 'm/z', descending: false, filters: {}, include_filtered: false,
                  ms2_only: false, hit_filter: emptyHitFilter() };
  let table = null;             // last response
  let rowIndex = new Map();     // compound -> row position, for keyboard nav
  let loadedRun = null;         // the run `table` belongs to
  let lastScroll = 0;           // where the list was when the view was last on screen
  // Runs whose one-off NPAtlas search has been attempted. Per run: this was
  // a single flag, so after the first run's automatic search no later run
  // ever got one and its Hits column stayed blank.
  const autoSearched = new Set();

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
  const hitBar = el('div', { class: 'hit-row' });
  const searchRow = el('div', { class: 'hit-row search-row' });
  const filterPanel = el('div', { class: 'plot-toolbar hit-filters' }, [hitBar, searchRow]);
  const tablePane = el('div', { class: 'table-pane' }, [toolbar, filterPanel, scroller]);
  const msmsOptions = { min_score: 0.5, match_precursor: true };
  let filtersOpen = filtersWereOpen();
  filterPanel.classList.toggle('hidden', !filtersOpen);

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

  /** A popup of three-state boxes, one per set: required, excluded, or
   *  unconstrained. Filters immediately and stays open. */
  function categoryFilter(column) {
    const saved = query.filters[column.key] || {};
    const state = new Map([
      ...(saved.required || saved.allowed || []).map((token) => [token, 'in']),
      ...(saved.excluded || []).map((token) => [token, 'out']),
    ]);
    const label = el('span', { class: 'cat-label' });
    const menu = el('div', { class: 'cat-menu hidden' });
    const button = el('button', {
      class: 'cat-button', type: 'button',
      on: { click: (event) => { event.stopPropagation(); toggle(); } },
    }, [label, el('span', { class: 'cat-caret', text: '▾' })]);
    const node = el('div', { class: 'cat-filter' }, [button, menu]);
    let tokens = [];

    const MARK = { in: '✓', out: '✗' };
    const NEXT = { undefined: 'in', in: 'out', out: undefined };

    function spec() {
      const pick = (wanted) => [...state].filter(([, v]) => v === wanted).map(([t]) => t);
      return state.size ? { required: pick('in'), excluded: pick('out') } : null;
    }

    function syncLabel() {
      if (state.size === 0) label.textContent = 'All';
      else if (state.size === 1) {
        const [[token, value]] = [...state];
        label.textContent = `${MARK[value]} ${token}`;
      } else label.textContent = `${state.size} selected`;
      node.classList.toggle('active', state.size > 0);
    }

    function apply() {
      query.filters[column.key] = spec();
      syncLabel();
      reload();
    }

    function toggle(open) {
      const show = open === undefined ? menu.classList.contains('hidden') : open;
      // Only one popup at a time.
      if (show) {
        for (const other of document.querySelectorAll('.cat-menu')) {
          if (other !== menu) other.classList.add('hidden');
        }
      }
      menu.classList.toggle('hidden', !show);
    }

    // (A click anywhere else closes it: the module's one document listener.)

    function refresh(categories) {
      const key = categories.join('\u0000');
      if (key === tokens.join('\u0000')) return;   // same tokens: leave the popup alone
      tokens = [...categories];

      // Drop a chosen token the run no longer has (a different run, say).
      let dropped = false;
      for (const token of [...state.keys()]) {
        if (!tokens.includes(token)) { state.delete(token); dropped = true; }
      }

      clear(menu);
      if (!tokens.length) {
        menu.append(el('div', { class: 'cat-empty muted', text: 'No sets in this run' }));
      }
      for (const token of tokens) {
        const box = el('span', { class: 'tri-box' });
        const paint = () => {
          const value = state.get(token);
          box.textContent = MARK[value] || '';
          box.className = `tri-box ${value || ''}`;
        };
        paint();
        menu.append(el('button', {
          class: 'cat-option tri-option', type: 'button',
          on: { click: () => {
            const next = NEXT[state.get(token)];
            if (next) state.set(token, next);
            else state.delete(token);
            paint();
            apply();
          } },
        }, [box, el('span', { text: token })]));
      }
      if (dropped) query.filters[column.key] = spec();
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
    counts.textContent = `${shown.toLocaleString()} features`;
  }

  let loadToken = 0;

  async function load({ keepScroll = true } = {}) {
    const id = runId();
    if (!id) return;
    const mine = ++loadToken;
    if (!hitBar.childElementCount) buildHitBar();
    const scrollTop = keepScroll ? scroller.scrollTop : 0;
    try {
      // limit 0 == every row; this table is deliberately unpaged.
      const response = await api.features(id, {
        ...query, limit: 0,
        hit_filter: hitFilterActive(query.hit_filter) ? query.hit_filter : null,
      });
      if (mine !== loadToken) return;
      table = response;
      loadedRun = id;
    } catch (error) {
      if (mine !== loadToken) return;
      loadedRun = null;
      clear(body).append(el('tr', {}, [el('td', { colSpan: 8 },
        [el('div', { class: 'notice error', text: error.message })])]));
      return;
    }
    rowIndex = new Map(table.rows.map((row, index) => [row[0], index]));
    // The same filter as last time stays the same object: the details rail
    // re-requests a feature's matches whenever this changes, and a fresh
    // copy on every reload made it do so for each sort click and keystroke.
    const hitFilter = table.hit_filter ? structuredClone(query.hit_filter) : null;
    const previous = store.get('hitFilter');
    store.set({
      hitFilter: JSON.stringify(previous) === JSON.stringify(hitFilter) ? previous : hitFilter,
      plotFilter: { ...store.get('plotFilter'), run: id, ids: new Set(rowIndex.keys()) },
    });
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

    if (table && table.has_fragments) {
      toolbar.append(el('label', { class: 'check' }, [
        el('input', {
          type: 'checkbox', checked: query.ms2_only,
          on: { change: (event) => {
            query.ms2_only = event.target.checked;
            load({ keepScroll: false });
          } },
        }),
        el('span', { class: 'muted', text: 'MS/MS required' }),
      ]));
    }

    toolbar.append(el('label', { class: 'check' }, [
      el('input', {
        type: 'checkbox', checked: store.get('plotFilter').active,
        on: { change: (event) => {
          store.set({ plotFilter: { ...store.get('plotFilter'), active: event.target.checked } });
        } },
      }),
      el('span', { class: 'muted', text: 'Current selection only' }),
    ]));

    toolbar.append(el('button', {
      class: 'collapse-toggle', type: 'button', text: filtersOpen ? '▾' : '▸',
      on: { click: (event) => {
        filtersOpen = !filtersOpen;
        rememberFilters(filtersOpen);
        filterPanel.classList.toggle('hidden', !filtersOpen);
        event.currentTarget.textContent = filtersOpen ? '▾' : '▸';
      } },
    }));
    buildSearchRow();
  }

  function clearFilters() {
    query.filters = {};
    query.ms2_only = false;
    query.hit_filter = emptyHitFilter();
    buildHeader();
    buildHitBar();
    load({ keepScroll: false });
  }

  function buildSearchRow() {
    clear(searchRow);
    const health = store.get('health') || {};
    if (health.npatlas !== false) searchRow.append(searchButton());
    if (health.msms && table && table.has_fragments) {
      const button = el('button', {
        class: 'btn small', type: 'button',
        text: table.msms_searched ? 'Re-search MS/MS' : 'Search MS/MS',
        on: { click: () => runMsmsSearch(button) },
      });
      searchRow.append(
        button,
        el('label', {}, [el('span', { text: 'Cosine ≥' }), el('input', {
          type: 'number', class: 'cosine-min', min: 0.05, max: 1, step: 0.05,
          value: msmsOptions.min_score,
          on: { change: (event) => {
            const value = parseFloat(event.target.value);
            if (Number.isNaN(value) || value <= 0 || value > 1) {
              event.target.value = msmsOptions.min_score;
              return;
            }
            msmsOptions.min_score = value;
          } },
        })]),
        el('label', { class: 'check' }, [
          el('input', { type: 'checkbox', checked: msmsOptions.match_precursor,
                        on: { change: (event) => { msmsOptions.match_precursor = event.target.checked; } } }),
          el('span', { text: 'Match precursor' }),
        ]),
      );
    }
    searchRow.append(el('span', { class: 'spacer' }), el('button', {
      class: 'btn small', type: 'button', text: 'Clear', on: { click: clearFilters },
    }));
  }

  // --- hit filters ------------------------------------------------------
  // A feature passes when one of its hits meets every hit criterion.

  function buildHitBar() {
    clear(hitBar);
    const health = store.get('health') || {};
    const spec = query.hit_filter;
    if (health.npatlas === false) return;
    const chemistry = health.chemistry !== false;

    const name = el('input', {
      type: 'text', class: 'hit-name', placeholder: 'contains...', value: spec.name,
      on: { input: (event) => { spec.name = event.target.value; reload(); } },
    });
    hitBar.append(el('label', {}, [el('span', { text: 'Hit name' }), name]));
    hitBar.append(taxonFilter(spec));
    if (chemistry) hitBar.append(substructureFilter(spec));
    hitBar.append(activityFilter(spec, chemistry));
  }

  function taxonFilter(spec) {
    const picker = taxonPicker({
      level: spec.taxon.level, value: spec.taxon.value,
      suggest: async (level, text) => (await api.taxa(runId(), level, text)).names,
      onChange: (level, value) => {
        spec.taxon = { level, value };
        load({ keepScroll: false });
      },
    });
    return el('div', { class: 'hit-field taxon-filter' }, [el('span', { text: 'Taxon' }), picker.node]);
  }

  function substructureFilter(spec) {
    const label = el('span', { class: 'cat-label' });
    const node = el('div', { class: 'cat-filter substructure-filter' });
    const remove = el('button', {
      class: 'cat-clear', type: 'button', text: '×',
      on: { click: () => { spec.substructure = ''; sync(); load({ keepScroll: false }); } },
    });
    const sync = () => {
      label.textContent = 'Draw';
      button.title = spec.substructure;
      node.classList.toggle('active', Boolean(spec.substructure));
      remove.classList.toggle('hidden', !spec.substructure);
    };
    const button = el('button', {
      class: 'cat-button', type: 'button',
      on: { click: async () => {
        const result = await openSketcher(runId(), spec.substructure);
        if (result === null || result === spec.substructure) return;
        spec.substructure = result;
        sync();
        load({ keepScroll: false });
      } },
    }, [label]);
    node.append(button, remove);
    sync();
    return el('div', { class: 'hit-field' }, [el('span', { text: 'Substructure' }), node]);
  }

  function activityFilter(spec, chemistry) {
    const options = chemistry
      ? [['electrophile', 'Electrophile'], ['siderophore', 'Siderophore'],
         ['chromophore', 'Chromophore']]
      : [['siderophore', 'Siderophore']];
    const label = el('span', { class: 'cat-label' });
    const menu = el('div', { class: 'cat-menu hidden' });
    const node = el('div', { class: 'cat-filter activity-filter' });
    const sync = () => {
      const names = options.filter(([key]) => spec.activity.includes(key)).map(([, text]) => text);
      label.textContent = names.length ? names.join(', ') : 'Any';
      node.classList.toggle('active', names.length > 0);
    };
    const button = el('button', {
      class: 'cat-button', type: 'button',
      on: { click: (event) => {
        event.stopPropagation();
        const show = menu.classList.contains('hidden');
        for (const other of document.querySelectorAll('.cat-menu')) other.classList.add('hidden');
        menu.classList.toggle('hidden', !show);
      } },
    }, [label, el('span', { class: 'cat-caret', text: '▾' })]);

    for (const [key, text] of options) {
      const row = el('label', { class: 'cat-option' }, [
        el('input', {
          type: 'checkbox', checked: spec.activity.includes(key),
          on: { change: (event) => {
            spec.activity = event.target.checked
              ? [...spec.activity, key] : spec.activity.filter((item) => item !== key);
            sync();
            load({ keepScroll: false });
          } },
        }),
        el('span', { text }),
      ]);
      if (key === 'chromophore') {
        row.append(el('span', { class: 'muted', text: '≥' }), el('input', {
          type: 'number', class: 'conj-min', min: 2, max: 200, step: 1,
          value: spec.chromophore_min,
          on: { change: (event) => {
            const value = parseInt(event.target.value, 10);
            if (Number.isNaN(value)) { event.target.value = spec.chromophore_min; return; }
            spec.chromophore_min = value;
            if (spec.activity.includes('chromophore')) load({ keepScroll: false });
          } },
        }), el('span', { class: 'muted', text: 'conjugated atoms' }));
      }
      menu.append(row);
    }
    node.append(button, menu);
    sync();
    return el('div', { class: 'hit-field' }, [el('span', { text: 'Activity' }), node]);
  }


  function searchButton() {
    if ((store.get('health') || {}).npatlas === false) {
      return el('span', { class: 'muted', text: 'NPAtlas not installed' });
    }
    const button = el('button', {
      class: 'btn small', type: 'button',
      text: table && table.searched ? 'Re-search MS1' : 'Search MS1',
    });
    button.addEventListener('click', () => runSearch(button));
    return button;
  }

  async function runSearch(button) {
    if (button) button.disabled = true;
    store.set({ busy: 'Searching MS1' });
    try {
      const result = await api.dbsearch(runId());
      toast(`MS1: ${result.matched} of ${result.searched} features matched`, 'ok');
      await load({ keepScroll: true });
    } catch (error) {
      toast(error.message, 'error');
      if (button) button.disabled = false;
    } finally {
      store.set({ busy: null });
    }
  }

  async function runMsmsSearch(button) {
    button.disabled = true;
    store.set({ busy: 'Searching MS/MS' });
    try {
      const result = await api.msmsSearch(runId(), msmsOptions);
      toast(`MS/MS: ${result.matched} of ${result.searched} features matched`, 'ok');
      store.set({ msmsVersion: (store.get('msmsVersion') || 0) + 1 });
      await load({ keepScroll: true });
    } catch (error) {
      toast(error.message, 'error');
      button.disabled = false;
    } finally {
      store.set({ busy: null });
    }
  }

  /** Fill the Hits column the first time this tab is opened.
   *
   *  The desktop runs the full NPAtlas search when its Feature Info tab first
   *  appears, which is why its Hits column is never blank. Doing the same
   *  here removes the state where a feature's detail panel lists matches
   *  while the table's Hits column still reads "--". */
  async function autoSearch() {
    const id = runId();
    if (!id || loadedRun !== id || !table || table.searched || autoSearched.has(id)) return;
    if ((store.get('health') || {}).npatlas === false) return;
    autoSearched.add(id);
    await runSearch(null);
  }

  /** Bring the selected feature's row into view, if it is in the list. */
  function revealSelection() {
    const selected = store.get('selectedFeature');
    if (selected && rowIndex.has(selected)) scrollRowIntoView(rowIndex.get(selected));
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

  scroller.addEventListener('scroll', () => {
    if (node.classList.contains('active')) lastScroll = scroller.scrollTop;
    renderWindow();
  }, { passive: true });
  scroller.addEventListener('keydown', (event) => {
    // The filter inputs live inside this container; leave their keys alone.
    if (event.target !== scroller) return;
    const step = { ArrowDown: 1, ArrowUp: -1, PageDown: 20, PageUp: -20 }[event.key];
    if (step !== undefined) { event.preventDefault(); moveSelection(step); return; }
    if (event.key === 'Home') { event.preventDefault(); jumpTo(0); }
    if (event.key === 'End' && table) { event.preventDefault(); jumpTo(table.rows.length - 1); }
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
      if (table && loadedRun === runId()) {
        // Same run, and everything that changes the table (a filter, a
        // search) reloads it itself -- so coming back shows the list as it
        // was left instead of fetching every row again and jumping to the top.
        buildToolbar();
        scroller.scrollTop = lastScroll;
        renderWindow(true);
      } else {
        lastScroll = 0;
        await load({ keepScroll: false });
      }
      // A feature picked on a plot is the reason for coming here.
      revealSelection();
      await autoSearch();
      scroller.focus({ preventScroll: true });
    },
  };
}
