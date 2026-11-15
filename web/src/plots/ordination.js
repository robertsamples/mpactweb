/* Multivariate ordination: scores (with 2-sigma confidence ellipses) and
 * loadings (origin-anchored arrows for the top features).
 *
 * The ellipse geometry is computed server-side from the same covariance
 * decomposition the desktop's `plot_point_cov` uses; here it is only drawn,
 * as an SVG path so Plotly can rotate it. */

import { num } from '../util.js';
import { AXIS, axisTitle, baseLayout, drawSelectable, imageFormat,
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

  // The same click highlight the feature plots get: selecting a sample rings
  // its point, not just names it in the status bar.
  const selected = context.selectedSample;
  let point = null;
  if (selected) {
    for (const series of payload.series) {
      const index = (series.ids || []).indexOf(selected);
      if (index !== -1) { point = { x: series.x[index], y: series.y[index] }; break; }
    }
  }

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

  const fresh = !drawSelectable(surface, [payload], () => ({
    traces,
    layout: baseLayout({
      title: payload.title ? { text: payload.title, font: { size: 13 } } : undefined,
      xaxis: { ...AXIS, title: axisTitle(payload.x_label) },
      yaxis: { ...AXIS, title: axisTitle(payload.y_label) },
      shapes,
    }),
    config: imageFormat('ordination'),
    // Sample markers are larger than feature markers, so the disc is too.
  }), point, { size: 14 });

  // Selecting a point here selects an injection/sample, not a feature -- it
  // is named in the status bar rather than driving the feature rail, but it
  // gets the same ring highlight the feature selection gets elsewhere.
  if (fresh) {
    onSelect(surface, (pt) => pt.customdata, (id) => {
      // The series a point belongs to *is* its biological group, so the status
      // bar can name the group without another request.
      const series = payload.series.find((item) => (item.ids || []).includes(id));
      (context.onSelectSample || (() => {}))(id, series && series.name);
    });
  }
}

function loadings(surface, payload, context) {
  if (!payload.loadings || !payload.loadings.length) {
    message(surface, 'No loadings to show.');
    return;
  }

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

  const flagged = context.flags && context.flags.size
    ? payload.loadings.filter((item) => context.flags.has(item.id))
      .map((item) => ({ x: item.x, y: item.y, colour: context.flags.get(item.id) }))
    : [];
  const picked = context.selectedFeatures;
  const hits = picked && picked.size
    ? payload.loadings.filter((item) => picked.has(item.id)) : [];

  const fresh = !drawSelectable(surface, [payload], () => ({
    traces,
    layout: baseLayout({
      title: payload.title ? { text: payload.title, font: { size: 13 } } : undefined,
      // Origin-anchored arrows, matching the desktop's biplot-style annotate().
      annotations: payload.loadings.map((item) => ({
        x: item.x, y: item.y, ax: 0, ay: 0,
        xref: 'x', yref: 'y', axref: 'x', ayref: 'y',
        showarrow: true, arrowhead: 2, arrowsize: 0.8, arrowwidth: 1,
        arrowcolor: 'steelblue', text: '',
      })),
      xaxis: { ...AXIS, title: axisTitle(payload.x_label), range: payload.range,
               zeroline: true, zerolinecolor: '#999' },
      yaxis: { ...AXIS, title: axisTitle(payload.y_label), range: payload.range,
               zeroline: true, zerolinecolor: '#999' },
    }),
    config: imageFormat('ordination-loadings'),
  }), hits, { flags: flagged });

  if (fresh) onSelect(surface, (pt) => pt.customdata, context.onSelectFeature);
}

export function ordination(surface, payload, context) {
  if (payload.view === 'Loadings') loadings(surface, payload, context);
  else scores(surface, payload, context);
}
