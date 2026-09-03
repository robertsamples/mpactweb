/* The navigation toolbar under every plot.
 *
 * The desktop puts matplotlib's NavigationToolbar2QT directly under each
 * canvas -- home, back, forward, pan, zoom, then save -- so this is the same
 * bar, in the same order, with the same latching behaviour, sitting under the
 * plot instead of floating over its top-right corner the way Plotly's own
 * mode bar does. Plotly's bar is switched off in CONFIG; every button here
 * drives the Plotly API that button would have called.
 *
 * Back and forward walk a view history the way matplotlib's do: every zoom or
 * pan pushes the axis ranges, and the two buttons step through them.
 */

import { el } from '../util.js';

/* 16x16 line icons, drawn in the same visual language as the matplotlib
 * toolbar's: one stroke weight, round caps, no fills. */
const ICONS = {
  home: '<path d="M2.5 7.6 8 2.6l5.5 5"/><path d="M3.9 7.1v6.4h8.2V7.1"/>'
        + '<path d="M6.7 13.5v-3.4h2.6v3.4"/>',
  back: '<path d="M13.2 8H3.4"/><path d="M7.6 3.6 3.2 8l4.4 4.4"/>',
  forward: '<path d="M2.8 8h9.8"/><path d="M8.4 3.6 12.8 8l-4.4 4.4"/>',
  pan: '<path d="M8 1.9v12.2M1.9 8h12.2"/>'
       + '<path d="m6.3 3.6 1.7-1.7 1.7 1.7M6.3 12.4 8 14.1l1.7-1.7'
       + 'M3.6 6.3 1.9 8l1.7 1.7M12.4 6.3 14.1 8l-1.7 1.7"/>',
  zoom: '<circle cx="7" cy="7" r="4.4"/><path d="m10.2 10.2 3.6 3.6"/>'
        + '<path d="M5 7h4M7 5v4"/>',
  select: '<rect x="2.6" y="3.4" width="10.8" height="9.2" stroke-dasharray="2.3 1.7"/>',
  lasso: '<path d="M8 2.8c3 0 5.2 1.4 5.2 3.3 0 1.7-1.8 3.1-4.3 3.3"/>'
         + '<path d="M8 2.8C5 2.8 2.8 4.2 2.8 6.1c0 1 .7 2 1.8 2.6"/>'
         + '<path d="M4.7 8.8c-.6.6-.6 1.6 0 2.1.5.4 1.2.4 1.7.1"/>'
         + '<circle cx="6.6" cy="12.3" r="1.3"/>',
  clear: '<circle cx="8" cy="8" r="5.8"/><path d="m5.7 5.7 4.6 4.6m0-4.6-4.6 4.6"/>',
  save: '<path d="M2.6 2.6h8.5L13.4 5v8.4H2.6z"/><path d="M5.2 2.6h5.2v3.3H5.2z"/>'
        + '<path d="M4.6 9.1h6.8v4.3H4.6z"/>',
};

const EXPORT_FORMATS = [['svg', 'SVG'], ['png', 'PNG'], ['jpeg', 'JPEG'], ['webp', 'WebP']];
const FORMAT_KEY = 'mpact.export.v1';

function loadFormats() {
  try { return JSON.parse(localStorage.getItem(FORMAT_KEY)) || {}; } catch { return {}; }
}
const chosenFormats = loadFormats();

function rememberFormat(key, value) {
  chosenFormats[key] = value;
  try { localStorage.setItem(FORMAT_KEY, JSON.stringify(chosenFormats)); } catch { /* ignore */ }
}

function iconNode(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('width', '16');
  svg.setAttribute('height', '16');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = ICONS[name];
  return svg;
}

/** Axis ranges of every cartesian axis, as a restorable snapshot. */
function snapshot(gd) {
  const layout = gd._fullLayout;
  if (!layout) return null;
  const state = {};
  for (const key of Object.keys(layout)) {
    if (!/^[xy]axis\d*$/.test(key)) continue;
    const axis = layout[key];
    state[key] = axis.autorange
      ? { autorange: true }
      : { range: Array.isArray(axis.range) ? axis.range.slice() : null };
  }
  return Object.keys(state).length ? state : null;
}

const sameSnapshot = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/**
 * Build a navigation bar bound to one plot surface.
 *
 * `sync()` must be called after every draw: it re-reads the drag mode and the
 * plot type (a 3D scene has no box select), restores the export format, and
 * re-attaches the Plotly listeners a purge would have dropped.
 */
export function createNavbar(surface, { onClearSelection, isSelectionActive } = {}) {
  const node = el('div', { class: 'plot-navbar' });
  const buttons = new Map();
  let history = [];
  let cursor = -1;
  let applying = false;
  let hasRegion = false;      // a box/lasso region is currently drawn

  const plot = () => (surface._plotly && typeof window.Plotly !== 'undefined' ? surface : null);
  const is3d = () => Boolean(plot() && surface._fullLayout && surface._fullLayout.scene);

  function button(name, title, onClick, { toggle = false } = {}) {
    const btn = el('button', {
      class: `nav-btn${toggle ? ' toggle' : ''}`, type: 'button', title,
      on: { click: onClick },
    }, [iconNode(name)]);
    btn.setAttribute('aria-label', title);
    buttons.set(name, btn);
    return btn;
  }

  // --- view history -------------------------------------------------------

  function push() {
    const state = snapshot(surface);
    if (!state) return;
    if (cursor >= 0 && sameSnapshot(history[cursor], state)) return;
    history = history.slice(0, cursor + 1);
    history.push(state);
    if (history.length > 40) history.shift();     // no unbounded stack
    cursor = history.length - 1;
    syncEnabled();
  }

  function restore(index) {
    const state = history[index];
    if (!state) return;
    const update = {};
    for (const [axis, value] of Object.entries(state)) {
      if (value.autorange) {
        update[`${axis}.autorange`] = true;
      } else if (value.range) {
        update[`${axis}.autorange`] = false;
        update[`${axis}.range`] = value.range;
      }
    }
    cursor = index;
    applying = true;
    Promise.resolve(Plotly.relayout(surface, update))
      .finally(() => { applying = false; syncEnabled(); });
  }

  // --- actions ------------------------------------------------------------

  function home() {
    const gd = plot();
    if (!gd) return;
    if (is3d()) {
      // Plotly's default 3D camera, which is where the scene starts.
      Plotly.relayout(gd, {
        'scene.camera.eye': { x: 1.25, y: 1.25, z: 1.25 },
        'scene.camera.center': { x: 0, y: 0, z: 0 },
        'scene.camera.up': { x: 0, y: 0, z: 1 },
      });
      return;
    }
    const update = {};
    for (const key of Object.keys(gd._fullLayout || {})) {
      if (/^[xy]axis\d*$/.test(key)) update[`${key}.autorange`] = true;
    }
    applying = true;
    Promise.resolve(Plotly.relayout(gd, update)).finally(() => { applying = false; push(); });
  }

  function setMode(mode) {
    const gd = plot();
    if (!gd) return;
    const current = is3d() ? gd._fullLayout.scene.dragmode : gd._fullLayout.dragmode;
    // Clicking the depressed button releases it, as matplotlib's pan and zoom
    // toggles do.
    const next = current === mode ? false : mode;
    Plotly.relayout(gd, is3d()
      ? { 'scene.dragmode': next === false ? 'turntable' : next }
      : { dragmode: next });
    requestAnimationFrame(syncEnabled);
  }

  function clearSelection() {
    const gd = plot();
    if (gd) {
      try {
        Plotly.restyle(gd, { selectedpoints: [null] });
        Plotly.relayout(gd, { selections: [] });
      } catch { /* a trace type with no selection support */ }
    }
    hasRegion = false;
    if (onClearSelection) onClearSelection();
    syncEnabled();
  }

  function formatKey() {
    const options = (surface._context && surface._context.toImageButtonOptions) || {};
    return options.filename || 'mpact-plot';
  }

  function save() {
    const gd = plot();
    if (!gd) return;
    const format = formatSelect.value;
    const options = (gd._context && gd._context.toImageButtonOptions) || {};
    Plotly.downloadImage(gd, {
      format,
      filename: options.filename || 'mpact-plot',
      // Vector output is resolution independent already; raster gets 2x so a
      // figure still holds up in a document.
      scale: format === 'svg' ? 1 : (options.scale || 2),
      width: gd.clientWidth || 1000,
      height: gd.clientHeight || 700,
    });
  }

  // --- assembly -----------------------------------------------------------

  const formatSelect = el('select', {
    class: 'export-format', title: 'Image export format',
    on: { change: () => rememberFormat(formatKey(), formatSelect.value) },
  }, EXPORT_FORMATS.map(([value, label]) => el('option', { value, text: label })));

  const group = (...children) => el('div', { class: 'nav-group' }, children);

  node.append(
    group(button('home', 'Reset view', home),
          button('back', 'Back to the previous view', () => restore(cursor - 1)),
          button('forward', 'Forward to the next view', () => restore(cursor + 1))),
    group(button('pan', 'Pan', () => setMode('pan'), { toggle: true }),
          button('zoom', 'Zoom to rectangle', () => setMode('zoom'), { toggle: true }),
          button('select', 'Box select', () => setMode('select'), { toggle: true }),
          button('lasso', 'Lasso select', () => setMode('lasso'), { toggle: true })),
    group(button('clear', 'Cancel selection', clearSelection)),
    el('span', { class: 'spacer' }),
    group(formatSelect, button('save', 'Save image', save)),
  );

  // --- state --------------------------------------------------------------

  function syncEnabled() {
    const gd = plot();
    const scene = is3d();
    const mode = !gd ? null
      : (scene ? gd._fullLayout.scene.dragmode : gd._fullLayout.dragmode);

    for (const [name, btn] of buttons) {
      // A 3D scene has no rectangle zoom and no region selection.
      const unsupported = scene && (name === 'select' || name === 'lasso' || name === 'zoom');
      btn.classList.toggle('hidden', unsupported);
      btn.disabled = !gd;
    }
    buttons.get('pan').classList.toggle('active', mode === 'pan');
    buttons.get('zoom').classList.toggle('active', mode === 'zoom');
    buttons.get('select').classList.toggle('active', mode === 'select');
    buttons.get('lasso').classList.toggle('active', mode === 'lasso');
    buttons.get('back').disabled = !gd || cursor <= 0;
    buttons.get('forward').disabled = !gd || cursor < 0 || cursor >= history.length - 1;

    const selected = hasRegion || Boolean(isSelectionActive && isSelectionActive());
    buttons.get('clear').disabled = !selected;
    node.classList.toggle('has-selection', selected);
    formatSelect.disabled = !gd;
  }

  function sync() {
    const gd = plot();
    if (gd) {
      const key = formatKey();
      const fallback = ((gd._context || {}).toImageButtonOptions || {}).format || 'svg';
      formatSelect.value = chosenFormats[key] || fallback;
      if (!formatSelect.value) formatSelect.value = fallback;

      // Plotly's event bus is rebuilt by purge(), so rebind on every draw.
      surface.removeAllListeners?.('plotly_relayout');
      surface.removeAllListeners?.('plotly_selected');
      surface.removeAllListeners?.('plotly_deselect');
      surface.removeAllListeners?.('plotly_doubleclick');
      surface.on('plotly_relayout', () => { if (!applying) push(); });
      surface.on('plotly_selected', () => { hasRegion = true; syncEnabled(); });
      surface.on('plotly_deselect', () => { hasRegion = false; syncEnabled(); });
      surface.on('plotly_doubleclick', () => { hasRegion = false; syncEnabled(); });
      if (cursor < 0) push();
    } else {
      history = [];
      cursor = -1;
      hasRegion = false;
    }
    syncEnabled();
  }

  syncEnabled();
  return { node, sync, syncEnabled };
}
