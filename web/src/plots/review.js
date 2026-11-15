/* Data Review and Group Analysis plots: the CV rarefaction curve, the filter
 * treemap, the UpSet set plot, and the per-feature abundance and spectrum
 * views from Feature Info. */

import { num } from '../util.js';
import { axisTitle, baseLayout, draw, imageFormat, message } from './base.js';

export function cvplot(surface, payload) {
  const traces = payload.series.map((series) => ({
    type: 'scatter',
    mode: 'lines',
    name: series.name,
    x: series.x,
    y: series.y,
    line: { color: series.colour, width: 2 },
    hovertemplate: `${series.name}<br>CV %{x:.3f}<br>%{y:.1f}%<extra></extra>`,
  }));

  // The desktop's two guide lines: a vertical at the CV threshold, and a
  // horizontal at the percentage of features that threshold retains.
  const shapes = [{
    type: 'line', x0: payload.threshold.cv, x1: payload.threshold.cv,
    y0: 0, y1: 100, line: { color: 'dimgrey', width: 1 }, layer: 'below',
  }];
  if (payload.threshold.percent !== null && payload.threshold.percent !== undefined) {
    shapes.push({
      type: 'line', x0: payload.x_range[0], x1: payload.x_range[1],
      y0: payload.threshold.percent, y1: payload.threshold.percent,
      line: { color: 'dimgrey', width: 1 }, layer: 'below',
    });
  }

  draw(surface, traces, baseLayout({
    // Both curves climb into the top-right corner, so matplotlib's loc='best'
    // drops the key at the bottom right here. Plotly has no 'best'.
    legend: { ...baseLayout().legend, y: 0.03, yanchor: 'bottom' },
    xaxis: { ...baseLayout().xaxis, title: axisTitle(payload.x_label), range: payload.x_range },
    yaxis: { ...baseLayout().yaxis, title: axisTitle(payload.y_label), range: payload.y_range },
    shapes,
  }), imageFormat('cvplot'));
}

/* Squarified treemap layout (Bruls, Huizing & van Wijk). The desktop uses the
 * `squarify` Python package for exactly this; reimplementing the ~40-line
 * layout here keeps the treemap interactive instead of shipping a raster. */
function squarify(values, x, y, width, height) {
  const total = values.reduce((sum, value) => sum + value, 0);
  if (!total) return [];
  const scaled = values.map((value) => (value * width * height) / total);
  const rects = [];

  const worst = (row, side) => {
    const sum = row.reduce((a, b) => a + b, 0);
    const max = Math.max(...row);
    const min = Math.min(...row);
    return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min));
  };

  let remaining = [...scaled];
  let indices = scaled.map((_, i) => i);
  let cursor = { x, y, width, height };

  while (remaining.length) {
    const side = Math.min(cursor.width, cursor.height);
    const row = [remaining[0]];
    const rowIndices = [indices[0]];
    let rest = remaining.slice(1);
    let restIndices = indices.slice(1);

    while (rest.length && worst(row, side) >= worst([...row, rest[0]], side)) {
      row.push(rest[0]);
      rowIndices.push(restIndices[0]);
      rest = rest.slice(1);
      restIndices = restIndices.slice(1);
    }

    const rowSum = row.reduce((a, b) => a + b, 0);
    if (cursor.width >= cursor.height) {
      const rowWidth = rowSum / cursor.height;
      let offset = cursor.y;
      row.forEach((value, i) => {
        const rectHeight = value / rowWidth;
        rects.push({ index: rowIndices[i], x: cursor.x, y: offset, w: rowWidth, h: rectHeight });
        offset += rectHeight;
      });
      cursor = { x: cursor.x + rowWidth, y: cursor.y,
                 width: cursor.width - rowWidth, height: cursor.height };
    } else {
      const rowHeight = rowSum / cursor.width;
      let offset = cursor.x;
      row.forEach((value, i) => {
        const rectWidth = value / rowHeight;
        rects.push({ index: rowIndices[i], x: offset, y: cursor.y, w: rectWidth, h: rowHeight });
        offset += rectWidth;
      });
      cursor = { x: cursor.x, y: cursor.y + rowHeight,
                 width: cursor.width, height: cursor.height - rowHeight };
    }
    remaining = rest;
    indices = restIndices;
  }
  return rects;
}

export function treemap(surface, payload) {
  const items = payload.items.filter((item) => item.value > 0);
  if (!items.length) {
    message(surface, 'No filtering was applied, so there is nothing to break down.');
    return;
  }
  const rects = squarify(items.map((item) => item.value), 0, 0, 100, 100);

  const shapes = rects.map((rect) => ({
    type: 'rect',
    x0: rect.x, y0: rect.y, x1: rect.x + rect.w, y1: rect.y + rect.h,
    fillcolor: items[rect.index].colour,
    opacity: 0.3,
    line: { color: '#444', width: 1 },
  }));

  const annotations = rects.map((rect) => {
    const item = items[rect.index];
    return {
      x: rect.x + rect.w / 2, y: rect.y + rect.h / 2,
      text: `${item.label}<br>${item.value.toLocaleString()}<br>${item.percent}%`,
      showarrow: false,
      font: { size: Math.max(8, Math.min(13, Math.sqrt(rect.w * rect.h) / 2.4)), color: '#111' },
    };
  });

  // An invisible marker per tile gives the tiles hover text; shapes alone
  // can't carry a tooltip.
  draw(surface, [{
    type: 'scatter', mode: 'markers',
    x: rects.map((r) => r.x + r.w / 2),
    y: rects.map((r) => r.y + r.h / 2),
    marker: { size: 1, color: 'rgba(0,0,0,0)' },
    text: rects.map((r) => {
      const item = items[r.index];
      return `<b>${item.label}</b><br>${item.value.toLocaleString()} features (${item.percent}%)`;
    }),
    hovertemplate: '%{text}<extra></extra>',
    showlegend: false,
  }], baseLayout({
    margin: { l: 10, r: 10, t: 10, b: 10 },
    showlegend: false,
    shapes,
    annotations,
    xaxis: { range: [0, 100], visible: false, fixedrange: true },
    yaxis: { range: [0, 100], visible: false, fixedrange: true },
  }), imageFormat('treemap'));
}

export function upset(surface, payload, context, options = {}) {
  const limit = options.limit || 30;
  const rows = payload.intersections.slice(0, limit);
  const categories = payload.categories;
  if (!rows.length || !categories.length) {
    message(surface, 'No group combinations to show.');
    return;
  }

  // Classic UpSet layout, matching the `upsetplot` package the desktop uses:
  //   top right   intersection sizes (bars)
  //   bottom right membership matrix (dots joined by a line)
  //   bottom left  set sizes (horizontal bars, growing leftwards)
  // Categories are ordered by set size, largest at the top, and the matrix
  // rows are banded so a row can be followed across a wide plot.
  const ordered = [...categories].sort(
    (a, b) => (payload.totals[b] || 0) - (payload.totals[a] || 0));
  const rowOf = new Map(ordered.map((name, index) => [name, index]));

  const dotX = [];
  const dotY = [];
  const dotOn = [];
  const dotText = [];
  const connectors = [];
  const bands = [];

  rows.forEach((row, column) => {
    const members = new Set(row.groups);
    const present = [];
    ordered.forEach((category) => {
      const level = rowOf.get(category);
      dotX.push(column);
      dotY.push(level);
      dotOn.push(members.has(category));
      dotText.push(`<b>${category}</b><br>${members.has(category) ? 'in' : 'not in'} this set`
                   + `<br>${row.size} features`);
      if (members.has(category)) present.push(level);
    });
    if (present.length > 1) {
      connectors.push({
        type: 'line', xref: 'x2', yref: 'y2',
        x0: column, x1: column, y0: Math.min(...present), y1: Math.max(...present),
        line: { color: '#333', width: 2 }, layer: 'below',
      });
    }
  });

  // Alternating row bands behind the matrix.
  ordered.forEach((_, level) => {
    if (level % 2) return;
    bands.push({
      type: 'rect', xref: 'paper', yref: 'y2',
      x0: 0, x1: 1, y0: level - 0.5, y1: level + 0.5,
      fillcolor: 'rgba(0,0,0,0.05)', line: { width: 0 }, layer: 'below',
    });
  });

  const traces = [
    {
      type: 'bar',
      name: 'Intersection size',
      x: rows.map((_, i) => i),
      y: rows.map((row) => row.size),
      // Black, like every UpSet plot (and the desktop's): the accent colour
      // means "selected" everywhere else in this app.
      marker: { color: '#1c1c1c' },
      text: rows.map((row) => `${row.size}`),
      textposition: 'outside',
      textfont: { size: 9, color: '#333' },
      customdata: rows.map((row) =>
        `${row.groups.join(' ∩ ') || 'no group'}<br>${row.size} features (${row.percent}%)`),
      hovertemplate: '%{customdata}<extra></extra>',
      cliponaxis: false,
      showlegend: false,
      xaxis: 'x', yaxis: 'y',
    },
    {
      type: 'scatter', mode: 'markers',
      x: dotX, y: dotY,
      marker: { size: 10, color: dotOn.map((on) => (on ? '#333' : '#c9c9c9')) },
      text: dotText,
      hovertemplate: '%{text}<extra></extra>',
      showlegend: false,
      xaxis: 'x2', yaxis: 'y2',
    },
    {
      type: 'bar', orientation: 'h',
      name: 'Set size',
      x: ordered.map((name) => payload.totals[name] || 0),
      y: ordered.map((_, index) => index),
      // `bargap` is a layout-wide setting driven by the intersection bars, so
      // the horizontal bars get an explicit width instead -- otherwise they
      // fill their whole row band and read as blocks rather than bars.
      width: 0.55,
      marker: { color: '#7f8c8d' },
      hovertemplate: '%{customdata}<extra></extra>',
      customdata: ordered.map((name) =>
        `<b>${name}</b><br>${(payload.totals[name] || 0).toLocaleString()} features total`),
      showlegend: false,
      xaxis: 'x3', yaxis: 'y3',
    },
  ];

  const matrixLeft = 0.3;
  // Row band height scales with the number of sets. A fixed split makes three
  // groups occupy the same space as twelve, which spreads the dots so far
  // apart that the matrix stops reading as a matrix.
  const matrixHeight = Math.min(0.44, Math.max(0.2, 0.08 * ordered.length + 0.08));
  const matrixTop = matrixHeight;
  draw(surface, traces, baseLayout({
    margin: { l: 12, r: 24, t: 26, b: 48 },
    showlegend: false,
    bargap: 0.3,
    shapes: [...bands, ...connectors],
    // Top: intersection sizes.
    xaxis: { domain: [matrixLeft, 1], anchor: 'y', showticklabels: false,
             showgrid: false, zeroline: false, range: [-0.6, rows.length - 0.4] },
    yaxis: { domain: [matrixTop + 0.06, 1], anchor: 'x',
             title: axisTitle('Intersection size'),
             gridcolor: '#cfcfcf', zeroline: true, zerolinecolor: '#999' },
    // Bottom right: membership matrix. Category labels live here, between the
    // set-size bars and the dots, as in the reference layout.
    xaxis2: { domain: [matrixLeft, 1], anchor: 'y2', showticklabels: false,
              showgrid: false, zeroline: false, range: [-0.6, rows.length - 0.4] },
    yaxis2: {
      domain: [0, matrixTop], anchor: 'x2', showgrid: false, zeroline: false,
      tickmode: 'array', tickvals: ordered.map((_, i) => i), ticktext: ordered,
      range: [ordered.length - 0.5, -0.5], tickfont: { size: 11 }, side: 'left',
      ticklabelposition: 'outside', automargin: false,
    },
    // Bottom left: set sizes, growing leftwards away from the matrix.
    xaxis3: { domain: [0.02, matrixLeft - 0.14], anchor: 'y3', autorange: 'reversed',
              title: { text: 'Set size', font: { size: 10 } },
              gridcolor: '#cfcfcf', zeroline: false, tickfont: { size: 9 } },
    yaxis3: { domain: [0, matrixTop], anchor: 'x3', showticklabels: false,
              showgrid: false, zeroline: false, range: [ordered.length - 0.5, -0.5] },
  }), imageFormat('upset'));
}

export function abundance(surface, payload, options = {}) {
  if (payload.found === false) {
    message(surface, payload.reason || 'No abundance data for this feature.');
    return;
  }
  if (!payload.groups.length) {
    message(surface, 'No group statistics for this feature.');
    return;
  }
  // The details rail draws this at ~190px, where a mode bar would cover the
  // chart it is meant to control.
  const compact = Boolean(options.compact);

  // Left: group means with propagated (combASD) error bars. Right: every
  // injection, jittered per sample -- the desktop's bar + strip pairing.
  const groupNames = payload.groups.map((group) => group.name);
  const palette = groupNames.map((_, i) =>
    `hsl(${(i * 360) / Math.max(1, groupNames.length)}, 45%, 52%)`);

  const bars = {
    type: 'bar',
    x: groupNames,
    y: payload.groups.map((group) => group.average),
    error_y: {
      type: 'data',
      array: payload.groups.map((group) => group.error || 0),
      color: '#333', thickness: 1, width: 4,
    },
    marker: { color: palette },
    hovertemplate: '<b>%{x}</b><br>mean %{y:.3s}<extra></extra>',
    showlegend: false,
    xaxis: 'x', yaxis: 'y',
  };

  const samples = [...new Set(payload.points.map((point) => point.sample))];
  const byGroup = new Map();
  for (const point of payload.points) {
    if (!byGroup.has(point.biolgroup)) byGroup.set(point.biolgroup, []);
    byGroup.get(point.biolgroup).push(point);
  }
  const strips = [...byGroup.entries()].map(([group, points]) => ({
    type: 'scatter',
    mode: 'markers',
    name: group,
    x: points.map((point) => point.sample),
    y: points.map((point) => point.abundance),
    marker: {
      size: 7, opacity: 0.6,
      color: palette[groupNames.indexOf(group)] || '#666',
    },
    text: points.map((point) => `<b>${point.injection}</b><br>${point.biolgroup}`),
    hovertemplate: '%{text}<br>%{y:.3s}<extra></extra>',
    showlegend: false,
    xaxis: 'x2', yaxis: 'y2',
  }));

  // Both panels, at both sizes: the desktop's abundance window is a bar chart
  // of group means beside a per-sample strip of every injection, and dropping
  // the strip in the rail threw away the half that shows the spread within a
  // group. The rail just gets smaller type and tighter margins.
  draw(surface, [bars, ...strips], baseLayout({
    margin: compact ? { l: 40, r: 6, t: 8, b: 74 } : { l: 62, r: 16, t: 16, b: 110 },
    showlegend: false,
    xaxis: { domain: [0, 0.42], anchor: 'y', tickangle: compact ? -90 : -45,
             tickfont: { size: compact ? 8 : 10 } },
    yaxis: { domain: [0, 1], anchor: 'x', rangemode: 'tozero',
             title: compact ? undefined : axisTitle('Abundance'),
             tickfont: { size: compact ? 8 : 12 },
             // The desktop shares one y limit across both panels
             // (`ylims = (0, ax[1].get_ylim()[1] * 1.05)`).
             gridcolor: '#dedede' },
    xaxis2: { domain: [0.5, 1], anchor: 'y2', tickangle: -90,
              tickfont: { size: compact ? 7 : 9 },
              categoryorder: 'array', categoryarray: samples },
    yaxis2: { domain: [0, 1], anchor: 'x2', matches: 'y', showticklabels: false,
              rangemode: 'tozero' },
  }), compact ? { displayModeBar: false } : {});
}

/** m/z labels for the most intense peaks that fit: a label is kept only if
 *  its box overlaps no kept label and no taller peak beneath it. Works in
 *  pixels, from the plot area's size and the visible axis ranges. `side` is
 *  1 for sticks drawn upwards and -1 for the mirrored reference below. */
function peakLabels(surface, mzs, values, side, compact) {
  const layout = surface._fullLayout;
  if (!layout || !layout.xaxis || !layout.yaxis) return [];
  const width = layout.xaxis._length;
  const height = layout.yaxis._length;
  const [x0, x1] = layout.xaxis.range;
  const [y0, y1] = layout.yaxis.range;
  const px = (mz) => ((mz - x0) / (x1 - x0)) * width;
  const py = (value) => ((value - y0) / (y1 - y0)) * height;
  const base = py(0);
  const digits = compact ? 3 : 4;
  const charWidth = compact ? 5 : 6.2;
  const boxHeight = compact ? 10 : 13;
  const limit = compact ? 6 : 12;

  const peaks = mzs.map((mz, i) => ({ mz, y: values[i] }))
    .filter((peak) => peak.mz >= x0 && peak.mz <= x1);
  const tallest = Math.max(...peaks.map((peak) => Math.abs(peak.y)), 0);
  // Distance of a stick's tip from the baseline, on its own side.
  const reach = (value) => side * (py(value) - base);
  const placed = [];
  for (const peak of [...peaks].sort((a, b) => Math.abs(b.y) - Math.abs(a.y))) {
    if (placed.length >= limit || Math.abs(peak.y) < tallest * 0.03) break;
    const text = peak.mz.toFixed(digits);
    const half = (text.length * charWidth) / 2 + 2;
    // Slide a label at the frame's edge inward rather than drop it.
    const centre = Math.min(Math.max(px(peak.mz), half), width - half);
    const near = reach(peak.y) + 2;
    const box = { left: centre - half, right: centre + half, shift: centre - px(peak.mz),
                  near, far: near + boxHeight };
    const room = side > 0 ? height - base : base;
    if (box.far > room) continue;
    const clashes = placed.some((other) => box.left < other.right && box.right > other.left
                                          && box.near < other.far && box.far > other.near)
      || peaks.some((other) => other !== peak && px(other.mz) > box.left
                    && px(other.mz) < box.right && reach(other.y) > box.near);
    if (clashes) continue;
    placed.push({ ...box, peak, text });
  }
  return placed.map(({ peak, text, shift }) => ({
    x: peak.mz, y: peak.y, text, showarrow: false, xshift: shift,
    yanchor: side > 0 ? 'bottom' : 'top', yshift: side,
    font: { size: compact ? 8 : 10, color: '#333' },
  }));
}

const MATCH_TOLERANCE = 0.02;
const QUERY_COLOUR = '#111';
const REFERENCE_COLOUR = '#8a8a8a';
const MATCHED_QUERY = '#1d7d74';
const MATCHED_REFERENCE = '#c0392b';

/** Which peaks on each side have a partner on the other, directly or, for an
 *  analogue, after shifting by the precursor difference. */
function matchedPeaks(queryMz, referenceMz, shift) {
  const near = (a, b) => Math.abs(a - b) <= MATCH_TOLERANCE;
  const shifted = shift && Math.abs(shift) > MATCH_TOLERANCE ? shift : null;
  const query = queryMz.map((q) => referenceMz.some((r) => near(q, r)
                                                    || (shifted !== null && near(q, r + shifted))));
  const reference = referenceMz.map((r) => queryMz.some((q) => near(q, r)
                                                       || (shifted !== null && near(q, r + shifted))));
  return { query, reference };
}

function sticks(mzs, values, colours) {
  // One trace per colour: Plotly line traces take a single colour.
  const byColour = new Map();
  mzs.forEach((mz, i) => {
    const colour = colours[i];
    if (!byColour.has(colour)) byColour.set(colour, { x: [], y: [] });
    const trace = byColour.get(colour);
    trace.x.push(mz, mz, null);
    trace.y.push(0, values[i], null);
  });
  return [...byColour].map(([colour, { x, y }]) => ({
    type: 'scatter', mode: 'lines', x, y,
    line: { color: colour, width: 1 }, hoverinfo: 'skip', showlegend: false,
  }));
}

/** MS2 spectrum; with `options.reference` ({mz, intensity, shift, name}) a
 *  mirror plot, the reference drawn downwards and both sides scaled to 100. */
export function spectrum(surface, payload, options = {}) {
  if (!payload.found || !payload.mz.length) {
    message(surface, 'No MS2 spectrum for this feature in the loaded fragment database.');
    return;
  }
  // The rail passes {compact: true}; the parameter was missing, so every one
  // of the `compact ? ... : ...` reads below threw ReferenceError and the
  // panel showed "compact is not defined" instead of a spectrum.
  const compact = Boolean(options.compact);
  const reference = options.reference && options.reference.mz && options.reference.mz.length
    ? options.reference : null;

  let top = payload.intensity;
  let bottom = [];
  let colours = payload.mz.map(() => QUERY_COLOUR);
  let referenceColours = [];
  if (reference) {
    const peak = Math.max(...payload.intensity);
    top = payload.intensity.map((value) => (100 * value) / peak);
    bottom = reference.intensity.map((value) => -value);
    const matched = matchedPeaks(payload.mz, reference.mz, reference.shift);
    colours = matched.query.map((hit) => (hit ? MATCHED_QUERY : QUERY_COLOUR));
    referenceColours = matched.reference.map((hit) => (hit ? MATCHED_REFERENCE : REFERENCE_COLOUR));
  }

  const allMz = reference ? [...payload.mz, ...reference.mz] : payload.mz;
  const xRange = [0, Math.max(...allMz) * 1.05];
  // Headroom beyond the base peak(s) for their labels.
  const yRange = reference ? [-128, 128] : [0, Math.max(...top) * 1.15];

  const hover = (mzs, values, label) => ({
    type: 'scatter', mode: 'markers', x: mzs, y: values,
    marker: { size: 5, color: 'rgba(0,0,0,0)' },
    text: mzs.map((mz, i) => `${label}m/z ${num(mz, 4)}<br>`
                             + (reference ? `${num(Math.abs(values[i]), 1)}%`
                                          : `abundance ${num(values[i], 0)}`)),
    hovertemplate: '%{text}<extra></extra>', showlegend: false,
  });

  const traces = [...sticks(payload.mz, top, colours), hover(payload.mz, top, '')];
  if (reference) {
    traces.push(...sticks(reference.mz, bottom, referenceColours),
                hover(reference.mz, bottom, reference.name ? `${reference.name}<br>` : ''));
  }

  const yaxis = { ...baseLayout().yaxis, range: yRange,
                  title: compact ? undefined : axisTitle(reference ? 'Relative abundance' : 'Abundance'),
                  tickfont: { size: compact ? 8 : 12 } };
  if (reference) {
    Object.assign(yaxis, { tickvals: [-100, -50, 0, 50, 100], ticktext: ['100', '50', '0', '50', '100'],
                           zeroline: true, zerolinecolor: '#3c3c3c', zerolinewidth: 1 });
  }

  const drawn = draw(surface, traces, baseLayout({
    margin: compact ? { l: 44, r: 8, t: 8, b: 34 } : { l: 66, r: 16, t: 16, b: 50 },
    showlegend: false,
    xaxis: { ...baseLayout().xaxis, range: xRange,
             title: compact ? undefined : axisTitle('m/z'),
             tickfont: { size: compact ? 8 : 12 } },
    yaxis,
  }), compact ? { displayModeBar: false } : {});

  const relabel = () => {
    if (!surface._fullLayout) return;
    const annotations = peakLabels(surface, payload.mz, top, 1, compact);
    if (reference) annotations.push(...peakLabels(surface, reference.mz, bottom, -1, compact));
    surface._labelling = true;
    Promise.resolve(Plotly.relayout(surface, { annotations }))
      .finally(() => { surface._labelling = false; });
  };
  Promise.resolve(drawn).then(relabel);
  if (!surface._labelListener) {
    surface._labelListener = true;
    surface.on('plotly_relayout', (event) => {
      if (surface._labelling || !event) return;
      const keys = Object.keys(event);
      if (keys.some((key) => key.startsWith('xaxis') || key.startsWith('yaxis')
                             || key === 'autosize' || key === 'width' || key === 'height')) {
        surface._relabel();
      }
    });
  }
  surface._relabel = relabel;
}
