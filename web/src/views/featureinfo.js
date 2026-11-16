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
 * Rows are windowed twice over. In the page: at a few thousand features a
 * full DOM table costs tens of thousands of nodes and makes every keystroke
 * stutter, so only the visible slice is rendered between two spacer rows.
 * And over the wire: the server sends every feature's id, in order, but the
 * rows themselves only a page at a time as they scroll into view -- with
 * filtered-out features shown, a class dataset is thirty thousand rows. The
 * list stays logically complete all the same: scroll position, selection,
 * range selection and keyboard navigation address the whole set through the
 * ids.
 *
 * Two narrow columns lead each row. "Sel" ticks the selected features and
 * "Flag" shows a feature's flag, a lasting coloured mark (see flags.js).
 * Clicking either header lists those features first -- that ordering is done
 * here, over the id list, which is why rows are fetched by id rather than by
 * position: the rows on screen need not be neighbours in the server's order.
 * Under each, in the filter row, is that column's filter: a checkbox that
 * lists only the selected features, and a popup that lists features by flag
 * (each colour can be required or excluded, as in the Sets filter). These two
 * are applied here as well, since the selection and the flags live here.
 *
 * Selection is multiple, as in a file list: click selects one, Ctrl-click
 * adds or removes one, Shift-click (or Shift with the arrow keys) selects a
 * range, Ctrl+A everything listed. The same selection is highlighted on
 * every plot and can be exported.
 *
 * Per-column filters are evaluated server-side by the vendored `treefilters`
 * module, so a filter behaves exactly as it does on the desktop.
 */

import { api } from '../api.js';
import { exportFeatures, exportSelected } from '../export.js';
import { flagControls, flags, showFlagsToggle, toggleFlag } from '../flags.js';
import { selection, store } from '../state.js';
import { clear, debounce, el, num, settle, toast } from '../util.js';
import { openSketcher } from './sketcher.js';
import { taxonPicker } from './taxonpicker.js';

const ROW_HEIGHT = 25;      // must match `table.features tbody tr` height in app.css
const OVERSCAN = 12;        // rows rendered beyond the viewport, each side
const PAGE = 200;           // rows that come with the id list
const PREFETCH = 60;        // rows fetched beyond the rendered window, each side
const MAX_CACHED = 20000;   // rows kept; past it, those far from view are dropped
const LEAD_COLUMNS = 2;     // Sel and Flag, ahead of the server's columns
const NO_FLAG = 'none';     // the Flag filter's entry for unflagged features

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
  let table = null;             // last response (its flags and columns)
  let serverIds = [];           // every listed compound, in the server's order
  let ids = [];                 // the same, in the order shown (see `pin`)
  let rowsById = new Map();     // compound -> its row, for those fetched so far
  const pendingIds = new Set();
  // Which features are listed first: null, or {key: 'sel' | 'flag', stamp}.
  // `stamp` is the selection (or flags) the order was made from, so a click
  // on the header can tell "order is out of date" from "turn it off".
  let pin = null;
  // The Sel and Flag filters. `flag` maps a flag colour (or NO_FLAG) to
  // 'in' (list only these) or 'out' (leave these out).
  const local = { selectedOnly: false, flag: new Map() };
  const localActive = () => local.selectedOnly || local.flag.size > 0;
  let filteredFor = null;       // the selection the Sel filter was last applied to
  let filtersRun = null;        // the run these filters and the pin were set for
  let flagFilterControl = null;
  let orderVersion = 0;         // bumped when the shown order changes
  let flagsVersion = 0;         // bumped when the flags change
  let flagBar = null;
  let loadedOptions = null;     // the options the ids and rows were fetched with
  let rowIndex = new Map();     // compound -> row position, for keyboard nav
  let anchor = null;            // row a Shift-click or Shift-arrow extends from
  let selectionVersion = 0;     // bumped when the selection changes
  let rowsVersion = 0;          // bumped when rows arrive
  let exportSelectedButton = null;
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

  const PINS = [['sel', 'Sel', 'Selected features: click to list them first'],
                ['flag', 'Flag', 'Flagged features: click to list them first, by colour']];
  const pinMarks = new Map();       // 'sel' | 'flag' -> the arrow span

  function buildHeader() {
    clear(headRow);
    clear(filterRow);
    sortMarks.clear();
    filterControls.clear();
    pinMarks.clear();

    for (const [key, label, title] of PINS) {
      const mark = el('span');
      pinMarks.set(key, mark);
      headRow.append(el('th', { class: `col-${key}`, title, on: { click: () => togglePin(key) } },
                        [el('span', { text: label }), mark]));
      filterRow.append(el('th', { class: `col-${key}` }, [key === 'sel' ? selectedOnlyBox()
                                                                         : flagFilter().node]));
    }

    table.columns.forEach((column, position) => {
      // The marker only carries the `sort` class while it is actually
      // indicating the sort, so "the sorted column" stays a single selector.
      const mark = el('span');
      sortMarks.set(column.key, mark);
      headRow.append(el('th', {
        // Numbers are right-aligned in the body, so their headings are too.
        class: `col-${position}${column.type === 'number' ? ' num' : ''}`,
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
    });
    headerColumns = table.columns.map((column) => column.key).join('\u0000');
    refreshHeader();
  }

  /** Update what a reload can legitimately change -- the sort arrows and the
   *  set of category tokens -- without touching the controls themselves. */
  function refreshHeader() {
    for (const [key, mark] of sortMarks) {
      const active = query.sort === key;
      mark.className = active ? 'sort' : '';
      mark.textContent = active ? (query.descending ? ' ▼' : ' ▲') : '';
    }
    // Both can show at once: the pinned features come first, and the column
    // sort orders the rows within each part.
    for (const [key, mark] of pinMarks) {
      const active = Boolean(pin && pin.key === key);
      mark.className = active ? 'sort' : '';
      mark.textContent = active ? '▼' : '';
    }
    for (const control of filterControls.values()) {
      if (control.refresh) control.refresh(table.categories || []);
    }
  }

  /** The Sel filter: a checkbox that lists only the selected features. */
  function selectedOnlyBox() {
    return el('input', {
      type: 'checkbox', class: 'sel-only', checked: local.selectedOnly,
      title: 'List only the selected features',
      on: { change: (event) => {
        local.selectedOnly = event.target.checked;
        refilter({ toTop: true });
        scroller.focus({ preventScroll: true });
      } },
    });
  }

  /** The Flag filter: a popup of the flag colours in use, each a three-state
   *  box as in the Sets filter -- ticked lists only features with that flag
   *  (several ticked: any of them, since a feature has one flag), crossed
   *  leaves them out. "No flag" stands for the unflagged features. */
  function flagFilter() {
    const state = local.flag;
    const label = el('span', { class: 'cat-label' });
    const menu = el('div', { class: 'cat-menu flag-menu hidden' });
    const button = el('button', {
      class: 'cat-button', type: 'button',
      title: 'List features by flag: tick a colour to list only those, cross it to leave them out',
      on: { click: (event) => {
        event.stopPropagation();
        const show = menu.classList.contains('hidden');
        for (const other of document.querySelectorAll('.cat-menu')) other.classList.add('hidden');
        menu.classList.toggle('hidden', !show);
      } },
    }, [label, el('span', { class: 'cat-caret', text: '▾' })]);
    const node = el('div', { class: 'cat-filter flag-filter' }, [button, menu]);

    const MARK = { in: '✓', out: '✗' };
    const NEXT = { undefined: 'in', in: 'out', out: undefined };

    const syncLabel = () => {
      label.textContent = state.size ? String(state.size) : 'All';
      node.classList.toggle('active', state.size > 0);
    };

    /** Rebuild the entries for the colours now in use. Returns true if a
     *  colour that was being filtered on has gone (its last flag removed). */
    function refresh() {
      const counts = new Map();
      for (const colour of flags.map().values()) counts.set(colour, (counts.get(colour) || 0) + 1);
      let dropped = false;
      for (const key of [...state.keys()]) {
        if (key !== NO_FLAG && !counts.has(key)) { state.delete(key); dropped = true; }
      }

      clear(menu);
      if (!counts.size) {
        menu.append(el('div', { class: 'cat-empty muted', text: 'Nothing is flagged yet' }));
      }
      const entries = counts.size ? [...counts, [NO_FLAG, null]] : [];
      for (const [key, count] of entries) {
        const box = el('span', { class: 'tri-box' });
        const paint = () => {
          const value = state.get(key);
          box.textContent = MARK[value] || '';
          box.className = `tri-box ${value || ''}`;
        };
        paint();
        menu.append(el('button', {
          class: 'cat-option tri-option', type: 'button',
          on: { click: () => {
            const next = NEXT[state.get(key)];
            if (next) state.set(key, next);
            else state.delete(key);
            paint();
            syncLabel();
            refilter({ toTop: true });
          } },
        }, [box,
            key === NO_FLAG ? el('span', { text: 'No flag' })
                            : el('span', { class: 'flag-dot', style: { background: key } }),
            count === null ? null : el('span', { class: 'muted', text: count.toLocaleString() })]));
      }
      syncLabel();
      return dropped;
    }

    refresh();
    flagFilterControl = { refresh };
    return { node };
  }

  /** Put `ids` in the order shown: the server's list, cut down by the Sel and
   *  Flag filters, with the pinned features (selected, or flagged grouped by
   *  colour) moved to the front. Each part keeps the server's order, so the
   *  column sort still applies within it. */
  function arrange() {
    let listed = serverIds;
    if (local.selectedOnly) {
      const chosen = selection.asSet();
      listed = listed.filter((compound) => chosen.has(compound));
    }
    filteredFor = store.get('selectedFeatures');
    if (local.flag.size) {
      const map = flags.map();
      const only = new Set([...local.flag].filter(([, value]) => value === 'in').map(([key]) => key));
      const without = new Set([...local.flag].filter(([, value]) => value === 'out').map(([key]) => key));
      listed = listed.filter((compound) => {
        const colour = map.get(compound) || NO_FLAG;
        return !without.has(colour) && (!only.size || only.has(colour));
      });
    }

    if (!pin) {
      ids = listed;
    } else if (pin.key === 'sel') {
      const chosen = selection.asSet();
      ids = [...listed.filter((compound) => chosen.has(compound)),
             ...listed.filter((compound) => !chosen.has(compound))];
      pin.stamp = store.get('selectedFeatures');
    } else {
      const map = flags.map();
      const byColour = new Map();
      const rest = [];
      for (const compound of listed) {
        const colour = map.get(compound);
        if (!colour) { rest.push(compound); continue; }
        if (!byColour.has(colour)) byColour.set(colour, []);
        byColour.get(colour).push(compound);
      }
      ids = [...[...byColour.values()].flat(), ...rest];
      pin.stamp = store.get('flags');
    }
    rowIndex = new Map(ids.map((compound, index) => [compound, index]));
    // Row positions have changed under the anchor; restart it at the focus.
    anchor = rowIndex.get(store.get('selectedFeature')) ?? null;
    orderVersion += 1;
  }

  /** Apply the Sel and Flag filters again: one of them changed, or what
   *  they test did. The plots' "table rows only" follows the list. */
  function refilter({ toTop = false } = {}) {
    if (!table) return;
    arrange();
    store.set({ plotFilter: { ...store.get('plotFilter'), run: loadedRun, ids: new Set(ids) } });
    updateCounts();
    if (toTop) scroller.scrollTop = 0;
    renderWindow(true);
  }

  /** A click on the Sel or Flag heading. The order is made when asked for,
   *  not kept live: a row that jumped to the top the moment it was ticked
   *  would be gone from under the pointer. So if the selection (or the flags)
   *  has changed since, the click brings the order up to date; otherwise it
   *  turns the pin off. */
  function togglePin(key) {
    if (!table) return;
    const current = key === 'sel' ? store.get('selectedFeatures') : store.get('flags');
    const upToDate = pin && pin.key === key && pin.stamp === current;
    pin = upToDate ? null : { key, stamp: null };
    arrange();
    refreshHeader();
    scroller.scrollTop = 0;
    renderWindow(true);
    scroller.focus({ preventScroll: true });
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

  let drawnWindow = null;   // signature of what is currently in the DOM

  const rowAt = (index) => rowsById.get(ids[index]);

  const requestOptions = () => ({
    ...query, hit_filter: hitFilterActive(query.hit_filter) ? query.hit_filter : null,
  });

  /** [first, last) of the rows to have in the DOM. */
  function windowBounds() {
    const viewport = scroller.clientHeight || 400;
    const first = Math.max(0, Math.floor(scroller.scrollTop / ROW_HEIGHT) - OVERSCAN);
    const visible = Math.ceil(viewport / ROW_HEIGHT) + OVERSCAN * 2;
    return [first, Math.min(ids.length, first + visible)];
  }

  /** Render only the rows currently in view, padded by two spacer rows. A
   *  row that has not arrived yet shows its id and placeholders. */
  function renderWindow(force = false) {
    if (!table) return;
    const [first, last] = windowBounds();

    // Scrolling fires far more often than the window moves: without this,
    // every scroll event rebuilt ~40 rows of ~7 cells each for nothing.
    const signature = [first, last, selectionVersion, ids.length, rowsVersion,
                       orderVersion, flagsVersion].join(':');
    if (!force && signature === drawnWindow) return;
    drawnWindow = signature;

    const selected = selection.asSet();
    const flagged = flags.map();
    const focused = store.get('selectedFeature');
    clear(body);
    if (!ids.length) {
      // Say why, rather than show a bare header: with "selected only" on and
      // nothing selected, an empty table looks like a failure.
      const why = local.selectedOnly && !selection.count()
        ? 'No features are selected. Untick the box under Sel to list them all.'
        : localActive() ? 'No listed feature passes the Sel and Flag filters.'
                        : 'No features match the filters.';
      body.append(el('tr', { class: 'empty-row' },
                     [el('td', { colSpan: table.columns.length + LEAD_COLUMNS, text: why })]));
      return;
    }
    topSpacer.style.height = `${first * ROW_HEIGHT}px`;
    bottomSpacer.style.height = `${Math.max(0, ids.length - last) * ROW_HEIGHT}px`;
    body.append(topSpacer);

    let missing = false;
    for (let i = first; i < last; i += 1) {
      const compound = ids[i];
      const row = rowAt(i);
      if (!row) missing = true;
      const isSelected = selected.has(compound);
      const flag = flagged.get(compound);
      const classes = [isSelected ? 'selected' : '',
                       compound === focused ? 'focused' : '',
                       row ? '' : 'pending'].filter(Boolean).join(' ');
      body.append(el('tr', { class: classes, dataset: { index: i } }, [
        el('td', { class: 'col-sel', text: isSelected ? '✓' : '',
                   title: isSelected ? 'Selected: click to deselect' : 'Click to add to the selection' }),
        el('td', { class: 'col-flag', title: flag ? 'Flagged: click to unflag' : 'Click to flag' },
           flag ? [el('span', { class: 'flag-dot', style: { background: flag } })] : []),
        ...table.columns.map((column, index) => {
          if (!row) return el('td', { text: index === 0 ? compound : '…' });
          return el('td', {
            class: column.type === 'number' ? 'num' : '',
            title: column.type === 'text' ? String(row[index] ?? '') : '',
            text: column.type === 'number' ? num(row[index], column.digits ?? 3)
                                           : (row[index] ?? ''),
          });
        }),
      ]));
    }
    body.append(bottomSpacer);
    if (missing) fetchVisible();
  }

  /** Fetch the rows the window needs, and a margin around it, by id. Settled,
   *  so dragging the scrollbar through thirty thousand rows asks only for
   *  where it comes to rest. */
  const fetchVisible = settle(() => {
    const id = runId();
    if (!id || !loadedOptions || loadedRun !== id) return;
    const mine = loadToken;
    const [first, last] = windowBounds();
    const from = Math.max(0, first - PREFETCH);
    const to = Math.min(ids.length, last + PREFETCH);
    const wanted = [];
    for (let i = from; i < to; i += 1) {
      if (!rowsById.has(ids[i]) && !pendingIds.has(ids[i])) wanted.push(ids[i]);
    }
    if (!wanted.length) return;
    for (const compound of wanted) pendingIds.add(compound);
    api.features(id, { ...loadedOptions, rows_for: wanted }).then((response) => {
      if (mine !== loadToken) return;      // the list has been reloaded since
      for (const compound of wanted) pendingIds.delete(compound);
      for (const row of response.rows) rowsById.set(row[0], row);
      // An id the server no longer lists: leave its row blank, don't ask again.
      for (const compound of wanted) if (!rowsById.has(compound)) rowsById.set(compound, [compound]);
      if (rowsById.size > MAX_CACHED) {
        const [near] = windowBounds();
        const keep = new Set(ids.slice(Math.max(0, near - 2000), near + 2000));
        for (const compound of [...rowsById.keys()]) {
          if (!keep.has(compound)) rowsById.delete(compound);
        }
      }
      rowsVersion += 1;
      renderWindow();
    }).catch(() => {
      if (mine === loadToken) for (const compound of wanted) pendingIds.delete(compound);
    });
  }, 90);

  function updateCounts() {
    const chosen = selection.count();
    // "12 of 977" when the Sel or Flag filter is cutting the list down.
    const listed = ids.length === serverIds.length ? ids.length.toLocaleString()
      : `${ids.length.toLocaleString()} of ${serverIds.length.toLocaleString()}`;
    counts.textContent = `${listed} features`
      + (chosen > 1 ? ` · ${chosen.toLocaleString()} selected` : '');
    if (exportSelectedButton) {
      exportSelectedButton.classList.toggle('hidden', chosen < 1);
      exportSelectedButton.textContent = chosen > 1 ? `Export selected (${chosen.toLocaleString()})`
                                                    : 'Export selected';
    }
    if (flagBar) flagBar.sync();
  }

  let loadToken = 0;

  async function load({ keepScroll = true } = {}) {
    const id = runId();
    if (!id) return;
    const mine = ++loadToken;
    if (filtersRun !== id) {
      // Another run: its selection is empty and its flags are its own, so the
      // Sel and Flag filters (and the pin) do not carry over.
      if (filtersRun !== null) {
        local.selectedOnly = false;
        local.flag.clear();
        pin = null;
        headerColumns = null;       // rebuild the header so its controls agree
      }
      filtersRun = id;
    }
    if (!hitBar.childElementCount) buildHitBar();
    const scrollTop = keepScroll ? scroller.scrollTop : 0;
    // The ids come with the first rows; ask for the ones that will be on
    // screen, which are not the first when the scroll position is kept. (With
    // features pinned to the front, what is on screen is not a slice of the
    // server's order at all; those rows are fetched by id once the ids are in.)
    const options = structuredClone(requestOptions());
    const offset = pin ? 0 : Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - PREFETCH);
    try {
      const response = await api.features(id, {
        ...options, with_ids: true, offset, limit: PAGE,
      });
      if (mine !== loadToken) return;
      rowsById = new Map();
      pendingIds.clear();
      if (Array.isArray(response.ids)) {
        serverIds = response.ids;
        for (const row of response.rows) rowsById.set(row[0], row);
      } else {
        // A server from before paging (still running while the page was
        // updated under it, say) sends no id list. Rather than show an empty
        // table, take every row the old way.
        const whole = await api.features(id, { ...options, offset: 0, limit: 0 });
        if (mine !== loadToken) return;
        serverIds = whole.rows.map((row) => row[0]);
        for (const row of whole.rows) rowsById.set(row[0], row);
      }
      table = response;
      loadedRun = id;
      loadedOptions = options;
      rowsVersion += 1;
    } catch (error) {
      if (mine !== loadToken) return;
      loadedRun = null;
      clear(body).append(el('tr', {}, [el('td', { colSpan: 8 + LEAD_COLUMNS },
        [el('div', { class: 'notice error', text: error.message })])]));
      return;
    }
    arrange();
    // The same filter as last time stays the same object: the details rail
    // re-requests a feature's matches whenever this changes, and a fresh
    // copy on every reload made it do so for each sort click and keystroke.
    const hitFilter = table.hit_filter ? structuredClone(query.hit_filter) : null;
    const previous = store.get('hitFilter');
    store.set({
      hitFilter: JSON.stringify(previous) === JSON.stringify(hitFilter) ? previous : hitFilter,
      plotFilter: { ...store.get('plotFilter'), run: id, ids: new Set(ids) },
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
    exportSelectedButton = el('button', {
      class: 'btn small', type: 'button', text: 'Export selected',
      title: 'Save the selected features as a CSV, with their best database matches',
      on: { click: (event) => exportSelected(event.currentTarget,
                                             { sort: query.sort, descending: query.descending }) },
    });
    toolbar.append(counts, el('button', {
      class: 'btn small', type: 'button', text: 'Export CSV',
      title: 'Save every feature listed here, as filtered and sorted, with its best '
             + 'database matches',
      // The Sel and Flag filters are applied here, so with one on the
      // listed ids are sent along for the server to keep to.
      on: { click: (event) => exportFeatures(
        localActive() ? { ...requestOptions(), ids } : requestOptions(), event.currentTarget) },
    }), exportSelectedButton);
    // Flag the selection: a colour, Flag, Unflag. Shown with a selection.
    flagBar = flagControls();
    toolbar.append(flagBar.node, el('span', { class: 'spacer' }));
    updateCounts();

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

    // ("MS/MS required" lives in the filter panel, beside the MS/MS search.)

    // "Table rows", not "selection": the selection is now a set of features
    // you pick, and this switch is about the rows the filters leave listed.
    toolbar.append(el('label', {
      class: 'check', title: 'Draw only the features listed here on the plots',
    }, [
      el('input', {
        type: 'checkbox', checked: store.get('plotFilter').active,
        on: { change: (event) => {
          store.set({ plotFilter: { ...store.get('plotFilter'), active: event.target.checked } });
        } },
      }),
      el('span', { class: 'muted', text: 'Plot table rows only' }),
    ]));

    toolbar.append(showFlagsToggle());

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
    local.selectedOnly = false;
    local.flag.clear();
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
    // With the MS/MS search rather than in the toolbar above, which it pushed
    // onto a second line. Offered whenever the run has a fragment file, even
    // without the library to search it against.
    if (table && table.has_fragments) {
      searchRow.append(el('label', {
        class: 'check', title: 'List only features that have an MS/MS spectrum',
      }, [
        el('input', {
          type: 'checkbox', checked: query.ms2_only,
          on: { change: (event) => {
            query.ms2_only = event.target.checked;
            load({ keepScroll: false });
          } },
        }),
        el('span', { text: 'MS/MS required' }),
      ]));
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
      // Say where the rest went: a short hit list mostly reflects how few
      // features have a spectrum, and how few of those a library compound.
      const without = result.features - result.searched;
      toast(`MS/MS: ${result.matched} of ${result.searched} spectra matched`
            + (without > 0 ? ` · ${without.toLocaleString()} of ${result.features.toLocaleString()} `
                             + 'features have no usable spectrum' : '')
            + (typeof result.with_candidates === 'number'
              ? ` · ${(result.searched - result.with_candidates).toLocaleString()} have no library `
                + 'compound at their mass' : ''), 'ok', 9000);
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

  // --- selection ---------------------------------------------------------

  /** Where a range starts: the last row clicked, or failing that the
   *  focused feature (it may have been picked on a plot). */
  const rangeStart = () => anchor ?? rowIndex.get(store.get('selectedFeature')) ?? null;

  function selectRange(from, to, additive) {
    const [low, high] = from <= to ? [from, to] : [to, from];
    const range = ids.slice(low, high + 1);
    if (additive) selection.add(range, ids[to]);
    else selection.set(range, ids[to]);
  }

  function clickRow(index, event) {
    const additive = event.ctrlKey || event.metaKey;
    if (local.selectedOnly && !additive) {
      // Only the selection is listed, so a click that replaced the selection
      // with this one row would empty the list around it. It moves the focus
      // instead; Ctrl-click (or the tick) still takes a row out.
      selection.focus(ids[index]);
      anchor = index;
      return;
    }
    const start = rangeStart();
    if (event.shiftKey && start !== null) {
      selectRange(start, index, additive);      // the anchor stays where it was
    } else if (additive) {
      selection.toggle(ids[index]);
      anchor = index;
    } else {
      store.set({ selectedFeature: ids[index] });
      anchor = index;
    }
  }

  // One listener for the whole body: rows are rebuilt as the list scrolls.
  body.addEventListener('mousedown', (event) => {
    // A Shift-click would otherwise select the text between the two rows.
    if (event.shiftKey) event.preventDefault();
  });
  body.addEventListener('click', (event) => {
    const row = event.target.closest('tr');
    if (!row || row.dataset.index === undefined) return;
    const index = Number(row.dataset.index);
    const cell = event.target.closest('td');
    if (cell && cell.classList.contains('col-flag')) {
      // The flag cell flags or unflags its own row and nothing else, the way
      // a star in a mail list does; the selection is left as it is.
      toggleFlag(ids[index]);
    } else if (cell && cell.classList.contains('col-sel') && !event.shiftKey) {
      // The tick is a checkbox: it adds or removes this row without the Ctrl.
      selection.toggle(ids[index]);
      anchor = index;
    } else {
      clickRow(index, event);
    }
    scroller.focus({ preventScroll: true });
  });

  // --- keyboard navigation ---------------------------------------------

  function moveSelection(delta, extend) {
    if (!ids.length) return;
    const current = store.get('selectedFeature');
    const at = rowIndex.has(current) ? rowIndex.get(current) : -1;
    jumpTo(Math.max(0, Math.min(ids.length - 1, at + delta)), extend);
  }

  function jumpTo(index, extend = false) {
    if (!ids.length) return;
    if (local.selectedOnly) {
      selection.focus(ids[index]);      // see clickRow
      anchor = index;
      scrollRowIntoView(index);
      return;
    }
    const start = rangeStart();
    if (extend && start !== null) {
      selectRange(start, index, false);
    } else {
      store.set({ selectedFeature: ids[index] });
      anchor = index;
    }
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
    if (step !== undefined) {
      event.preventDefault();
      moveSelection(step, event.shiftKey);
      return;
    }
    if (event.key === 'Home') { event.preventDefault(); jumpTo(0, event.shiftKey); }
    if (event.key === 'End') { event.preventDefault(); jumpTo(ids.length - 1, event.shiftKey); }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a' && ids.length
        && !local.selectedOnly) {
      event.preventDefault();
      selection.set(ids, store.get('selectedFeature'));
    }
    if (event.key === 'Escape') selection.clear();
  });

  // --- assembly ---------------------------------------------------------

  node.append(host);

  store.on(['selectedFeature', 'selectedFeatures'], () => {
    selectionVersion += 1;
    // With only the selection listed, the list is the selection: it follows
    // a row being deselected here or a lasso drawn on a plot. (A change of
    // focus alone leaves the set, and so the list, as it is.)
    if (local.selectedOnly && store.get('selectedFeatures') !== filteredFor) {
      refilter();
      return;
    }
    updateCounts();
    if (!node.classList.contains('active')) return;
    renderWindow();
  });
  store.on('flags', () => {
    flagsVersion += 1;
    // The popup lists the colours in use; a colour whose last flag has gone
    // drops out of it, and out of the filter if it was being filtered on.
    const dropped = flagFilterControl ? flagFilterControl.refresh() : false;
    if (local.flag.size || dropped) {
      refilter();     // the list depends on the flags (or just stopped doing so)
      return;
    }
    updateCounts();
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
