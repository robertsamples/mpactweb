/* Matrix-shaped plots: the clustered heatmap, the sample-correlation
 * heatmap, and the dendrogram (drawn from scipy's own segment coordinates).
 *
 * The heatmap is a real clustermap: the server sends the row-scaled matrix
 * already reordered by Ward linkage plus both dendrograms' segments, and the
 * layout below stitches them together the way seaborn's clustermap does --
 * feature dendrogram on the left, column dendrogram on top, matrix filling
 * the rest. */

import { store } from '../state.js';
import { AXIS, axisTitle, baseLayout, draw, imageFormat, message } from './base.js';

/* seaborn colormap names offered by the desktop's colour dropdowns, mapped
 * to Plotly-compatible colorscales. 'rocket' and 'mako' aren't built into
 * Plotly, so their control points are sampled from the seaborn originals. */
export const COLORSCALES = {
  rocket: [[0, '#03051A'], [0.25, '#4B1B33'], [0.5, '#A02F44'], [0.75, '#E56E4E'], [1, '#FAEBDD']],
  mako: [[0, '#0B0405'], [0.25, '#38477A'], [0.5, '#357BA2'], [0.75, '#48B4AC'], [1, '#DEF5E5']],
  flare: [[0, '#EDB081'], [0.5, '#DE6E56'], [1, '#8A226A']],
  crest: [[0, '#A5CD90'], [0.5, '#3E9F8C'], [1, '#20326C']],
  magma: 'Magma',
  viridis: 'Viridis',
};

export const colorscale = (name) => COLORSCALES[name] || COLORSCALES.rocket;

/** Convert scipy dendrogram icoord/dcoord segment pairs into a single
 *  Plotly line trace, using nulls to break between segments. */
function dendrogramTrace(icoord, dcoord, colours, orientation, axisIds, hover) {
  const x = [];
  const y = [];
  const line = { color: '#333', width: 1 };
  const traces = [];

  for (let i = 0; i < icoord.length; i += 1) {
    // Each scipy segment is the 4-point "staple" of one merge.
    const across = icoord[i];
    const height = dcoord[i];
    const colour = (colours && colours[i]) || '#333';
    const segX = orientation === 'top' ? across : height;
    const segY = orientation === 'top' ? height : across;
    const text = hover ? hover(i) : null;
    traces.push({
      type: 'scatter',
      mode: 'lines',
      x: segX,
      y: segY,
      line: { ...line, color: colour },
      // Without hover text a dendrogram gives no feedback at all: you can see
      // the shape but never read a merge height off it.
      hoverinfo: text ? 'text' : 'skip',
      hoveron: 'points+fills',
      text: text ? segX.map(() => text) : undefined,
      hovertemplate: text ? '%{text}<extra></extra>' : undefined,
      showlegend: false,
      xaxis: axisIds.x,
      yaxis: axisIds.y,
    });
  }
  if (!traces.length) traces.push({ type: 'scatter', mode: 'lines', x, y, hoverinfo: 'skip',
                                    showlegend: false, xaxis: axisIds.x, yaxis: axisIds.y });
  return traces;
}

export function heatmap(surface, payload, context) {
  const [rows, cols] = payload.shape;
  if (!rows || !cols) {
    message(surface, 'The filtered peak table is empty -- nothing to cluster.');
    return;
  }

  // scipy lays leaves out at 5, 15, 25, ... in icoord units; map that onto
  // the heatmap's 0..n-1 category positions so the dendrogram lines up with
  // the columns/rows beneath them.
  const toIndex = (value) => (value - 5) / 10;
  const colDendro = payload.col_dendrogram;
  const rowDendro = payload.row_dendrogram;

  const traces = [{
    type: 'heatmap',
    z: payload.z,
    x: payload.columns.map((label, index) => index),
    y: payload.features.map((label, index) => index),
    colorscale: colorscale(payload.colorscheme),
    colorbar: { title: 'Scaled\nabundance', titleside: 'right', thickness: 12, len: 0.72,
                x: 1.01, tickfont: { size: 10 } },
    hovertemplate: '%{customdata}<extra></extra>',
    customdata: payload.z.map((row, i) => row.map((value, j) =>
      `<b>${payload.features[i]}</b><br>${payload.columns[j]}<br>scaled: ${value.toFixed(3)}`)),
    xaxis: 'x', yaxis: 'y',
  }];

  for (const trace of dendrogramTrace(
    colDendro.icoord.map((seg) => seg.map(toIndex)), colDendro.dcoord, null, 'top',
    { x: 'x', y: 'y2' })) traces.push(trace);
  for (const trace of dendrogramTrace(
    rowDendro.icoord.map((seg) => seg.map(toIndex)), rowDendro.dcoord, null, 'left',
    { x: 'x2', y: 'y' })) traces.push(trace);

  // Column labels get dropped once they can no longer be read; the desktop
  // hides feature (row) tick labels for the same reason.
  const showColumnLabels = cols <= 60;

  // Yellow outline on the selected feature's row -- the web form of the
  // desktop's `parent.highlight['heatmap']` line artist. Rows are one unit
  // tall in category coordinates, so the band spans idx +/- 0.5.
  const selectedRow = payload.features.indexOf(context.selectedFeature);
  const shapes = [];
  if (selectedRow !== -1) {
    shapes.push({
      type: 'rect', xref: 'x', yref: 'y',
      x0: -0.5, x1: cols - 0.5,
      y0: selectedRow - 0.5, y1: selectedRow + 0.5,
      line: { color: '#f2d024', width: 2 }, fillcolor: 'rgba(0,0,0,0)',
      layer: 'above',
    });
  }

  draw(surface, traces, baseLayout({
    margin: { l: 74, r: 90, t: 12, b: showColumnLabels ? 140 : 40 },
    showlegend: false,
    shapes,
    xaxis: {
      domain: [0.1, 1], anchor: 'y', showgrid: false, zeroline: false,
      showline: true, mirror: true, linecolor: '#3c3c3c',
      tickmode: showColumnLabels ? 'array' : 'auto',
      tickvals: showColumnLabels ? payload.columns.map((_, i) => i) : undefined,
      ticktext: showColumnLabels ? payload.columns : undefined,
      tickangle: -90, tickfont: { size: 10 },
    },
    yaxis: {
      domain: [0, 0.88], anchor: 'x', showgrid: false, zeroline: false,
      showline: true, mirror: true, linecolor: '#3c3c3c',
      showticklabels: false, title: axisTitle('Feature'),
    },
    xaxis2: { domain: [0, 0.095], anchor: 'y', showgrid: false, zeroline: false,
              showticklabels: false, autorange: 'reversed' },
    yaxis2: { domain: [0.89, 1], anchor: 'x', showgrid: false, zeroline: false,
              showticklabels: false },
  }), imageFormat('heatmap'));

  if (surface._plotly && surface.on) {
    surface.removeAllListeners?.('plotly_click');
    surface.on('plotly_click', (event) => {
      const point = event.points && event.points[0];
      if (!point || point.curveNumber !== 0) return;
      const feature = payload.features[point.pointIndex[0]];
      if (feature) context.onSelectFeature(feature);
    });
  }

  // Step through features in the clustered row order with the arrow keys or
  // W/S -- the desktop binds W/S to `mv_heatmap`, which walks the same
  // reordered index. The surface has to be focusable to receive them.
  surface.tabIndex = 0;
  if (surface._heatmapKeys) surface.removeEventListener('keydown', surface._heatmapKeys);
  surface._heatmapKeys = (event) => {
    const step = { ArrowDown: 1, s: 1, S: 1, ArrowUp: -1, w: -1, W: -1 }[event.key];
    if (step === undefined) return;
    event.preventDefault();
    const current = payload.features.indexOf(store.get('selectedFeature'));
    const next = current === -1
      ? (step > 0 ? 0 : payload.features.length - 1)
      : Math.max(0, Math.min(payload.features.length - 1, current + step));
    context.onSelectFeature(payload.features[next]);
  };
  surface.addEventListener('keydown', surface._heatmapKeys);
}

export function samplecorr(surface, payload) {
  if (!payload.labels.length) {
    message(surface, 'Not enough samples to correlate.');
    return;
  }
  const showLabels = payload.labels.length <= 60;
  draw(surface, [{
    type: 'heatmap',
    z: payload.z,
    x: payload.labels,
    y: payload.labels,
    zmin: payload.range[0],
    zmax: payload.range[1],
    colorscale: colorscale(payload.colorscheme),
    colorbar: { thickness: 12, len: 0.8, tickfont: { size: 10 } },
    hovertemplate: '%{y}<br>%{x}<br>%{z:.3f}<extra></extra>',
  }], baseLayout({
    title: { text: payload.method, font: { size: 13 } },
    margin: { l: showLabels ? 130 : 50, r: 40, t: 40, b: showLabels ? 130 : 50 },
    showlegend: false,
    xaxis: { ...AXIS, tickangle: -90, showticklabels: showLabels, tickfont: { size: 10 } },
    yaxis: { ...AXIS, autorange: 'reversed', showticklabels: showLabels,
             tickfont: { size: 10 } },
  }), imageFormat('samplecorr'));
}

export function dendrogram(surface, payload) {
  if (!payload.icoord.length) {
    message(surface, 'Not enough leaves to build a dendrogram.');
    return;
  }
  const PURITY_MEANING = {
    green: 'every leaf below shares one group',
    magenta: 'this merge splits a group apart',
  };
  const traces = dendrogramTrace(
    payload.icoord, payload.dcoord, payload.link_colours, 'top', { x: 'x', y: 'y' },
    (i) => {
      const height = payload.dcoord[i][1];
      const colour = payload.link_colours ? payload.link_colours[i] : null;
      const meaning = PURITY_MEANING[colour];
      const bootstrap = payload.bootstrap_values && payload.link_order
        ? payload.bootstrap_values[payload.link_order[i]] : null;
      return [
        `Merge distance: ${height.toFixed(4)}`,
        meaning ? meaning : null,
        bootstrap ? `AU ${Math.round(bootstrap.au * 100)} / BP ${Math.round(bootstrap.bp * 100)}`
                  : null,
      ].filter(Boolean).join('<br>');
    });

  // An invisible marker per leaf, so hovering a leaf names it (and its group)
  // rather than showing nothing.
  const leafPositionsForHover = payload.leaves.map((_, index) => 5 + index * 10);
  traces.push({
    type: 'scatter', mode: 'markers',
    x: leafPositionsForHover,
    y: payload.leaves.map(() => 0),
    marker: { size: 12, color: 'rgba(0,0,0,0)' },
    text: payload.leaves.map((name, i) => {
      const group = (payload.leaf_groups || {})[name];
      return `<b>${payload.labels[i]}</b>${group ? `<br>${group}` : ''}`;
    }),
    hovertemplate: '%{text}<extra></extra>',
    showlegend: false,
  });

  // Bootstrap AU/BP values sit at each merge's apex, the same annotation the
  // desktop's PvClust plot writes above every node.
  const annotations = [];
  if (payload.bootstrap_values && payload.link_order) {
    payload.icoord.forEach((across, i) => {
      const values = payload.bootstrap_values[payload.link_order[i]];
      if (!values) return;
      annotations.push({
        x: (across[1] + across[2]) / 2,
        y: payload.dcoord[i][1],
        text: `${Math.round(values.au * 100)} / ${Math.round(values.bp * 100)}`,
        showarrow: false, yshift: 7,
        font: { size: Math.max(7, Math.min(9, 700 / payload.leaves.length)), color: '#2f4f4f' },
      });
    });
  }

  const leafPositions = payload.leaves.map((_, index) => 5 + index * 10);
  draw(surface, traces, baseLayout({
    title: payload.title ? { text: payload.title, font: { size: 13 } } : undefined,
    margin: { l: 60, r: 20, t: payload.title ? 42 : 20, b: 150 },
    showlegend: false,
    annotations,
    xaxis: {
      ...AXIS,
      tickmode: 'array', tickvals: leafPositions, ticktext: payload.labels,
      tickangle: -90, tickfont: { size: Math.max(7, Math.min(11, 420 / payload.leaves.length)) },
    },
    // Distances start at zero: scipy's dendrogram pins the bottom of the
    // axis to 0, so leaves sit on the axis line rather than floating
    // above a negative stub of padding.
    yaxis: { ...AXIS, title: axisTitle('Distance'), rangemode: 'tozero' },
  }), imageFormat('dendrogram'));
}
