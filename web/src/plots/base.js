/* Shared Plotly configuration and plot-surface plumbing.
 *
 * The desktop draws into a matplotlib canvas with a navigation toolbar
 * (pan/zoom/save) in a strip under every plot. Plotly's floating mode bar is
 * therefore switched off and plots/navbar.js draws that strip instead, and
 * the light plot ground matches the desktop's canvas colour so plots look the
 * same lifted out of either app. */

// The desktop draws a transparent figure (facecolor alpha 0) over a Qt page
// whose background is rgba(225,225,225) -- so this exact grey *is* the plot
// background there, and matching it is what makes the plot region read as
// one continuous working surface rather than a white card on a dark page.
export const PLOT_BG = '#e1e1e1';

export const CONFIG = {
  responsive: true,
  displaylogo: false,
  // Plotly's own bar floats over the plot's top-right corner. The desktop
  // puts matplotlib's navigation toolbar in a strip *under* the canvas, so
  // that bar is switched off here and plots/navbar.js draws the real one in
  // the right place, driving the same Plotly APIs.
  displayModeBar: false,
  // Vector export by default -- these plots end up in figures. The heatmap
  // opts out (see `imageFormat`): a few thousand features x tens of columns
  // is hundreds of thousands of SVG rects, which no editor opens happily.
  toImageButtonOptions: { format: 'svg', scale: 2, filename: 'mpact-plot' },
};

/** Per-plot export config: SVG everywhere except raster-heavy plots. */
export function imageFormat(name) {
  const raster = name === 'heatmap' || name === 'samplecorr';
  return {
    toImageButtonOptions: {
      format: raster ? 'png' : 'svg',
      scale: 2,
      filename: `mpact-${name}`,
    },
  };
}

/* matplotlib draws a full box around the axes (all four spines) with ticks
 * pointing outwards, and no gridlines unless the plot explicitly calls
 * `ax.grid()`. Plotly's defaults are the opposite on both counts, so the
 * axis template below restores matplotlib's, and the handful of plots whose
 * desktop counterpart calls `.grid()` opt back in with `gridded()`.
 */
export const AXIS = {
  showline: true,
  mirror: true,
  linecolor: '#3c3c3c',
  linewidth: 1,
  ticks: 'outside',
  tickcolor: '#3c3c3c',
  ticklen: 4,
  showgrid: false,
  zeroline: false,
  automargin: true,
};

/** Axis overrides for the plots the desktop draws with a grid. */
export const gridded = (extra = {}) => ({
  ...AXIS, showgrid: true, gridcolor: '#c4c4c4', ...extra,
});

export function baseLayout(overrides = {}) {
  return {
    // Transparent figure over the page's grey, exactly as the desktop draws
    // it (figure facecolor alpha 0 on a 225-grey page).
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { family: 'Segoe UI, Bahnschrift, system-ui, sans-serif', size: 12, color: '#222' },
    margin: { l: 68, r: 20, t: 20, b: 58 },
    hovermode: 'closest',
    showlegend: true,
    // matplotlib's `ax.legend()` draws the key *inside* the axes (loc='best',
    // which lands top-right on an empty corner), on a translucent white patch
    // with a thin border. Plotly's default parks it outside on the right,
    // which both shrinks the data area and stops the plot reading as the
    // desktop's. This is matplotlib's placement.
    legend: {
      x: 0.985, xanchor: 'right', y: 0.985, yanchor: 'top',
      bgcolor: 'rgba(255,255,255,0.8)', bordercolor: '#7a7a7a', borderwidth: 1,
      font: { size: 11 }, itemsizing: 'constant',
    },
    xaxis: { ...AXIS },
    yaxis: { ...AXIS },
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

/** Resolves once Plotly has loaded (or has definitely failed to).
 *
 *  index.html loads the library with an injected script, which does not hold
 *  up the app's own modules. On a link straight to a plot, the plot's data
 *  can therefore arrive before the 4 MB library has, and drawing at that
 *  moment put "Plot library could not be loaded" on screen for a library
 *  that was a second away. Callers wait on this before their first draw. */
let plotlyWait = null;
export function plotlyReady(timeout = 30000) {
  const settled = () => typeof window.Plotly !== 'undefined' || window.__plotlyFailed;
  if (settled()) return Promise.resolve();
  if (!plotlyWait) {
    plotlyWait = new Promise((resolve) => {
      const started = performance.now();
      const check = () => {
        if (settled() || performance.now() - started > timeout) {
          plotlyWait = null;
          resolve();
        } else {
          setTimeout(check, 80);
        }
      };
      check();
    });
  }
  return plotlyWait;
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

/** A 3D selection marker: `highlightTrace`'s scattergl has no meaning inside
 *  a scene, so the ring is an outlined scatter3d point instead. */
export function highlightTrace3d(x, y, z) {
  const empty = x === null || x === undefined;
  return {
    type: 'scatter3d',
    mode: 'markers',
    x: empty ? [] : [x],
    y: empty ? [] : [y],
    z: empty ? [] : [z],
    marker: { size: 7, color: '#f2d024', opacity: 1,
              line: { color: '#7a6410', width: 2 } },
    hoverinfo: 'skip',
    showlegend: false,
    name: 'selection',
  };
}

/**
 * Draw a plot whose only per-selection change is where the highlight sits.
 *
 * `signature` is the list of things the drawn traces depend on (the payload,
 * plus any live control that changes classification). While it is unchanged,
 * moving the selection restyles the single highlight trace in place; building
 * every point's colour and hover text again, and diffing the result through
 * `Plotly.react`, is what made a moved marker cost a full replot.
 *
 * `build()` returns `{traces, layout, config}` and is called only when a real
 * redraw is needed. The highlight is appended by this function, so it is
 * always the trace named 'selection'.
 */
export function drawSelectable(surface, signature, build, point, options = {}) {
  const previous = surface._selectionSignature;
  const unchanged = surface._plotly && Array.isArray(previous)
    && previous.length === signature.length
    && previous.every((value, i) => value === signature[i]);

  const make = options.marker3d ? highlightTrace3d : highlightTrace;
  const coords = point ? [point.x, point.y, point.z] : [];

  if (unchanged) {
    const index = (surface.data || []).findIndex((trace) => trace.name === 'selection');
    if (index !== -1) {
      const update = { x: [coords.length ? [coords[0]] : []],
                       y: [coords.length ? [coords[1]] : []] };
      if (options.marker3d) update.z = [coords.length ? [coords[2]] : []];
      Plotly.restyle(surface, update, [index]);
      return true;
    }
  }

  const { traces, layout, config } = build();
  traces.push(make(...(coords.length ? coords : [null, null, null])));
  draw(surface, traces, layout, config);
  surface._selectionSignature = signature;
  return false;
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

/** Range for a quantity with a real zero: the axis starts *at* zero and the
 *  top is padded, which is what the desktop's fixed `set_xlim(0, ...)` /
 *  `set_ylim(0, ...)` calls do, minus their hardcoded ceilings. */
export function originRange(values, pad = 0.05) {
  const bounds = extent(values);
  if (!bounds) return undefined;
  const max = Math.max(bounds.max, 0);
  return [0, max > 0 ? max * (1 + pad) : 1];
}

/** Min and max of the finite numbers in `values`, or null if there are none.
 *
 *  A loop rather than `Math.min(...values)`: spreading passes every point as
 *  an argument, and V8 gives up with a stack overflow well inside the 100,000
 *  features this app is willing to put in a table.
 */
export function extent(values) {
  let min = Infinity;
  let max = -Infinity;
  let seen = false;
  for (const value of values) {
    if (typeof value !== 'number' || !Number.isFinite(value)) continue;
    if (value < min) min = value;
    if (value > max) max = value;
    seen = true;
  }
  return seen ? { min, max } : null;
}

/** Sensible axis padding when a payload gives no explicit range.
 *  The desktop hardcodes several axis ranges (see plotdata/scatter.py); the
 *  web app autoscales instead so nothing is clipped off-screen. */
export function paddedRange(values, pad = 0.04) {
  const bounds = extent(values);
  if (!bounds) return undefined;
  const { min, max } = bounds;
  if (min === max) return [min - 1, max + 1];
  const span = (max - min) * pad;
  return [min - span, max + span];
}
