/* Feature scatter plots: m/z vs RT, mass defect, the Data Review quality
 * view, and the 3D fold-change plot.
 *
 * Points carry a per-point alpha computed server-side from each feature's
 * log max abundance -- the same abundance-tied opacity the desktop bakes
 * into its RGBA arrays -- so a faint point really is a low-abundance
 * feature in both apps. */

import { rgba, num } from '../util.js';
import { axisTitle, baseLayout, draw, highlightTrace, message, onSelect, paddedRange } from './base.js';

function hoverText(series, index, fields) {
  const parts = [`<b>${series.ids[index]}</b>`];
  for (const [label, values, digits] of fields) {
    const value = values && values[index];
    if (value !== null && value !== undefined) parts.push(`${label}: ${num(value, digits)}`);
  }
  return parts.join('<br>');
}

function scatterTraces(payload, fields) {
  return payload.series
    .filter((series) => series.count > 0)
    .map((series) => ({
      type: 'scattergl',
      mode: 'markers',
      name: `${series.name} (${series.count})`,
      x: series.x,
      y: series.y,
      customdata: series.ids,
      marker: {
        size: 6,
        // Per-point colour so each keeps its own abundance-derived alpha;
        // when the payload has no alpha array (the quality view), fall back
        // to the desktop's flat 0.5.
        color: series.ids.map((_, i) =>
          rgba(series.colour, series.alpha ? series.alpha[i] : 0.5)),
        line: { width: 0 },
      },
      text: series.ids.map((_, i) => hoverText(series, i, fields(series))),
      hovertemplate: '%{text}<extra></extra>',
    }));
}

function render2d(surface, payload, context, options) {
  if (!payload.series.some((series) => series.count > 0)) {
    message(surface, 'No features matched the current feature sets.');
    return;
  }
  const traces = scatterTraces(payload, options.fields);
  const selected = context.selectedFeature;
  const point = selected ? findPoint(payload, selected) : null;
  traces.push(highlightTrace(point && point.x, point && point.y));

  const allX = payload.series.flatMap((series) => series.x || []);
  const allY = payload.series.flatMap((series) => series.y || []);

  draw(surface, traces, baseLayout({
    xaxis: { ...baseLayout().xaxis, title: axisTitle(payload.x_label),
             range: options.xRange || paddedRange(allX) },
    yaxis: { ...baseLayout().yaxis, title: axisTitle(payload.y_label),
             range: options.yRange || payload.y_range || paddedRange(allY) },
    shapes: options.shapes || [],
  }));

  onSelect(surface, (pt) => pt.customdata, context.onSelectFeature);
}

/** Locate a compound's coordinates in a payload, for the highlight marker. */
function findPoint(payload, compound) {
  for (const series of payload.series) {
    const index = series.ids.indexOf(compound);
    if (index !== -1) return { x: series.x[index], y: series.y[index] };
  }
  return null;
}

export function mzrt(surface, payload, context) {
  render2d(surface, payload, context, {
    fields: (series) => [
      ['m/z', series.y, 4],
      ['RT', series.x, 3],
    ],
  });
}

export function massdefect(surface, payload, context) {
  // The three grey reference lines from the desktop's "Mass Defect
  // Guidelines" toggle, drawn as layout shapes so they sit under the points.
  const shapes = (payload.guidelines || []).map((line) => ({
    type: 'line', x0: line.x[0], y0: line.y[0], x1: line.x[1], y1: line.y[1],
    line: { color: 'dimgrey', width: 1 }, layer: 'below',
  }));
  render2d(surface, payload, context, {
    yRange: [0, 1],
    shapes,
    fields: (series) => [
      ['m/z', series.x, 4],
      ['Mass defect', series.y, 4],
    ],
  });
}

export function quality(surface, payload, context) {
  render2d(surface, payload, context, {
    fields: (series) => [
      ['m/z', series.y, 4],
      ['RT', series.x, 3],
    ],
  });
}

export function fc3d(surface, payload, context) {
  const traces = payload.series
    .filter((series) => series.count > 0)
    .map((series) => ({
      type: 'scatter3d',
      mode: 'markers',
      name: `${series.name} (${series.count})`,
      x: series.x, y: series.y, z: series.z,
      customdata: series.ids,
      marker: { size: 3, color: series.colour, opacity: 0.85 },
      text: series.ids.map((id, i) =>
        `<b>${id}</b><br>log2 FC: ${num(series.x[i], 3)}`
        + `<br>RT: ${num(series.y[i], 3)}<br>m/z: ${num(series.z[i], 4)}`),
      hovertemplate: '%{text}<extra></extra>',
    }));

  if (!traces.length) {
    message(surface, 'No features with a fold change to plot.');
    return;
  }

  draw(surface, traces, baseLayout({
    margin: { l: 0, r: 0, t: 24, b: 0 },
    scene: {
      xaxis: { title: payload.x_label, gridcolor: '#cfcfcf', backgroundcolor: '#ededed',
               showbackground: true },
      yaxis: { title: payload.y_label, gridcolor: '#cfcfcf', backgroundcolor: '#ededed',
               showbackground: true },
      zaxis: { title: payload.z_label, gridcolor: '#cfcfcf', backgroundcolor: '#ededed',
               showbackground: true },
    },
  }));

  onSelect(surface, (pt) => pt.customdata, context.onSelectFeature);
}
