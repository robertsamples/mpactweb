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

/** Convert scipy dendrogram icoord/dcoord segments into Plotly line traces.
 *
 *  With per-segment colours or hover text each merge is its own trace, which
 *  is what the standalone dendrogram (tens of leaves) wants. Without either
 *  -- the two dendrograms beside the heatmap -- every merge goes into one
 *  trace, broken with nulls: a heatmap of three thousand features otherwise
 *  carried three thousand traces, and Plotly's cost is per trace. */
function dendrogramTrace(icoord, dcoord, colours, orientation, axisIds, hover) {
  const line = { color: '#333', width: 1 };

  if (!colours && !hover) {
    const x = [];
    const y = [];
    for (let i = 0; i < icoord.length; i += 1) {
      const across = icoord[i];
      const height = dcoord[i];
      const segX = orientation === 'top' ? across : height;
      const segY = orientation === 'top' ? height : across;
      for (let k = 0; k < segX.length; k += 1) { x.push(segX[k]); y.push(segY[k]); }
      x.push(null);
      y.push(null);
    }
    return [{ type: 'scatter', mode: 'lines', x, y, line, hoverinfo: 'skip',
              showlegend: false, xaxis: axisIds.x, yaxis: axisIds.y }];
  }

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
  if (!traces.length) traces.push({ type: 'scatter', mode: 'lines', x: [], y: [], hoverinfo: 'skip',
                                    showlegend: false, xaxis: axisIds.x, yaxis: axisIds.y });
  return traces;
}

/** Where each feature sits in the clustered order, built once per payload. */
function featureIndex(payload) {
  if (!payload._featureIndex) {
    payload._featureIndex = new Map(payload.features.map((feature, index) => [feature, index]));
  }
  return payload._featureIndex;
}

// Highlight bands are layout shapes; past this many a relayout stops being
// instant, and that many separate bands cannot be told apart anyway.
const MAX_BANDS = 400;

export function heatmap(surface, payload, context) {
  const [rows, cols] = payload.shape;
  if (!rows || !cols) {
    message(surface, 'The filtered peak table is empty -- nothing to cluster.');
    return;
  }
  // The server averages neighbouring features into bins once there are more
  // of them than the plot has pixels (see plotdata/heatmap.py). `drawn` is
  // the number of matrix rows; drawn row r covers features [r*bin, (r+1)*bin).
  const bin = payload.bin_size || 1;
  const drawn = payload.z.length;
  const rowOf = (index) => Math.floor(index / bin);
  const featuresIn = (row) => payload.features.slice(row * bin, (row + 1) * bin);

  // A highlight band per selected row. Rows are one unit tall in axis
  // coordinates, so a band spans row +/- 0.5. This is the web form of the
  // desktop's `parent.highlight['heatmap']` line artist.
  const band = (row, colour) => ({
    type: 'rect', xref: 'x', yref: 'y',
    x0: -0.5, x1: cols - 0.5, y0: row - 0.5, y1: row + 0.5,
    line: { color: colour, width: 2 }, fillcolor: 'rgba(0,0,0,0)',
    layer: 'above',
  });
  // Flagged rows in their flag's colour, then the selected rows in yellow on
  // top (a row that is both reads as selected).
  const highlightShapes = (selected, flags) => {
    const index = featureIndex(payload);
    const flagged = new Map();          // drawn row -> colour
    if (flags && flags.size) {
      for (const [feature, colour] of flags) {
        const at = index.get(feature);
        if (at !== undefined && !flagged.has(rowOf(at))) flagged.set(rowOf(at), colour);
        if (flagged.size >= MAX_BANDS) break;
      }
    }
    const bands = new Set();
    if (selected && selected.size) {
      for (const feature of selected) {
        const at = index.get(feature);
        if (at !== undefined) bands.add(rowOf(at));
        if (bands.size >= MAX_BANDS) break;
      }
    }
    return [...[...flagged].filter(([row]) => !bands.has(row)).map(([row, colour]) => band(row, colour)),
            ...[...bands].map((row) => band(row, '#f2d024'))];
  };

  // Changing the selection must not redraw the matrix. A `Plotly.react` here
  // re-diffs every cell, which is what made the arrow keys crawl on a real
  // dataset; the highlight is a layout shape, so a relayout is all it needs.
  if (surface._plotly && surface._heatmapPayload === payload) {
    Plotly.relayout(surface, { shapes: highlightShapes(context.selectedFeatures, context.flags) });
    return;
  }
  surface._heatmapPayload = payload;

  // scipy lays leaves out at 5, 15, 25, ... in icoord units; map that onto
  // the heatmap's 0..n-1 positions so the dendrograms line up with the
  // columns and rows beside them. The row dendrogram has a leaf per feature,
  // so with binning its positions are scaled onto the drawn rows.
  const toIndex = (value) => (value - 5) / 10;
  const toRow = (value) => (toIndex(value) + 0.5) / bin - 0.5;
  const colDendro = payload.col_dendrogram;
  const rowDendro = payload.row_dendrogram;

  // Hover names the cell from the axes' own labels: each axis carries one
  // tick label per position (shown or not), and Plotly's hover text for an
  // axis value is its tick label. Building a string per cell instead, as
  // this used to, was rows x columns strings before anything could be drawn.
  const rowLabels = bin === 1 ? payload.features : Array.from({ length: drawn }, (_, row) => {
    const members = featuresIn(row);
    return members.length > 1 ? `${members[0]} … ${members[members.length - 1]} `
                                + `(${members.length} features, averaged)` : members[0];
  });
  const positions = (count) => Array.from({ length: count }, (_, i) => i);

  const traces = [{
    type: 'heatmap',
    z: payload.z,
    x: positions(cols),
    y: positions(drawn),
    colorscale: colorscale(payload.colorscheme),
    // The title has to be an object: this Plotly ignores the bare-string
    // form (and `titleside`), which left the colour bar with no label at all.
    colorbar: { title: { text: 'Scaled abundance', side: 'right', font: { size: 11 } },
                thickness: 12, len: 0.72, x: 1.01, tickfont: { size: 10 } },
    hovertemplate: '<b>%{y}</b><br>%{x}<br>scaled: %{z:.3f}<extra></extra>',
    xaxis: 'x', yaxis: 'y',
  }];

  for (const trace of dendrogramTrace(
    colDendro.icoord.map((seg) => seg.map(toIndex)), colDendro.dcoord, null, 'top',
    { x: 'x', y: 'y2' })) traces.push(trace);
  for (const trace of dendrogramTrace(
    rowDendro.icoord.map((seg) => seg.map(toRow)), rowDendro.dcoord, null, 'left',
    { x: 'x2', y: 'y' })) traces.push(trace);

  // Column labels get dropped once they can no longer be read; the desktop
  // hides feature (row) tick labels for the same reason.
  const showColumnLabels = cols <= 60;

  draw(surface, traces, baseLayout({
    margin: { l: 74, r: 90, t: 12, b: showColumnLabels ? 140 : 40 },
    showlegend: false,
    shapes: highlightShapes(context.selectedFeatures, context.flags),
    // Pinned to the cells' own extent. Left to autorange, the row
    // dendrogram (a scatter trace on this same y axis) pads the range a few
    // pixels past the data, and that padding shows as bare axis below the
    // bottom row of colours.
    xaxis: {
      domain: [0.1, 1], anchor: 'y', showgrid: false, zeroline: false,
      range: [-0.5, cols - 0.5],
      showline: true, mirror: true, linecolor: '#3c3c3c',
      tickmode: 'array', tickvals: positions(cols), ticktext: payload.columns,
      showticklabels: showColumnLabels, ticks: showColumnLabels ? 'outside' : '',
      tickangle: -90, tickfont: { size: 10 },
    },
    yaxis: {
      domain: [0, 0.88], anchor: 'x', showgrid: false, zeroline: false,
      range: [-0.5, drawn - 0.5],
      showline: true, mirror: true, linecolor: '#3c3c3c',
      tickmode: 'array', tickvals: positions(drawn), ticktext: rowLabels,
      showticklabels: false, ticks: '', title: axisTitle('Feature'),
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
      const members = featuresIn(point.pointIndex[0]);
      if (!members.length) return;
      // A binned row stands for several features: clicking it selects them.
      if (members.length === 1) context.onSelectFeature(members[0], event.event);
      else context.onSelectFeatures(members, event.event);
    });
  }

  // Plotly's drag layer calls preventDefault on mousedown, which stops this
  // div taking focus the way a tabindex'd element normally would when you
  // click inside it -- so without this, clicking a cell selects the feature
  // but the arrow keys below still go to the page.
  if (!surface._heatmapFocus) {
    surface._heatmapFocus = () => surface.focus({ preventScroll: true });
    surface.addEventListener('pointerdown', surface._heatmapFocus);
  }

  // Step through features in the clustered row order with the arrow keys or
  // W/S -- the desktop binds W/S to `mv_heatmap`, which walks the same
  // reordered index. The surface has to be focusable to receive them.
  surface.tabIndex = 0;
  if (surface._heatmapKeys) surface.removeEventListener('keydown', surface._heatmapKeys);
  surface._heatmapKeys = (event) => {
    // Feature 0 is drawn at the *bottom*: the y axis is not reversed, so the
    // row dendrogram beside the matrix lines up with it. A key that moves the
    // selection down the screen therefore moves it down the index, not up --
    // getting this backwards is what made the arrows feel inverted.
    const step = { ArrowDown: -1, s: -1, S: -1, ArrowUp: 1, w: 1, W: 1 }[event.key];
    if (step === undefined || event.ctrlKey || event.metaKey || event.altKey) return;
    event.preventDefault();
    const last = payload.features.length - 1;
    const current = featureIndex(payload).get(store.get('selectedFeature'));
    const next = current === undefined
      ? (step < 0 ? last : 0)      // nothing picked yet: enter from that edge
      : Math.max(0, Math.min(last, current + step));
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
