/* Analogues: features one or two small formula steps from another, and the
 * vectors that join them on the mass defect plot.
 *
 * A search (the Analogues section of the details rail) selects what it
 * finds and adds a vector for each to the `analogues` store key. Vectors
 * build up across searches, so a family can be followed outwards one
 * feature at a time, and each can be taken away again. They belong to a run
 * and are not kept after the page is closed. */

import { api } from './api.js';
import { selection, store } from './state.js';

// The steps the server knows (plotdata/analogues.py), in its order, each
// with the colour its vectors are drawn in.
export const UNITS = [
  { key: 'CH2', html: 'CH<sub>2</sub>', text: 'CH₂', mass: 14.0156500642, colour: '#1a1a1a' },
  { key: 'H2', html: 'H<sub>2</sub>', text: 'H₂', mass: 2.0156500642, colour: '#6f6f6f' },
  { key: 'O', html: 'O', text: 'O', mass: 15.99491461956, colour: '#c00000' },
  { key: 'NH', html: 'NH', text: 'NH', mass: 15.0108990373, colour: '#0070c0' },
];
export const UNIT = new Map(UNITS.map((unit) => [unit.key, unit]));

const runId = () => (store.get('run') || {}).run_id;

/** The current run's vectors: `{key, from, to, steps, error}` each. */
export function analogueVectors() {
  const state = store.get('analogues');
  return state && state.run === runId() ? state.vectors : EMPTY;
}
const EMPTY = [];

const keyOf = (from, to) => `${from}\u0000${to}`;

const signOf = (step) => (step.sign > 0 ? '+' : '−');
/** "+O −H₂" for a vector's steps. */
export const stepsText = (steps) => steps.map((step) =>
  `${signOf(step)}${UNIT.get(step.unit).text}`).join(' ');
export const stepHtml = (step) => `${signOf(step)}${UNIT.get(step.unit).html}`;

/** Find `compound`'s analogues, select them with it, and add their vectors.
 *  Resolves to the number of features found. */
export async function findAnalogues(compound, options = {}) {
  const id = runId();
  const result = await api.analogues(id, { compound, ...options });
  if (id !== runId()) return 0;

  const vectors = [...analogueVectors()];
  const known = new Set(vectors.map((vector) => vector.key));
  for (const entry of result.analogues) {
    // Through the detected halfway feature, a two-step analogue is one more
    // step on from it; otherwise both steps run from the seed.
    const from = entry.via || compound;
    const steps = entry.via ? entry.steps.slice(1) : entry.steps;
    const key = keyOf(from, entry.id);
    // Searching outwards from an analogue finds its seed again, backwards.
    if (known.has(key) || known.has(keyOf(entry.id, from))) continue;
    known.add(key);
    vectors.push({ key, from, to: entry.id, steps, error: entry.error });
  }
  store.set({ analogues: { run: id, vectors } });
  if (result.analogues.length) {
    selection.add([compound, ...result.analogues.map((entry) => entry.id)], compound);
  }
  return result.analogues.length;
}

/** Take one vector away. The feature at its end leaves the selection too,
 *  unless another vector still reaches or leaves it. */
export function removeVector(key) {
  const vectors = analogueVectors();
  const gone = vectors.find((vector) => vector.key === key);
  if (!gone) return;
  const rest = vectors.filter((vector) => vector.key !== key);
  store.set({ analogues: { run: runId(), vectors: rest } });
  const held = rest.some((vector) => vector.to === gone.to || vector.from === gone.to);
  if (!held && gone.to !== store.get('selectedFeature')) selection.remove(gone.to);
}

export function clearVectors() {
  store.set({ analogues: { run: runId(), vectors: [] } });
}

/* --- drawing ------------------------------------------------------------ */

const massDefect = (mz) => mz - Math.floor(mz);

/** Plotly annotations for the vectors whose two ends are both in
 *  `coordinates` (compound -> [m/z, mass defect]). A two-step vector is two
 *  arrows end to end, meeting where the compound in between would be. */
export function vectorAnnotations(vectors, coordinates) {
  const annotations = [];
  const arrow = (from, to, colour, opening, closing) => annotations.push({
    x: to[0], y: to[1], ax: from[0], ay: from[1],
    xref: 'x', yref: 'y', axref: 'x', ayref: 'y',
    text: '', showarrow: true, arrowhead: 2, arrowsize: 1, arrowwidth: 1.8,
    arrowcolor: colour, startstandoff: opening, standoff: closing, captureevents: false,
  });
  // Isomers share an m/z, so several vectors can lie on top of each other;
  // their labels would too, and read as one smudged label.
  const labelled = new Set();
  const label = (from, to, html, colour) => {
    const x = (from[0] + to[0]) / 2;
    const y = (from[1] + to[1]) / 2;
    const place = `${html}|${x.toFixed(1)}|${y.toFixed(3)}`;
    if (labelled.has(place)) return;
    labelled.add(place);
    annotations.push({
      x, y, xref: 'x', yref: 'y', text: html, showarrow: false, yshift: 9,
      captureevents: false,
      font: { size: 11, color: colour, family: 'Bahnschrift, Segoe UI, sans-serif' },
    });
  };

  for (const vector of vectors) {
    const from = coordinates.get(vector.from);
    const to = coordinates.get(vector.to);
    if (!from || !to) continue;
    const [first, second] = vector.steps;
    if (second) {
      const mz = from[0] + first.sign * UNIT.get(first.unit).mass;
      const between = [mz, massDefect(mz)];
      // The mass defect wraps at 1: a halfway point on the far side of the
      // plot would send both arrows across it, so the pair is drawn as one.
      const wraps = Math.abs(between[1] - from[1]) > 0.5 || Math.abs(to[1] - between[1]) > 0.5;
      if (!wraps) {
        arrow(from, between, UNIT.get(first.unit).colour, 5, 0);
        label(from, between, stepHtml(first), UNIT.get(first.unit).colour);
        arrow(between, to, UNIT.get(second.unit).colour, 0, 6);
        label(between, to, stepHtml(second), UNIT.get(second.unit).colour);
        continue;
      }
    }
    const colour = UNIT.get(first.unit).colour;
    arrow(from, to, colour, 5, 6);
    label(from, to, vector.steps.map(stepHtml).join(' '), colour);
  }
  return annotations;
}
