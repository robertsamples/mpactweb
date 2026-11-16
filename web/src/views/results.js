/* Result views.
 *
 * The nav mirrors the desktop's result buttons -- Data Review, Group
 * Analysis, Dendrogram, Multivariate, m/z vs RT, Mass Defect, 3D FC,
 * Volcano, Heatmap -- and the pages that were sub-tabbed on the desktop
 * (Data Review's Summary / Feature plot / Tree map / CV plot; Group
 * Analysis's Sets / Sample Correlations) keep their sub-tabs here.
 *
 * Each plot's control bar carries the same live controls the desktop grew
 * per-plot: view and colour switchers, purity colouring, bootstrap support,
 * the mass-defect guidelines toggle, and the volcano's p/q and fold-change
 * thresholds. */

import { registerView } from '../app.js';
import { api } from '../api.js';
import { selection, store } from '../state.js';
import { flags, showFlagsToggle } from '../flags.js';
import { analogueVectors } from '../analogues.js';
import { clear, el, isAdditive, num, select, toast, debounce } from '../util.js';
import { message, plotlyReady } from '../plots/base.js';
import { fc3d, massdefect, mzrt, quality } from '../plots/scatter.js';
import { volcano as drawVolcano, volcanoCounts } from '../plots/volcano.js';
import { dendrogram as drawDendrogram, heatmap as drawHeatmap, samplecorr as drawSamplecorr } from '../plots/matrix.js';
import { ordination as drawOrdination } from '../plots/ordination.js';
import { cvplot, treemap, upset } from '../plots/review.js';
import { createNavbar } from '../plots/navbar.js';
import { buildFeatureInfoView } from './featureinfo.js';
import { buildDownloadsView } from './downloads.js';

const COLORSCHEMES = ['rocket', 'mako', 'flare', 'crest', 'magma', 'viridis'];

const runId = () => (store.get('run') || {}).run_id;

/** Shared context handed to every plot renderer. */
function plotContext() {
  return {
    selectedFeature: store.get('selectedFeature'),
    // Every selected feature, as a Set: what the plots highlight.
    selectedFeatures: selection.asSet(),
    // Flagged features to mark, compound -> colour (empty when switched off).
    flags: flags.shown(),
    // Analogue vectors, drawn on the mass defect plot.
    analogues: analogueVectors(),
    selectedSample: store.get('selectedSample'),
    // A plain click selects that feature alone; with Ctrl or Shift held it
    // is added to (or taken out of) the selection, as in the feature table.
    onSelectFeature: (compound, mouse) => {
      if (isAdditive(mouse)) selection.toggle(compound);
      else store.set({ selectedFeature: compound });
    },
    // Several at once (a binned heatmap row): replace, or add with Ctrl/Shift.
    onSelectFeatures: (compounds, mouse) => {
      if (isAdditive(mouse)) selection.add(compounds);
      else selection.set(compounds, compounds[0]);
    },
    // Picking an ordination point selects an injection/sample. It goes to
    // the status bar, which stays on screen, rather than a toast that has
    // vanished by the time you look for it. The series it came from is the
    // injection's biological group, which the bar shows beside it.
    onSelectSample: (name, group) =>
      store.set({ selectedSample: name, selectedSampleGroup: group || null }),
  };
}

/** The Feature Info table's rows, when "Table rows only" is on for the
 *  current run; otherwise null. */
function tableIds() {
  const filter = store.get('plotFilter');
  return filter && filter.active && filter.ids && filter.run === runId() ? filter.ids : null;
}

/** A payload with every per-feature series cut down to `ids`. Cached on the
 *  payload so a selection redraw reuses the same copy. */
function restrictPayload(payload, ids) {
  if (!ids || !Array.isArray(payload.series)) return payload;
  if (payload._restricted && payload._restricted.ids === ids) return payload._restricted.payload;
  const series = payload.series.map((entry) => {
    if (!Array.isArray(entry.ids)) return entry;
    const keep = entry.ids.map((id) => ids.has(id));
    const out = { ...entry };
    for (const [key, value] of Object.entries(entry)) {
      if (Array.isArray(value) && value.length === entry.ids.length) {
        out[key] = value.filter((_, i) => keep[i]);
      }
    }
    out.count = out.ids.length;
    return out;
  });
  const copy = { ...payload, series, _classified: undefined, _restricted: undefined };
  payload._restricted = { ids, payload: copy };
  return copy;
}

/**
 * Build a plot panel: a control bar, a plot surface, and the fetch/render
 * cycle connecting them.
 *
 * `spec.controls(state, rerender)` returns control elements; `spec.render`
 * draws a fetched payload; `spec.options(state)` maps the panel's control
 * state to the request body. A panel refetches when an option that the
 * server computes changes, and redraws without refetching when only a
 * client-side option (a volcano threshold, say) changes.
 */
function plotPanel(spec) {
  const state = { ...(spec.initial || {}) };
  let payload = null;
  let loadedKey = null;     // run + options the payload was fetched for

  // Two bars, as the desktop has: the plot's own switcher bar across the top
  // of the graphing window (what is being plotted, and how), and matplotlib's
  // navigation toolbar in a strip directly under the canvas (how you move
  // around it and save it). The caption sits below both.
  const toolbar = el('div', { class: 'plot-toolbar' });
  const surface = el('div', { class: `plot-surface ${spec.tall ? 'tall' : ''}` });
  const footer = el('div', { class: 'plot-footer' });
  const navbar = createNavbar(surface, {
    onClearSelection: () => store.set({ selectedFeature: null, selectedSample: null,
                                       selectedSampleGroup: null }),
    isSelectionActive: () => Boolean(store.get('selectedFeature') || store.get('selectedSample')),
    // Box and lasso select features on the plots that draw one point per
    // feature. Holding Shift or Ctrl adds the region to the selection.
    onRegion: spec.featureLevel ? (ids, additive) => {
      if (additive) { if (ids.length) selection.add(ids); }
      else selection.set(ids);
    } : null,
  });
  const canvas = el('div', { class: 'plot-canvas' },
                    [toolbar, surface, navbar.node, footer]);
  const node = el('div', { class: 'plot-panel' }, [canvas]);

  const status = el('span', { class: 'status' });

  const redraw = () => {
    if (!payload) return;
    try {
      const shown = spec.featureLevel ? restrictPayload(payload, tableIds()) : payload;
      // Caption first, so the plot is sized once instead of drawn and then
      // squeezed by a caption arriving underneath it.
      if (spec.footer) {
        const info = spec.info ? spec.info(shown, state) : undefined;
        clear(footer).append(spec.footer(shown, state, info) || el('span'));
      }
      spec.render(surface, shown, plotContext(), state);
    } catch (error) {
      console.error(error);
      message(surface, `Could not draw this plot: ${error.message}`, 'error');
    } finally {
      // Only the enabled/pressed states, which is all a selection can change.
      // The full sync rebinds Plotly's listeners and re-reads the export
      // preferences, and belongs with an actual redraw, not with every click.
      navbar.syncEnabled();
      // The caption is written *after* the plot is drawn (a volcano's counts
      // come out of drawing it), and a two-line caption is 50px the plot no
      // longer has. Plotly only reflows on demand, so without this the axis
      // labels spill over the toolbar below.
      requestAnimationFrame(() => resize());
    }
  };

  // Requests can overlap (a control changed twice, a run switched mid-load)
  // and need not come back in the order they left. Only the newest one may
  // draw: before this, ticking "Bootstrap support" and then unticking it drew
  // the quick plain dendrogram first and then, seconds later, the bootstrap
  // one over it -- under a checkbox that said it was off.
  let requestToken = 0;
  let pending = null;       // {key, promise} for the request in the air

  const fetchAndDraw = () => {
    const id = runId();
    if (!id) { message(surface, 'Run an analysis to see this plot.'); return Promise.resolve(); }
    const options = spec.options ? spec.options(state) : {};
    const key = `${id}\u0000${JSON.stringify(options)}`;
    // Asked again for exactly what is already on its way (a quick tab
    // round-trip): wait for that one instead of sending a second.
    if (pending && pending.key === key) return pending.promise;

    const mine = ++requestToken;
    status.textContent = 'Loading…';
    let promise = null;
    promise = (async () => {
      try {
        const [fetched] = await Promise.all([api.plot(id, spec.plot, options), plotlyReady()]);
        if (mine !== requestToken) return;
        payload = fetched;
        loadedKey = key;
        status.textContent = '';
        buildToolbar();
        redraw();
        // A new figure: rebind the toolbar to it in full.
        navbar.sync();
      } catch (error) {
        if (mine !== requestToken) return;
        payload = null;
        status.textContent = '';
        message(surface, error.message, 'error');
        navbar.sync();
      } finally {
        if (pending && pending.promise === promise) pending = null;
      }
    })();
    pending = { key, promise };
    return promise;
  };

  function buildToolbar() {
    // The controls' options come from the payload, so the bar is rebuilt after
    // each fetch -- which throws away the control the user just operated. Note
    // where the focus was and put it back, or changing a dropdown leaves the
    // keyboard stranded (the same fault the table header had).
    const focusables = () => [...toolbar.querySelectorAll('select, input, button')];
    const focusedIndex = toolbar.contains(document.activeElement)
      ? focusables().indexOf(document.activeElement) : -1;

    clear(toolbar);
    const controls = spec.controls ? spec.controls(state, { fetchAndDraw, redraw }, payload) : [];
    if (spec.featureLevel) controls.push(tableToggle());
    // Only once something is flagged: until then it is a switch for nothing.
    if ((spec.featureLevel || spec.showsFlags) && flags.count()) {
      controls.push(showFlagsToggle('Show flags'));
    }
    for (const item of controls) toolbar.append(item);
    toolbar.append(el('span', { class: 'spacer' }));
    toolbar.append(status);
    // A plot with no live controls gets no empty grey strip above it; the
    // surface then owns the full rounded border on its own.
    const bare = !controls.length;
    toolbar.classList.toggle('hidden', bare);
    canvas.classList.toggle('no-toolbar', bare);

    if (focusedIndex !== -1) {
      const restored = focusables()[focusedIndex];
      if (restored) restored.focus({ preventScroll: true });
    }
  }

  /** The plot-side switch for the Feature Info table restriction. Only shown
   *  once the table has been loaded for this run. */
  function tableToggle() {
    const filter = store.get('plotFilter');
    const node = el('label', { class: 'check' }, [
      el('input', {
        type: 'checkbox', checked: filter.active,
        on: { change: (event) => {
          store.set({ plotFilter: { ...store.get('plotFilter'), active: event.target.checked } });
        } },
      }),
      el('span', { text: 'Table rows only' }),
    ]);
    node.title = 'Plot only the features currently listed in Feature Info '
                 + '(its filters and searches)';
    node.classList.toggle('hidden', !filter.ids || filter.run !== runId());
    return node;
  }

  if (spec.featureLevel) {
    store.on('plotFilter', () => {
      if (!node.isConnected || node.offsetParent === null) return;
      buildToolbar();
      redraw();
    });
  }
  if (spec.featureLevel || spec.showsFlags) {
    // The switch appears with the first flag and follows the setting; the
    // redraw itself is the view's (it redraws on any selection or flag change).
    store.on(['flags', 'showFlags'], () => {
      if (!node.isConnected || node.offsetParent === null) return;
      buildToolbar();
    });
  }

  buildToolbar();
  message(surface, 'Loading…');

  const resize = () => {
    if (!surface._plotly || typeof window.Plotly === 'undefined') return;
    // `Plotly.Plots.resize` redraws the entire figure, so calling it when
    // nothing has actually changed size makes every selection flash the plot.
    // Redraw only when the container and the figure disagree.
    const layout = surface._fullLayout;
    if (layout && Math.abs(layout.width - surface.clientWidth) < 1
        && Math.abs(layout.height - surface.clientHeight) < 1) {
      return;
    }
    try { Plotly.Plots.resize(surface); } catch { /* not drawn yet */ }
  };

  /** Show the panel: refetch only if the run or the server-side options
   *  changed since the last fetch, otherwise redraw what is there. */
  const load = () => {
    const key = `${runId()}\u0000${JSON.stringify(spec.options ? spec.options(state) : {})}`;
    if (payload && key === loadedKey) {
      // The bar's switches may have been changed from another view since.
      buildToolbar();
      redraw();
      return Promise.resolve();
    }
    return fetchAndDraw();
  };

  return { node, load, redraw, resize, surface,
           syncNav: navbar.syncEnabled,
           get payload() { return payload; } };
}

/** A labelled control for a plot toolbar. */
const control = (label, node) => el('label', {}, [el('span', { text: label }), node]);

/** Build a view made of sub-tabs, each holding one plot panel. */
function tabbedView({ id, label, group, requires, tabs }) {
  const node = el('div', { class: 'plot-view' });
  const header = el('div', { class: 'btn-row tab-header', style: { marginBottom: '12px' } });
  const panelHost = el('div', { class: 'plot-panel-host',
                                style: { flex: '1', minWidth: '0', display: 'flex' } });
  const host = el('div', { class: 'plot-host' }, [panelHost]);
  let activeTab = tabs[0].id;

  const panels = new Map();
  for (const tab of tabs) panels.set(tab.id, tab.build());

  const showTab = (tabId) => {
    activeTab = tabId;
    for (const button of header.children) {
      button.classList.toggle('primary', button.dataset.tab === tabId);
    }
    clear(panelHost).append(panels.get(tabId).node);
    panels.get(tabId).load();
  };

  for (const tab of tabs) {
    header.append(el('button', {
      class: 'btn small', text: tab.label, dataset: { tab: tab.id },
      on: { click: () => showTab(tab.id) },
    }));
  }

  // No title strip: the view is named in the navigation bar on the left, and
  // repeating it above the plot only costs the plot vertical space.
  if (tabs.length > 1) node.append(header);
  node.append(host);

  // A selection made anywhere updates the highlight here -- the desktop's
  // cross-plot highlight. Only the visible view redraws; the others pick the
  // selection up when they are next shown.
  store.on(['selectedFeature', 'selectedFeatures', 'selectedSample', 'flags', 'showFlags',
            'analogues'], () => {
    if (!node.classList.contains('active')) return;
    panels.get(activeTab)?.redraw();
  });

  // Plotly only reflows on demand, so a resized window would otherwise leave
  // a stretched canvas. Debounced, and only for the view actually on screen.
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    if (!node.classList.contains('active')) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => panels.get(activeTab)?.resize(), 120);
  });

  return {
    id, label, group, requiresRun: true, node, rail: true,
    hidden: () => {
      if (!requires) return false;
      const catalogue = store.get('catalogue');
      if (!catalogue) return false;
      const entry = catalogue.plots.find((plot) => plot.name === requires);
      return entry ? !entry.available : false;
    },
    onShow: () => {
      showTab(activeTab);
      // Plotly sizes to its container; the container had no height while the
      // view was hidden, so nudge it once the view is laid out.
      requestAnimationFrame(() => panels.get(activeTab)?.resize());
    },
  };
}

// --------------------------------------------------------------------------
// Individual pages
// --------------------------------------------------------------------------

function summaryPanel() {
  const surface = el('div');
  const node = el('div', { class: 'plot-panel scrolls' }, [surface]);

  let shownFor = null;      // the run whose summary is on screen
  let loadToken = 0;

  const load = async () => {
    const id = runId();
    // Nothing in the summary changes after a run completes, so coming back
    // to the tab shows what is already there instead of refetching it.
    if (!id || id === shownFor) return;
    const mine = ++loadToken;
    shownFor = null;
    clear(surface).append(el('p', { class: 'muted', text: 'Loading…' }));
    let payload;
    try {
      payload = await api.plot(id, 'summary', {});
    } catch (error) {
      if (mine !== loadToken) return;
      clear(surface).append(el('div', { class: 'notice error', text: error.message }));
      return;
    }
    if (mine !== loadToken) return;
    shownFor = id;
    clear(surface);

    const counts = payload.counts;
    surface.append(el('div', { class: 'panel' }, [
      el('h3', { text: 'Dataset' }),
      el('div', { class: 'stat-row' }, [
        stat('Features', counts.features_total.toLocaleString()),
        stat('Passing filters', counts.features_passing.toLocaleString(), true),
        stat('Injections', counts.injections),
        stat('Samples', counts.samples),
        stat('Biological groups', counts.biological_groups),
      ]),
    ]));

    if (payload.scores) {
      surface.append(el('div', { class: 'panel' }, [
        el('h3', {}, [el('span', { text: 'Data quality' }),
                      el('span', { class: 'hint', text: 'from the CV rarefaction curve' })]),
        el('div', { class: 'stat-row' }, [
          stat('Reproducibility', `${payload.scores.reproducibility.toFixed(1)}%`),
          stat('Skewness', `${payload.scores.skewness.toFixed(1)}%`),
          stat('Overall', `${payload.scores.overall.toFixed(1)}%`, true),
        ]),
      ]));
    }

    const table = el('table', { class: 'data' }, [
      el('thead', {}, [el('tr', {}, [
        el('th', { text: 'Filter outcome' }), el('th', { text: 'Features' }), el('th', { text: '%' }),
      ])]),
    ]);
    const body = el('tbody');
    for (const row of payload.filters) {
      body.append(el('tr', {}, [
        el('td', { text: row.label }),
        el('td', { class: 'num', text: row.count.toLocaleString() }),
        el('td', { class: 'num', text: `${row.percent}%` }),
      ]));
    }
    table.append(body);
    surface.append(el('div', { class: 'panel' }, [el('h3', { text: 'Filtering' }), table]));

    if (payload.feature_sets.length) {
      surface.append(el('div', { class: 'panel' }, [
        el('h3', { text: 'Plot Feature Sets' }),
        el('div', { class: 'plot-legend' }, payload.feature_sets.map((set) =>
          el('div', { class: 'item' }, [
            el('span', { class: 'swatch', style: { background: set.colour } }),
            el('span', { text: `${set.name}: ${set.count.toLocaleString()} features` }),
          ]))),
      ]));
    }

    surface.append(el('div', { class: 'panel' }, [
      el('h3', { text: 'Analysis info' }),
      el('pre', { class: 'log', text: payload.analysisinfo || 'Not available.' }),
    ]));
  };

  return { node, load, redraw: () => {}, resize: () => {} };
}

const stat = (label, value, accent = false) => el('div', { class: 'stat' }, [
  el('div', { class: `value ${accent ? 'accent' : ''}`, text: String(value) }),
  el('div', { class: 'label', text: label }),
]);

function dataReviewView() {
  return tabbedView({
    id: 'review', label: 'Data Review', group: 'Results',
    tabs: [
      { id: 'summary', label: 'Summary', build: summaryPanel },
      {
        id: 'quality', label: 'Feature plot',
        // No caption legend: the plot draws its own key inside the axes,
        // where matplotlib puts it.
        build: () => plotPanel({ plot: 'quality', render: quality, featureLevel: true }),
      },
      {
        id: 'treemap', label: 'Tree map',
        build: () => plotPanel({ plot: 'treemap', render: treemap }),
      },
      {
        id: 'cvplot', label: 'CV plot',
        build: () => plotPanel({
          plot: 'cvplot', render: cvplot,
          footer: (payload) => el('div', { class: 'stat-row', style: { paddingTop: '10px' } }, [
            stat('Reproducibility', `${payload.scores.reproducibility.toFixed(1)}%`),
            stat('Skewness', `${payload.scores.skewness.toFixed(1)}%`),
            stat('Overall', `${payload.scores.overall.toFixed(1)}%`, true),
          ]),
        }),
      },
    ],
  });
}

function groupAnalysisView() {
  return tabbedView({
    id: 'groupanalysis', label: 'Group Analysis', group: 'Results',
    tabs: [
      {
        id: 'upset', label: 'Sets',
        build: () => plotPanel({
          plot: 'upset', tall: true, initial: { limit: 30 },
          controls: (state, actions) => [
            control('Max combinations', select([10, 20, 30, 50, 100], state.limit, (value) => {
              state.limit = Number(value);
              actions.redraw();
            })),
          ],
          render: (surface, payload, context, state) => upset(surface, payload, context, state),
          footer: (payload) => el('p', { class: 'muted' },
            [`${payload.intersections.length} distinct group combinations across `
             + `${payload.total.toLocaleString()} filtered features.`]),
        }),
      },
      {
        id: 'samplecorr', label: 'Sample Correlations',
        build: () => plotPanel({
          plot: 'samplecorr', tall: true,
          initial: { method: 'Spearman', view: 'Biological Replicates',
                     use_sample_names: true, colorscheme: 'rocket' },
          options: (state) => state,
          controls: (state, actions, payload) => [
            control('Method', select(payload ? payload.methods : ['Spearman'], state.method,
                                     (value) => { state.method = value; actions.fetchAndDraw(); })),
            control('View', select(payload ? payload.views : [], state.view,
                                   (value) => { state.view = value; actions.fetchAndDraw(); })),
            el('label', { class: 'check' }, [
              el('input', { type: 'checkbox', checked: state.use_sample_names,
                            on: { change: (e) => { state.use_sample_names = e.target.checked; actions.fetchAndDraw(); } } }),
              el('span', { text: 'Use Sample/Group Names' }),
            ]),
            control('Colour', select(COLORSCHEMES, state.colorscheme,
                                     (value) => { state.colorscheme = value; actions.fetchAndDraw(); })),
          ],
          render: drawSamplecorr,
        }),
      },
    ],
  });
}

function dendrogramView() {
  return tabbedView({
    id: 'dendrogram', label: 'Dendrogram', group: 'Results', requires: 'dendrogram',
    tabs: [{
      id: 'dendrogram', label: 'Dendrogram',
      build: () => plotPanel({
        plot: 'dendrogram', tall: true,
        initial: { view: 'Technical Replicates', color_mode: 'Purity',
                   bootstrap: false, use_sample_names: true },
        options: (state) => state,
        controls: (state, actions, payload) => [
          control('View', select(payload ? payload.views : [], state.view,
                                 (value) => { state.view = value; actions.fetchAndDraw(); })),
          control('Colour', select(payload ? payload.color_modes : [], state.color_mode,
                                   (value) => { state.color_mode = value; actions.fetchAndDraw(); })),
          el('label', { class: 'check' }, [
            el('input', { type: 'checkbox', checked: state.bootstrap,
                          on: { change: (e) => { state.bootstrap = e.target.checked; actions.fetchAndDraw(); } } }),
            el('span', { text: 'Bootstrap support (slow)' }),
          ]),
          el('label', { class: 'check' }, [
            el('input', { type: 'checkbox', checked: state.use_sample_names,
                          on: { change: (e) => { state.use_sample_names = e.target.checked; actions.fetchAndDraw(); } } }),
            el('span', { text: 'Use Sample/Group Names' }),
          ]),
        ],
        render: drawDendrogram,
        footer: (payload) => el('p', { class: 'muted' }, [
          payload.color_mode === 'Purity'
            ? 'Green: every leaf below shares one group. Magenta: this merge splits a group apart. '
              + 'Black: the two sides share no group.'
            : '',
          payload.bootstrap ? ' Node labels are AU / BP bootstrap percentages.' : '',
        ]),
      }),
    }],
  });
}

function multivariateView() {
  return tabbedView({
    id: 'multivariate', label: 'Multivariate', group: 'Results', requires: 'ordination',
    tabs: [{
      id: 'ordination', label: 'Multivariate',
      build: () => plotPanel({
        plot: 'ordination', tall: true, showsFlags: true,
        // NMDS of the biological replicates (technical replicates averaged)
        // first; both can be changed from the bar.
        initial: { method: 'NMDS', view: 'Scores', replicates: 'Biological Replicates' },
        // The selected feature only matters to the loadings (it is always
        // included there). Sent with the scores as well, it made every
        // feature selection look like a change of options, and the next
        // visit to this tab re-requested an ordination that had not changed.
        options: (state) => (state.view === 'Loadings'
          ? { ...state, selected_feature: store.get('selectedFeature') }
          : { ...state }),
        controls: (state, actions, payload) => [
          control('Method', select(payload ? payload.methods : [state.method], state.method,
                                   (value) => { state.method = value; actions.fetchAndDraw(); })),
          control('View', select(payload ? payload.views : ['Scores'], state.view,
                                 (value) => { state.view = value; actions.fetchAndDraw(); })),
          // A named choice rather than a "collapse" checkbox: it says what
          // you get instead of what it does to something unnamed.
          control('Points', select(payload ? payload.replicate_views
                                           : [state.replicates],
                                   state.replicates,
                                   (value) => { state.replicates = value; actions.fetchAndDraw(); })),
        ],
        render: drawOrdination,
        footer: (payload) => {
          if (payload.view === 'Loadings') {
            return el('p', { class: 'muted',
                             text: 'Top features by loading magnitude within the two displayed components.' });
          }
          const variance = payload.explained_variance || [];
          return el('p', { class: 'muted' }, [
            `${payload.n_points} ${payload.point_noun} plotted · `,
            variance.length
              ? `explained variance ${variance.slice(0, 4).map((v) => `${(100 * v).toFixed(1)}%`).join(', ')} · `
              : '',
            'ellipses are 2σ confidence regions per biological group.',
          ]);
        },
      }),
    }],
  });
}

function scatterView({ id, label, plot, requires, render, extraControls }) {
  return tabbedView({
    id, label, group: 'Results', requires,
    tabs: [{
      id, label,
      build: () => plotPanel({
        plot, tall: true, initial: { guidelines: true }, featureLevel: true,
        options: (state) => ({ guidelines: state.guidelines }),
        controls: extraControls,
        render,
      }),
    }],
  });
}

function volcanoView() {
  return tabbedView({
    id: 'volcano', label: 'Volcano', group: 'Results', requires: 'volcano',
    tabs: [{
      id: 'volcano', label: 'Volcano',
      build: () => plotPanel({
        plot: 'volcano', tall: true, featureLevel: true,
        initial: { pqthresh: 0.05, fcthresh: 1.5 },
        controls: (state, actions) => {
          // Threshold changes are re-classified client-side, so these redraw
          // without a round trip.
          const onInput = debounce(actions.redraw, 220);
          const box = (label, key, step) => control(label, el('input', {
            type: 'number', step, value: state[key],
            on: { input: (event) => {
              const value = parseFloat(event.target.value);
              if (!Number.isNaN(value)) { state[key] = value; onInput(); }
            } },
          }));
          return [box('p/q threshold', 'pqthresh', 0.01),
                  box('Fold-change threshold', 'fcthresh', 0.1)];
        },
        info: (payload, state) => volcanoCounts(payload, state),
        render: (surface, payload, context, state) => drawVolcano(surface, payload, context, state),
        footer: (payload, state, counts) => el('div', {}, [
          payload.warning ? el('div', { class: 'notice warn', text: payload.warning }) : null,
          el('p', { class: 'muted' }, [
            `${payload.stats_groups[0]} vs ${payload.stats_groups[1]} · `
            + `using ${payload.pq_variable}`
            + (counts ? ` · ${counts.up} enriched, ${counts.down} depleted, ${counts.ns} not significant` : ''),
          ]),
        ]),
      }),
    }],
  });
}

function heatmapView() {
  return tabbedView({
    id: 'heatmap', label: 'Heatmap', group: 'Results',
    tabs: [{
      id: 'heatmap', label: 'Heatmap',
      build: () => plotPanel({
        plot: 'heatmap', tall: true, showsFlags: true,
        initial: { view: 'Technical Replicates', use_sample_names: true, colorscheme: 'rocket' },
        options: (state) => state,
        controls: (state, actions, payload) => [
          control('View', select(payload ? payload.views : [], state.view,
                                 (value) => { state.view = value; actions.fetchAndDraw(); })),
          el('label', { class: 'check' }, [
            el('input', { type: 'checkbox', checked: state.use_sample_names,
                          on: { change: (e) => { state.use_sample_names = e.target.checked; actions.fetchAndDraw(); } } }),
            el('span', { text: 'Use Sample/Group Names' }),
          ]),
          control('Colour', select(COLORSCHEMES, state.colorscheme,
                                   (value) => { state.colorscheme = value; actions.fetchAndDraw(); })),
        ],
        render: drawHeatmap,
        footer: (payload) => el('p', { class: 'muted' },
          [`${payload.shape[0].toLocaleString()} features × ${payload.shape[1]} columns, `
           + 'row-scaled and clustered with Ward linkage on Euclidean distance. '
           + ((payload.bin_size || 1) > 1
             ? `Drawn as ${payload.z.length.toLocaleString()} rows: neighbouring features are `
               + `averaged ${payload.bin_size} to a row, and clicking a row selects them. `
             : 'Click a cell to select that feature; Ctrl-click adds to the selection. ')]),
      }),
    }],
  });
}

// --------------------------------------------------------------------------

export function buildResultViews() {
  registerView(dataReviewView());
  registerView(groupAnalysisView());
  registerView(dendrogramView());
  registerView(multivariateView());
  registerView(scatterView({
    id: 'mzrt', label: 'm/z vs RT', plot: 'mzrt', requires: 'mzrt', render: mzrt,
  }));
  registerView(scatterView({
    id: 'massdefect', label: 'Mass Defect', plot: 'massdefect', requires: 'massdefect',
    render: massdefect,
    extraControls: (state, actions) => [
      el('label', { class: 'check' }, [
        el('input', { type: 'checkbox', checked: state.guidelines,
                      on: { change: (e) => { state.guidelines = e.target.checked; actions.fetchAndDraw(); } } }),
        el('span', { text: 'Mass Defect Guidelines' }),
      ]),
    ],
  }));
  registerView(scatterView({
    id: 'fc3d', label: '3D Fold Change', plot: 'fc3d', requires: 'fc3d', render: fc3d,
  }));
  registerView(volcanoView());
  registerView(heatmapView());
  registerView(buildFeatureInfoView());
  registerView(buildDownloadsView());
}

export { plotPanel, control, stat };
