/* Application shell: navigation, view registry, run polling.
 *
 * The sidebar mirrors the desktop's tab order -- Setup (File Selection,
 * Filtering, Analysis Settings, Run) then the result tabs -- and result tabs
 * stay disabled until a run has completed, the same way the desktop's plot
 * pages are empty before you press Run. */

import { api } from './api.js';
import { store } from './state.js';
import { loadFlags } from './flags.js';
import { $, clear, el, settle, toast } from './util.js';
import { makeSplitter } from './components.js';
import { buildSetupViews } from './views/setup.js';
import { buildResultViews } from './views/results.js';
import { showRunsDialog } from './views/runs.js';
import { createStatusBar } from './views/statusbar.js';
import { showLogin } from './views/login.js';
import { detailsRail } from './views/detailsrail.js';
import { dinoCrash, toggleDinoMenu } from './views/dino.js';
import { buildAdminView } from './views/admin.js';

const POLL_INTERVAL_MS = 1500;
// Consecutive failed status polls tolerated before a run is reported lost:
// with the backoff below, a little over a minute and a half.
const POLL_MAX_FAILURES = 12;

const views = new Map();     // id -> {id, label, group, requiresRun, node, onShow}
let pollTimer = null;
let pollToken = 0;           // bumped whenever the watched run changes

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

/** Show a view. Returns false when it cannot be shown (an unknown id, or a
 *  result view with no completed run behind it). */
export function showView(id) {
  const view = views.get(id);
  if (!view) return false;
  if (view.requiresRun && !isRunComplete()) return false;
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
  return true;
}

/** Where to land when a result view has no completed run to show: the Run
 *  view if there is a run to report on (in flight, failed, cancelled),
 *  otherwise the start of the workflow. */
function fallbackView() {
  return store.get('run') ? 'run' : 'files';
}

export const isRunComplete = () => (store.get('run') || {}).state === 'complete';

/** Put the signed-in identity, and a way out, in the title bar. */
function showSignedIn(auth) {
  if (!auth.required) return;
  const host = $('#run-chip').parentElement;
  const label = auth.user && auth.user !== 'shared' ? auth.user : 'Signed in';
  host.insertBefore(el('span', {
    class: 'signed-in', text: label,
    title: auth.role === 'external'
      ? 'Outside account: your own uploads and the public demo, within a storage quota'
      : auth.role === 'internal' ? 'Internal account' : '',
  }), $('#run-chip'));
  host.append(el('button', {
    class: 'btn small', type: 'button', text: 'Sign out',
    on: {
      click: async () => {
        try { await api.logout(); } catch { /* going anyway */ }
        window.location.reload();
      },
    },
  }));
}

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
  } else if (run.state === 'complete' && store.get('busy')) {
    chip.classList.add('is-running');
    clear(chip).append(el('span', { class: 'spinner' }),
                       el('span', { text: `${label}${store.get('busy')}` }));
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

/** Put a polled status in the store, but only when something in it changed.
 *
 *  Every poll returns a fresh object, and the store notifies on identity. So
 *  each 1.5 s tick used to count as "the run changed": the Run view tore
 *  itself down and rebuilt, which blanked the log and threw away where you
 *  had scrolled to in it, and the navigation and run chip were redrawn too. */
function setRun(status) {
  const current = store.get('run');
  if (current && JSON.stringify(current) === JSON.stringify(status)) return;
  store.set({ run: status });
}

/** Poll a run to completion, then load its result catalogue. */
export function watchRun(runId) {
  clearTimeout(pollTimer);
  // Cancelling the timer does not cancel a request already in the air; the
  // token is what stops a superseded watch from writing its answer over the
  // run that replaced it.
  const mine = ++pollToken;
  let failures = 0;

  const tick = async () => {
    let status;
    try {
      status = await api.runStatus(runId);
    } catch (error) {
      if (mine !== pollToken) return;
      failures += 1;
      // One dropped request is not a lost analysis. The run carries on
      // server-side whatever happens to this connection, so keep asking
      // through a blip or a server restart, and only give up when the run is
      // really gone (404) or the outage has lasted a while.
      const gone = error.status === 404;
      if (!gone && failures <= POLL_MAX_FAILURES) {
        if (failures === 2) toast('Connection to the server lost; retrying…', 'info');
        pollTimer = setTimeout(tick, Math.min(POLL_INTERVAL_MS * 2 ** (failures - 1), 10000));
        return;
      }
      store.set({ run: { run_id: runId, state: 'failed', error: error.message } });
      toast(`Lost track of the analysis: ${error.message}`, 'error');
      return;
    }
    if (mine !== pollToken) return;
    if (failures >= 2) toast('Reconnected', 'ok');
    failures = 0;
    setRun(status);

    if (status.state === 'complete') {
      await loadCatalogue(runId);
      if (mine !== pollToken) return;
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
    // The run's flags come with it, so the first plot drawn already has them.
    const [catalogue] = await Promise.all([api.catalogue(runId), loadFlags(runId)]);
    store.set({ catalogue });
  } catch (error) {
    store.set({ catalogue: null });
    toast(`Could not load results: ${error.message}`, 'error');
  }
}

/** Adopt an existing run (from the run list, or the URL) as the active one. */
export async function selectRun(runId) {
  clearTimeout(pollTimer);
  const mine = ++pollToken;
  const status = await api.runStatus(runId);
  if (mine !== pollToken) return;
  store.set({ run: status, selectedFeature: null, selectedSample: null,
              selectedSampleGroup: null, catalogue: null, flags: {} });
  if (status.state === 'running' || status.state === 'queued') {
    // Not viewable yet; the store listener in boot() has already moved a
    // result view over to Run, and the watch takes it from here.
    watchRun(runId);
    return;
  }
  if (status.state === 'complete') await loadCatalogue(runId);
  if (mine !== pollToken) return;
  // The view on screen was drawn for the previous run. Views load when they
  // are shown, so show it again -- otherwise opening another analysis from
  // the run list left the old one's plot up until you changed tabs.
  const current = store.get('view');
  if (views.has(current) && !showView(current)) showView(fallbackView());
}

async function boot() {
  // Before anything else: does this server want a login, and do we have one?
  // Everything below would 401 without it.
  let auth = { required: false, authenticated: true };
  try {
    auth = await api.authStatus();
  } catch { /* an older server with no auth routes: carry on unauthenticated */ }

  if (auth.required && !auth.authenticated) {
    await showLogin(document.getElementById('app'), auth);
    window.location.reload();
    return;
  }
  showSignedIn(auth);

  // A login that lapses mid-session sends you back to the sign-in screen
  // rather than leaving a shell that fails every request.
  window.addEventListener('mpact:unauthenticated', () => {
    if (document.querySelector('.login-screen')) return;
    window.location.reload();
  }, { once: true });

  try {
    store.set({ health: await api.health() });
  } catch (error) {
    toast(error.message, 'error');
  }

  buildSetupViews();
  buildResultViews();
  if (auth.admin) registerView(buildAdminView());
  mountRail();
  // Settled, not immediate: holding an arrow key in the feature table changes
  // the selection thirty times a second, and each rail render is four or five
  // requests. The first change still renders at once.
  const renderRail = settle(() => {
    if (railWanted() && !rail.isCollapsed()) rail.render();
  }, 140);
  store.on('selectedFeature', renderRail);

  store.on(['run', 'catalogue'], () => {
    renderRunChip();
    renderNav();
    // A result view with no completed run behind it (the run was deleted, or
    // a new one was started from another tab) has nothing true left to show.
    const current = views.get(store.get('view'));
    if (current && current.requiresRun && !isRunComplete()) showView(fallbackView());
  });
  store.on('busy', renderRunChip);
  store.on('view', renderNav);

  $('#btn-runs').addEventListener('click', showRunsDialog);
  $('.titlebar .app-icon').addEventListener('click', (event) => {
    dinoCrash();
    toggleDinoMenu(event.currentTarget);
  });

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
  // A link to a result tab whose run is still going, failed, or is gone
  // cannot open that tab; without a fallback nothing was shown at all.
  if (!showView(views.has(viewId) ? viewId : 'files')) showView(fallbackView());
}

boot();
