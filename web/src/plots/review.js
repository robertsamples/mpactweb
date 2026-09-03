/* Data Review and Group Analysis plots: the CV rarefaction curve, the filter
 * treemap, the UpSet set plot, and the per-feature abundance and spectrum
 * views from Feature Info. */

import { num } from '../util.js';
import { axisTitle, baseLayout, draw, message } from './base.js';

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
    xaxis: { ...baseLayout().xaxis, title: axisTitle(payload.x_label), range: payload.x_range },
    yaxis: { ...baseLayout().yaxis, title: axisTitle(payload.y_label), range: payload.y_range },
    shapes,
  }));
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
  }));
}

export function upset(surface, payload, context, options = {}) {
  const limit = options.limit || 30;
  const rows = payload.intersections.slice(0, limit);
  const categories = payload.categories;
  if (!rows.length || !categories.length) {
    message(surface, 'No group combinations to show.');
    return;
  }

  const dotX = [];
  const dotY = [];
  const dotOn = [];
  const lines = [];
  rows.forEach((row, column) => {
    const members = new Set(row.groups);
    const present = [];
    categories.forEach((category, level) => {
      dotX.push(column);
      dotY.push(level);
      dotOn.push(members.has(category));
      if (members.has(category)) present.push(level);
    });
    if (present.length > 1) {
      lines.push({
        type: 'line', xref: 'x2', yref: 'y2',
        x0: column, x1: column, y0: Math.min(...present), y1: Math.max(...present),
        line: { color: '#333', width: 2 },
      });
    }
  });

  const traces = [
    // Top: intersection sizes.
    {
      type: 'bar',
      x: rows.map((_, i) => i),
      y: rows.map((row) => row.size),
      marker: { color: '#26a69a' },
      text: rows.map((row) => `${row.size} (${row.percent}%)`),
      textposition: 'outside',
      textfont: { size: 9 },
      hovertemplate: '%{text}<extra></extra>',
      showlegend: false,
      xaxis: 'x', yaxis: 'y',
    },
    // Bottom: the membership matrix.
    {
      type: 'scatter', mode: 'markers',
      x: dotX, y: dotY,
      marker: {
        size: 9,
        color: dotOn.map((on) => (on ? '#333' : '#cbcbcb')),
      },
      text: dotX.map((column, i) => {
        const category = categories[dotY[i]];
        return `${category}<br>${dotOn[i] ? 'in' : 'not in'} this set`;
      }),
      hovertemplate: '%{text}<extra></extra>',
      showlegend: false,
      xaxis: 'x2', yaxis: 'y2',
    },
  ];

  draw(surface, traces, baseLayout({
    margin: { l: 150, r: 20, t: 24, b: 40 },
    showlegend: false,
    shapes: lines,
    bargap: 0.35,
    xaxis: { domain: [0, 1], anchor: 'y', showticklabels: false, showgrid: false,
             range: [-0.6, rows.length - 0.4] },
    yaxis: { domain: [0.42, 1], anchor: 'x', title: axisTitle('Features'),
             gridcolor: '#cfcfcf' },
    xaxis2: { domain: [0, 1], anchor: 'y2', showticklabels: false, showgrid: false,
              range: [-0.6, rows.length - 0.4] },
    yaxis2: {
      domain: [0, 0.38], anchor: 'x2', showgrid: false, zeroline: false,
      tickmode: 'array', tickvals: categories.map((_, i) => i), ticktext: categories,
      range: [categories.length - 0.5, -0.5], tickfont: { size: 11 },
    },
  }));
}

export function abundance(surface, payload, options = {}) {
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

  draw(surface, [bars, ...strips], baseLayout({
    margin: compact ? { l: 40, r: 8, t: 8, b: 62 } : { l: 62, r: 16, t: 16, b: 110 },
    showlegend: false,
    xaxis: { domain: [0, 0.42], anchor: 'y', tickangle: -45,
             tickfont: { size: compact ? 8 : 10 } },
    yaxis: { domain: [0, 1], anchor: 'x', rangemode: 'tozero',
             title: compact ? undefined : axisTitle('Abundance'),
             tickfont: { size: compact ? 8 : 12 } },
    xaxis2: { domain: [0.5, 1], anchor: 'y2', tickangle: -90,
              tickfont: { size: compact ? 7 : 9 },
              categoryorder: 'array', categoryarray: samples },
    yaxis2: { domain: [0, 1], anchor: 'x2', matches: 'y', showticklabels: false,
              rangemode: 'tozero' },
  }), compact ? { displayModeBar: false, staticPlot: false } : {});
}

export function spectrum(surface, payload) {
  if (!payload.found || !payload.mz.length) {
    message(surface, 'No MS2 spectrum for this feature in the loaded fragment database.');
    return;
  }
  // Stick spectrum: one zero-anchored vertical per fragment, the direct
  // equivalent of the desktop's ax.vlines call.
  const x = [];
  const y = [];
  payload.mz.forEach((mz, i) => {
    x.push(mz, mz, null);
    y.push(0, payload.intensity[i], null);
  });

  draw(surface, [
    {
      type: 'scatter', mode: 'lines', x, y,
      line: { color: '#111', width: 1 }, hoverinfo: 'skip', showlegend: false,
    },
    {
      type: 'scatter', mode: 'markers',
      x: payload.mz, y: payload.intensity,
      marker: { size: 5, color: 'rgba(0,0,0,0)' },
      text: payload.mz.map((mz, i) =>
        `m/z ${num(mz, 4)}<br>abundance ${num(payload.intensity[i], 0)}`),
      hovertemplate: '%{text}<extra></extra>', showlegend: false,
    },
  ], baseLayout({
    margin: { l: 66, r: 16, t: 16, b: 50 },
    showlegend: false,
    xaxis: { ...baseLayout().xaxis, title: axisTitle('m/z'), rangemode: 'tozero' },
    yaxis: { ...baseLayout().yaxis, title: axisTitle('Abundance'), rangemode: 'tozero' },
  }));
}
