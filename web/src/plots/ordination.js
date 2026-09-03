/* Multivariate ordination: scores (with 2-sigma confidence ellipses) and
 * loadings (origin-anchored arrows for the top features).
 *
 * The ellipse geometry is computed server-side from the same covariance
 * decomposition the desktop's `plot_point_cov` uses; here it is only drawn,
 * as an SVG path so Plotly can rotate it. */

import { num } from '../util.js';
import { AXIS, axisTitle, baseLayout, draw, highlightTrace, imageFormat,
         message, onSelect } from './base.js';

/** Rotated ellipse as an SVG path -- Plotly's shape ellipse has no rotation,
 *  so the outline is sampled and transformed here. */
function ellipsePath(ellipse, steps = 64) {
  const radians = (ellipse.angle * Math.PI) / 180;
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  const rx = ellipse.width / 2;
  const ry = ellipse.height / 2;
  const points = [];
  for (let i = 0; i <= steps; i += 1) {
    const theta = (2 * Math.PI * i) / steps;
    const x = rx * Math.cos(theta);
    const y = ry * Math.sin(theta);
    points.push([ellipse.cx + x * cos - y * sin, ellipse.cy + x * sin + y * cos]);
  }
  return `M ${points.map(([x, y]) => `${x},${y}`).join(' L ')} Z`;
}

function scores(surface, payload, context) {
  if (!payload.series || !payload.series.length) {
    message(surface, 'No samples to plot -- check that the metadata assigns biological groups.');
    return;
  }

  const traces = payload.series.map((series) => ({
    type: 'scattergl',
    mode: 'markers',
    name: series.name,
    x: series.x, y: series.y,
    customdata: series.ids,
    marker: { size: 9, color: series.colour, line: { color: '#00000055', width: 1 } },
    text: series.labels.map((label, i) => `<b>${label}</b><br>${series.name}`
      + `<br>${payload.x_label.split(' ')[0]}: ${num(series.x[i], 3)}`
      + `<br>${payload.y_label.split(' ')[0]}: ${num(series.y[i], 3)}`),
    hovertemplate: '%{text}<extra></extra>',
  }));

  const shapes = payload.series
    .filter((series) => series.ellipse)
    .map((series) => ({
      type: 'path',
      path: ellipsePath(series.ellipse),
      fillcolor: series.colour,
      opacity: 0.16,
      line: { color: series.colour, width: 1 },
      layer: 'below',
    }));

  draw(surface, traces, baseLayout({
    title: payload.title ? { text: payload.title, font: { size: 13 } } : undefined,
    xaxis: { ...AXIS, title: axisTitle(payload.x_label) },
    yaxis: { ...AXIS, title: axisTitle(payload.y_label) },
    shapes,
  }), imageFormat('ordination'));

  // Selecting a point here selects an injection/sample, not a feature -- the
  // desktop shows it in the "Injection/Sample" label rather than driving the
  // feature highlight.
  onSelect(surface, (pt) => pt.customdata, context.onSelectSample || (() => {}));
}

function loadings(surface, payload, context) {
  if (!payload.loadings || !payload.loadings.length) {
    message(surface, 'No loadings to show.');
    return;
  }

  const selected = context.selectedFeature;
  const traces = [{
    type: 'scattergl',
    mode: 'markers+text',
    x: payload.loadings.map((item) => item.x),
    y: payload.loadings.map((item) => item.y),
    customdata: payload.loadings.map((item) => item.id),
    text: payload.loadings.map((item) => item.id),
    textposition: 'top center',
    textfont: { size: 9, color: '#333' },
    marker: { size: 6, color: 'steelblue' },
    hovertemplate: '<b>%{customdata}</b><br>%{x:.4f}, %{y:.4f}<extra></extra>',
    showlegend: false,
  }];

  const hit = payload.loadings.find((item) => item.id === selected);
  traces.push(highlightTrace(hit && hit.x, hit && hit.y));

  // Origin-anchored arrows, matching the desktop's biplot-style annotate().
  const annotations = payload.loadings.map((item) => ({
    x: item.x, y: item.y, ax: 0, ay: 0,
    xref: 'x', yref: 'y', axref: 'x', ayref: 'y',
    showarrow: true, arrowhead: 2, arrowsize: 0.8, arrowwidth: 1, arrowcolor: 'steelblue',
    text: '',
  }));

  draw(surface, traces, baseLayout({
    title: payload.title ? { text: payload.title, font: { size: 13 } } : undefined,
    annotations,
    xaxis: { ...AXIS, title: axisTitle(payload.x_label), range: payload.range,
             zeroline: true, zerolinecolor: '#999' },
    yaxis: { ...AXIS, title: axisTitle(payload.y_label), range: payload.range,
             zeroline: true, zerolinecolor: '#999' },
  }), imageFormat('ordination-loadings'));

  onSelect(surface, (pt) => pt.customdata, context.onSelectFeature);
}

export function ordination(surface, payload, context) {
  if (payload.view === 'Loadings') loadings(surface, payload, context);
  else scores(surface, payload, context);
}
