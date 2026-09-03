/* Application shell: navigation, view registry, run polling.
 *
 * The sidebar mirrors the desktop's tab order -- Setup (File Selection,
 * Filtering, Analysis Settings, Run) then the result tabs -- and result tabs
 * stay disabled until a run has completed, the same way the desktop's plot
 * pages are empty before you press Run. */

import { api } from './api.js';
import { store } from './state.js';
import { $, clear, el, toast } from './util.js';
import { makeSplitter } from './components.js';
import { buildSetupViews } from './views/setup.js';
import { buildResultViews } from './views/results.js';
import { showRunsDialog } from './views/runs.js';
import { createStatusBar } from './views/statusbar.js';
import { detailsRail } from './views/detailsrail.js';

const POLL_INTERVAL_MS = 1500;

const views = new Map();     // id -> {id, label, group, requiresRun, node, onShow}
let pollTimer = null;

// The one details rail, mounted beside the main region as the counterpart of
// the navigation bar on the left. Views declare `rail: true` to have it; its
// width and collapsed state are the app's, not any one view's, so hiding it
// on one plot hides it on all of them.
let rail = null;
let railSplitter = null;
let railReopen = null;

const railWanted = () => Boolean(views.get(store.get('view'))?.rail);

function mountRail() {
  rail = detailsRail({
    onCollapse: () => setRailCollapsed(true),
    onOpenFeatureInfo: () => showView('features'),
  });
  railSplitter = el('div', { class: 'rail-splitter',
                             title: 'Drag to resize, double-click to reset' });
  railReopen = el('button', {
    class: 'rail-toggle', type: 'button', text: '\u2039 Details',
    title: 'Show the details panel',
    on: { click: () => setRailCollapsed(false) },
  });
  $('.body').append(railSplitter, rail.node, railReopen);
  makeSplitter(railSplitter, rail.node, {
    min: 220,
    max: 640,
    // Plots size to their container, so the view on screen has to re-lay-out
    // as the boundary moves; the plot views listen for this.
    onResize: () => window.dispatchEvent(new Event('resize')),
    onCommit: (width) => { rail.setWidth(width); window.dispatchEvent(new Event('resize')); },
  });
  syncRail();
}

function syncRail() {
  if (!rail) return;
  const wanted = railWanted();
  const collapsed = rail.isCollapsed();
  rail.applyWidth();
  rail.node.classList.toggle('hidden', !wanted);
  railSplitter.classList.toggle('hidden', !wanted || collapsed);
  railReopen.classList.toggle('hidden', !wanted || !collapsed);
  if (wanted && !collapsed) rail.render();
}

function setRailCollapsed(collapsed) {
  rail.setCollapsed(collapsed);
  syncRail();
  window.dispatchEvent(new Event('resize'));
}

export function registerView(view) {
  views.set(view.id, view);
  view.node.classList.add('view');
  view.node.id = `view-${view.id}`;
  $('#main').append(view.node);
}

export function showView(id) {
  const view = views.get(id);
  if (!view) return;
  if (view.requiresRun && !isRunComplete()) return;
  for (const other of views.values()) other.node.classList.toggle('active', other === view);
  store.set({ view: id });
  renderNav();
  syncRail();
  if (view.onShow) {
    Promise.resolve(view.onShow()).catch((error) => toast(error.message, 'error'));
  }
  // The active view is part of the address so a result tab can be linked or
  // survive a refresh.
  const run = store.get('run');
  const hash = run && run.run_id ? `#${id}/${run.run_id}` : `#${id}`;
  if (window.location.hash !== hash) window.history.replaceState(null, '', hash);
}

export const isRunComplete = () => (store.get('run') || {}).state === 'complete';

function renderNav() {
  const nav = clear($('#sidebar'));
  const complete = isRunComplete();
  const active = store.get('view');
  let lastGroup = null;

  for (const view of views.values()) {
    if (view.hidden && view.hidden()) continue;
    if (view.group !== lastGroup) {
      nav.append(el('div', { class: 'group-label', text: view.group }));
      lastGroup = view.group;
    }
    const disabled = Boolean(view.requiresRun) && !complete;
    nav.append(el('button', {
      class: view.id === active ? 'active' : '',
      disabled,
      title: disabled ? 'Run an analysis first' : '',
      on: { click: () => showView(view.id) },
    }, [
      el('span', { text: view.label }),
      view.badge ? el('span', { class: 'badge', text: view.badge() }) : null,
    ]));
  }
}

function renderRunChip() {
  const chip = $('#run-chip');
  const run = store.get('run');
  chip.className = 'run-chip';
  if (!run) { chip.textContent = 'No analysis'; return; }

  const label = run.label ? `${run.label} · ` : '';
  if (run.state === 'running' || run.state === 'queued') {
    chip.classList.add('is-running');
    clear(chip).append(el('span', { class: 'spinner' }),
                       el('span', { text: `${label}${run.stage || run.state}` }));
  } else if (run.state === 'complete') {
    chip.classList.add('is-complete');
    chip.textContent = `${label}Analysis complete`;
  } else if (run.state === 'failed' || run.state === 'cancelled') {
    chip.classList.add('is-failed');
    chip.textContent = `${label}${run.state}`;
  } else {
    chip.textContent = `${label}${run.state}`;
  }
}

/** Poll a run to completion, then load its result catalogue. */
export function watchRun(runId) {
  clearTimeout(pollTimer);

  const tick = async () => {
    let status;
    try {
      status = await api.runStatus(runId);
    } catch (error) {
      store.set({ run: { run_id: runId, state: 'failed', error: error.message } });
      toast(`Lost track of the analysis: ${error.message}`, 'error');
      return;
    }
    store.set({ run: status });

    if (status.state === 'complete') {
      await loadCatalogue(runId);
      toast('Analysis complete', 'ok');
      return;
    }
    if (status.state === 'failed' || status.state === 'cancelled') {
      if (status.error) toast(status.error, 'error');
      return;
    }
    pollTimer = setTimeout(tick, POLL_INTERVAL_MS);
  };
  tick();
}

export async function loadCatalogue(runId) {
  try {
    const catalogue = await api.catalogue(runId);
    store.set({ catalogue });
  } catch (error) {
    store.set({ catalogue: null });
    toast(`Could not load results: ${error.message}`, 'error');
  }
}

/** Adopt an existing run (from the run list, or the URL) as the active one. */
export async function selectRun(runId) {
  clearTimeout(pollTimer);
  const status = await api.runStatus(runId);
  store.set({ run: status, selectedFeature: null, catalogue: null });
  if (status.state === 'complete') await loadCatalogue(runId);
  else if (status.state === 'running' || status.state === 'queued') watchRun(runId);
}

async function boot() {
  try {
    store.set({ health: await api.health() });
  } catch (error) {
    toast(error.message, 'error');
  }

  buildSetupViews();
  buildResultViews();
  mountRail();
  store.on('selectedFeature', () => { if (railWanted() && !rail.isCollapsed()) rail.render(); });

  store.on(['run', 'catalogue'], () => { renderRunChip(); renderNav(); });
  store.on('view', renderNav);

  $('#btn-runs').addEventListener('click', showRunsDialog);

  // Persistent selection readout along the bottom of the window.
  const statusbar = createStatusBar();
  $('#statusbar-host').replaceWith(statusbar.node);

  renderNav();
  renderRunChip();

  // Restore the run and view named in the URL, if any.
  const [, viewId, runId] = (window.location.hash || '').match(/^#([^/]+)(?:\/(.+))?$/) || [];
  if (runId) {
    try {
      await selectRun(runId);
    } catch {
      toast('That analysis is no longer available on this server', 'info');
    }
  }
  showView(views.has(viewId) ? viewId : 'files');
}

boot();
