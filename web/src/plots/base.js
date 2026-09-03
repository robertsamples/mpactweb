/* Shared Plotly configuration and plot-surface plumbing.
 *
 * The desktop draws into a matplotlib canvas with a navigation toolbar
 * (pan/zoom/save) under every plot; Plotly's mode bar is the direct
 * equivalent, so it is kept rather than hidden, and the light plot ground
 * matches the desktop's canvas colour so plots look the same lifted out of
 * either app. */

export const PLOT_BG = '#e3e3e3';

export const CONFIG = {
  responsive: true,
  displaylogo: false,
  // Always visible, not on hover: this is the equivalent of the matplotlib
  // navigation toolbar under every desktop plot (pan, zoom, reset, save),
  // and a control you have to discover by hovering is a control most people
  // never find.
  displayModeBar: true,
  modeBarButtonsToRemove: ['select2d', 'lasso2d', 'toggleSpikelines'],
  toImageButtonOptions: { format: 'png', scale: 2, filename: 'mpact-plot' },
};

export function baseLayout(overrides = {}) {
  return {
    paper_bgcolor: PLOT_BG,
    plot_bgcolor: PLOT_BG,
    font: { family: 'Segoe UI, Bahnschrift, system-ui, sans-serif', size: 12, color: '#222' },
    margin: { l: 68, r: 24, t: 34, b: 60 },
    hovermode: 'closest',
    showlegend: true,
    legend: { bgcolor: 'rgba(255,255,255,0.65)', bordercolor: '#bbb', borderwidth: 1 },
    xaxis: { gridcolor: '#cfcfcf', zerolinecolor: '#b0b0b0', automargin: true },
    yaxis: { gridcolor: '#cfcfcf', zerolinecolor: '#b0b0b0', automargin: true },
    ...overrides,
  };
}

export function axisTitle(text) {
  return { text, font: { size: 13, family: 'Bahnschrift, Segoe UI, sans-serif' } };
}

/** Replace a surface's contents with a centred message. */
export function message(surface, text, kind = '') {
  // purge() releases Plotly's state for this div; the flag reset below makes
  // the next draw() clear the placeholder before re-initialising.
  if (surface._plotly) { try { Plotly.purge(surface); } catch { /* not a plot yet */ } }
  surface.innerHTML = '';
  const node = document.createElement('div');
  node.className = `placeholder ${kind}`;
  node.textContent = text;
  surface.append(node);
  surface._plotly = false;
}

export function ensurePlotly(surface) {
  if (typeof window.Plotly === 'undefined') {
    message(surface,
            'Plot library could not be loaded. If this server is offline, run '
            + 'scripts/fetch_web_vendor.py to vendor Plotly locally.', 'error');
    return false;
  }
  return true;
}

/** Draw (or redraw) a plot into a surface element.
 *
 * The container is only emptied when it does *not* already hold a plot.
 * Wiping it unconditionally would tear out the DOM Plotly still believes it
 * owns, and the following `Plotly.react` would then render nothing -- which
 * is exactly what happened when a selection triggered a redraw. Clearing
 * only on the placeholder-to-plot transition keeps `react`'s in-place
 * diffing (and the user's current zoom) intact.
 */
export function draw(surface, traces, layout, config = {}) {
  if (!ensurePlotly(surface)) return null;
  if (!surface._plotly) surface.innerHTML = '';
  surface._plotly = true;
  return Plotly.react(surface, traces, layout, { ...CONFIG, ...config });
}

/** Yellow selection marker, the web form of the desktop's
 *  `parent.highlight[currplt]` artist: one extra trace holding at most one
 *  point, updated in place as the selection moves between tabs. */
export function highlightTrace(x, y) {
  return {
    type: 'scattergl',
    mode: 'markers',
    x: x === null || x === undefined ? [] : [x],
    y: y === null || y === undefined ? [] : [y],
    marker: { size: 15, color: 'rgba(0,0,0,0)', line: { color: '#f2d024', width: 3 } },
    hoverinfo: 'skip',
    showlegend: false,
    name: 'selection',
  };
}

/** Wire click-to-select. `resolve(point)` returns the compound id, or null. */
export function onSelect(surface, resolve, onPick) {
  if (!surface._plotly || !surface.on) return;
  surface.removeAllListeners?.('plotly_click');
  surface.on('plotly_click', (event) => {
    const point = event.points && event.points[0];
    if (!point) return;
    const id = resolve(point);
    if (id) onPick(id);
  });
}

/** Sensible axis padding when a payload gives no explicit range.
 *  The desktop hardcodes several axis ranges (see plotdata/scatter.py); the
 *  web app autoscales instead so nothing is clipped off-screen. */
export function paddedRange(values, pad = 0.04) {
  const finite = values.filter((v) => typeof v === 'number' && Number.isFinite(v));
  if (!finite.length) return undefined;
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  if (min === max) return [min - 1, max + 1];
  const span = (max - min) * pad;
  return [min - span, max + span];
}
