/* Volcano plot.
 *
 * The server sends every point's log2 fold change and -log10 p/q once; the
 * red / blue / black classification is re-applied here as the thresholds
 * move, so dragging the p-q or fold-change box redraws instantly instead of
 * re-querying. The rule is the desktop's: significant means past the p/q
 * line AND past the fold-change line, and the sign of the fold change then
 * splits enriched (red) from depleted (blue). */

import { num } from '../util.js';
import { axisTitle, baseLayout, drawSelectable, gridded, imageFormat,
         message, onSelect, originRange, paddedRange } from './base.js';

const CLASS_LABEL = { up: 'Enriched', down: 'Depleted', ns: 'Not significant' };

function classify(payload, pqthresh, fcthresh) {
  const pqCut = pqthresh > 0 ? -Math.log10(pqthresh) : Infinity;
  const fcCut = fcthresh > 0 ? Math.log2(fcthresh) : Infinity;
  const buckets = { up: [], down: [], ns: [] };

  for (const series of payload.series) {
    for (let i = 0; i < series.x.length; i += 1) {
      const fc = series.x[i];
      const pq = series.y[i];
      if (fc === null || pq === null) continue;
      const significant = pq >= pqCut && Math.abs(fc) >= fcCut;
      const bucket = !significant ? 'ns' : (fc > 0 ? 'up' : 'down');
      buckets[bucket].push({
        x: fc, y: pq, id: series.ids[i],
        mz: series.mz ? series.mz[i] : null,
        rt: series.rt ? series.rt[i] : null,
        set: series.name,
      });
    }
  }
  return { buckets, pqCut, fcCut };
}

/** Classification is per-threshold, not per-selection: cache it on the payload
 *  so moving the highlight does not re-bucket a thousand points and rebuild
 *  their hover strings. */
function classified(payload, pqthresh, fcthresh) {
  const key = `${pqthresh}|${fcthresh}`;
  if (!payload._classified || payload._classified.key !== key) {
    payload._classified = { key, ...classify(payload, pqthresh, fcthresh) };
  }
  return payload._classified;
}

/** The enriched / depleted / not-significant counts at these thresholds. */
export function volcanoCounts(payload, options = {}) {
  const { buckets } = classified(payload, options.pqthresh ?? payload.thresholds.pq,
                                 options.fcthresh ?? payload.thresholds.fc);
  return { up: buckets.up.length, down: buckets.down.length, ns: buckets.ns.length };
}

export function volcano(surface, payload, context, options = {}) {
  const pqthresh = options.pqthresh ?? payload.thresholds.pq;
  const fcthresh = options.fcthresh ?? payload.thresholds.fc;
  const { buckets, pqCut, fcCut } = classified(payload, pqthresh, fcthresh);

  const total = buckets.up.length + buckets.down.length + buckets.ns.length;
  if (!total) {
    message(surface, 'No features with both a fold change and a p-value -- check the '
                     + 'experimental and control groups for this run.');
    return { up: 0, down: 0, ns: 0 };
  }

  const cache = payload._classified;
  if (!cache.traces) {
    cache.traces = ['ns', 'down', 'up'].map((key) => {
      const points = buckets[key];
      return {
        type: 'scattergl',
        mode: 'markers',
        name: `${CLASS_LABEL[key]} (${points.length})`,
        x: points.map((p) => p.x),
        y: points.map((p) => p.y),
        customdata: points.map((p) => p.id),
        marker: { size: 6, color: payload.class_colours[key], opacity: 0.55,
                  line: { width: 0 } },
        text: points.map((p) =>
          `<b>${p.id}</b><br>log2 FC: ${num(p.x, 3)}<br>${payload.pq_variable}: ${num(p.y, 3)}`
          + (p.mz === null ? '' : `<br>m/z: ${num(p.mz, 4)}`)
          + (p.rt === null ? '' : `<br>RT: ${num(p.rt, 3)}`)),
        hovertemplate: '%{text}<extra></extra>',
      };
    });
  }
  const traces = cache.traces;

  const selected = context.selectedFeature;
  let point = null;
  if (selected) {
    for (const key of ['up', 'down', 'ns']) {
      const hit = buckets[key].find((p) => p.id === selected);
      if (hit) { point = hit; break; }
    }
  }

  const allX = traces.flatMap((trace) => trace.x || []);
  const allY = traces.flatMap((trace) => trace.y || []);
  const xRange = paddedRange(allX) || [-7, 7];
  // The desktop's `set_ylim(0, maxpval*1.1)` exactly: significance starts at
  // zero, with headroom above the most significant point. Markers sitting at
  // y = 0 are half-clipped by the frame, which is also what matplotlib does
  // with its default clip_on, so this matches the desktop rather than
  // floating the data above a negative stub of axis.
  const yRange = originRange(allY, 0.1) || [0, 1];

  const line = (x0, y0, x1, y1) => ({
    type: 'line', x0, y0, x1, y1,
    line: { color: 'dimgrey', width: 1, dash: 'dot' }, layer: 'below',
  });
  const shapes = [];
  if (Number.isFinite(fcCut)) {
    shapes.push(line(-fcCut, yRange[0], -fcCut, yRange[1]));
    shapes.push(line(fcCut, yRange[0], fcCut, yRange[1]));
  }
  if (Number.isFinite(pqCut)) shapes.push(line(xRange[0], pqCut, xRange[1], pqCut));

  // The thresholds are part of the signature: moving one re-classifies every
  // point, which is a real redraw, while moving the selection is not.
  const fresh = !drawSelectable(surface, [payload, pqthresh, fcthresh], () => ({
    traces,
    layout: baseLayout({
      xaxis: { ...gridded(), title: axisTitle(payload.x_label), range: xRange },
      yaxis: { ...gridded(), title: axisTitle(payload.y_label), range: yRange },
      shapes,
    }),
    config: imageFormat('volcano'),
  }), point);

  if (fresh) onSelect(surface, (pt) => pt.customdata, context.onSelectFeature);
  return { up: buckets.up.length, down: buckets.down.length, ns: buckets.ns.length };
}
