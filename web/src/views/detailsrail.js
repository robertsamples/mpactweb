/* The selected-feature rail: the app's right-hand panel.
 *
 * There is exactly one of these, mounted beside the main region as the
 * counterpart of the navigation bar on the left, so hiding or resizing it on
 * one plot hides or resizes it on all of them. It is closer to the desktop's
 * compound-details window than a single fixed block: each part (identity,
 * abundance, spectrum, matches) is its own collapsible section, so you can
 * keep open only what you are reading and give the rest of the width back to
 * the plot. Its own header carries the control that hides it.
 */

import { api } from '../api.js';
import { store } from '../state.js';
import { collapsible } from '../components.js';
import { clear, el, num } from '../util.js';
import { FEATURE_FIELDS } from '../fields.js';
import { abundance as drawAbundance, spectrum as drawSpectrum } from '../plots/review.js';

const runId = () => (store.get('run') || {}).run_id;

// Layout preferences, shared by every rail instance and persisted so the app
// reopens the way it was left.
const PREF_KEY = 'mpact.rail.v1';
const prefs = loadPrefs();

function loadPrefs() {
  const fallback = { width: 320, collapsed: false,
                     sections: { identity: true, abundance: true,
                                 spectrum: false, matches: true } };
  try {
    const raw = localStorage.getItem(PREF_KEY);
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback;
  } catch { return fallback; }
}

function savePrefs() {
  try { localStorage.setItem(PREF_KEY, JSON.stringify(prefs)); } catch { /* ignore */ }
}

export const railPrefs = prefs;
export const saveRailPrefs = savePrefs;

// The rail is narrow, so it shows the most-consulted subset of the shared
// field table; Feature Info shows all of them.
const RAIL_FIELDS = new Set([
  'm/z', 'Retention time (min)', 'kmd', 'max', 'fc', 'logfc',
  '-logp', '-logq', 'median CV', 'hits', 'groups',
]);

export function createDetailsRail({ onCollapse, onOpenFeatureInfo } = {}) {
  const node = el('aside', { class: 'details-rail' });
  const sections = el('div', { class: 'rail-sections' });
  // The hide control belongs to the panel it hides, not to a strip above the
  // plot: this is the rail's own title bar, the way the desktop's details
  // window carries its own close button.
  const header = el('div', { class: 'rail-header' }, [
    el('span', { class: 'rail-title', text: 'Details' }),
    el('span', { class: 'spacer' }),
    el('button', {
      class: 'rail-collapse', type: 'button', text: '›',
      title: 'Hide the details panel', on: { click: () => onCollapse && onCollapse() },
    }),
  ]);
  node.append(header, sections);
  applyWidth();

  let token = 0;

  function applyWidth() {
    node.classList.toggle('collapsed', Boolean(prefs.collapsed));
    if (!prefs.collapsed) {
      node.style.flexBasis = `${prefs.width}px`;
      node.style.width = `${prefs.width}px`;
    }
  }

  const section = (key, title, hint, body) => collapsible({
    title, hint, body,
    open: prefs.sections[key] !== false,
    onToggle: (open) => { prefs.sections[key] = open; savePrefs(); },
  });

  const showEmpty = () => {
    node.classList.add('empty');
    clear(sections).append(el('p', { class: 'muted', style: { textAlign: 'center' } },
      ['Click a point, cell or row to inspect a feature.']));
  };

  const render = async () => {
    const compound = store.get('selectedFeature');
    const id = runId();
    if (!compound || !id) { showEmpty(); return; }

    const mine = ++token;
    node.classList.remove('empty');

    const heading = el('div', { class: 'rail-head' }, [
      el('h4', { text: compound }),
      el('button', {
        class: 'btn small', text: '×', title: 'Clear selection', type: 'button',
        on: { click: () => store.set({ selectedFeature: null }) },
      }),
    ]);

    const identityBody = el('div', { class: 'muted', text: 'Loading...' });
    const abundanceBody = el('div', { class: 'mini-plot' });
    const spectrumBody = el('div', { class: 'mini-plot' });
    const matchesBody = el('div', { class: 'muted', text: 'Searching...' });

    const identitySection = section('identity', 'Details', null, identityBody);
    const abundanceSection = section('abundance', 'Abundance', 'by group', abundanceBody);
    const spectrumSection = section('spectrum', 'MS2 spectrum', null, spectrumBody);
    const matchesSection = section('matches', 'Database matches', null, matchesBody);

    const children = [heading, identitySection, abundanceSection,
                      spectrumSection, matchesSection];
    if (onOpenFeatureInfo && store.get('view') !== 'features') {
      children.push(el('div', { class: 'rail-actions' }, [
        el('button', {
          class: 'btn small', text: 'Open in Feature Info', type: 'button',
          on: { click: () => onOpenFeatureInfo() },
        }),
      ]));
    }
    clear(sections).append(...children);

    // --- identity -------------------------------------------------------
    try {
      const detail = await api.feature(id, compound);
      if (mine !== token) return;
      const list = el('dl', { class: 'kv' });
      for (const [key, label, digits] of FEATURE_FIELDS) {
        if (!RAIL_FIELDS.has(key)) continue;
        const value = detail.fields[key];
        if (value === undefined || value === null || value === '') continue;
        list.append(el('dt', { text: label }),
                    el('dd', { text: digits === null ? String(value).trim() : num(value, digits) }));
      }
      identitySection.setBody(list);
    } catch (error) {
      if (mine !== token) return;
      identitySection.setBody(el('div', { class: 'notice error', text: error.message }));
    }

    // --- abundance (only fetched when the section is open) --------------
    if (abundanceSection.isOpen()) {
      try {
        const payload = await api.abundance(id, compound);
        if (mine !== token) return;
        drawAbundance(abundanceBody, payload, { compact: true });
      } catch {
        if (mine !== token) return;
        abundanceBody.replaceChildren(el('div', { class: 'placeholder',
          text: 'No abundance data: this feature did not pass filtering.' }));
      }
    }

    // --- spectrum -------------------------------------------------------
    if (spectrumSection.isOpen()) {
      try {
        const payload = await api.spectrum(id, compound);
        if (mine !== token) return;
        drawSpectrum(spectrumBody, payload, { compact: true });
      } catch (error) {
        if (mine !== token) return;
        spectrumBody.replaceChildren(el('div', { class: 'placeholder', text: error.message }));
      }
    }

    // --- database matches ------------------------------------------------
    if (matchesSection.isOpen()) {
      try {
        const payload = await api.matches(id, compound);
        if (mine !== token) return;
        matchesSection.setBody(matchesPanel(payload, id));
      } catch (error) {
        if (mine !== token) return;
        matchesSection.setBody(el('div', { class: 'notice warn', text: error.message }));
      }
    }
  };

  /* Hit list and structure together, with the structure following the
   * highlighted hit. The desktop's compound window shows both at once and
   * redraws the structure as you move down the list, so a match is never a
   * bare name you have to click before you can see what it is. */
  function matchesPanel(payload, id) {
    if (!payload.matches.length) {
      return el('p', { class: 'muted' },
        [`No match within ${payload.ppm_threshold} ppm of m/z ${num(payload.mz, 4)}.`]);
    }

    let index = 0;
    const structure = el('div', { class: 'structure' });
    const caption = el('div', { class: 'match-caption' });
    const position = el('span', { class: 'muted' });
    // Focusable so the arrow keys reach it: every list in this app should
    // step with the keyboard, not just the feature table.
    const list = el('div', { class: 'match-list', tabIndex: 0 });

    const rows = payload.matches.map((match, i) => el('button', {
      class: 'match-row', type: 'button',
      on: { click: () => show(i) },
    }, [
      el('span', { class: 'match-name', text: match.compound_name || '(unnamed)' }),
      el('span', { class: 'match-ppm', text: `${num(match.ppm, 2)} ppm` }),
    ]));
    list.append(...rows);

    function show(next) {
      index = Math.max(0, Math.min(payload.matches.length - 1, next));
      const match = payload.matches[index];
      rows.forEach((row, i) => {
        row.classList.toggle('active', i === index);
        // Only the active row is a tab stop, so Tab moves past the list
        // rather than through every hit in it.
        row.tabIndex = i === index ? 0 : -1;
      });
      rows[index].scrollIntoView({ block: 'nearest' });
      position.textContent = `${index + 1} of ${payload.matches.length}`;

      clear(caption).append(
        el('div', { class: 'match-title', text: match.compound_name || '(unnamed)' }),
        el('div', { class: 'muted', text: [
          match.compound_molecular_formula,
          `${num(match.compound_accurate_mass, 4)} Da`,
          `${num(match.ppm, 2)} ppm`,
        ].filter(Boolean).join('  ·  ') }),
        el('div', { class: 'muted',
                    text: `${match.genus || ''} ${match.origin_species || ''}`.trim() }));

      clear(structure);
      if (!match.compound_smiles) {
        structure.append(el('div', { class: 'muted', text: 'No structure for this match.' }));
        return;
      }
      structure.append(el('img', {
        src: api.structureUrl(id, match.compound_smiles),
        alt: match.compound_name || 'structure',
        on: { error: () => clear(structure).append(el('div', { class: 'muted',
          text: 'Structure rendering is unavailable on this server.' })) },
      }));
    }

    list.addEventListener('keydown', (event) => {
      const step = { ArrowDown: 1, ArrowUp: -1, PageDown: 10, PageUp: -10 }[event.key];
      if (step !== undefined) { event.preventDefault(); show(index + step); return; }
      if (event.key === 'Home') { event.preventDefault(); show(0); }
      if (event.key === 'End') { event.preventDefault(); show(payload.matches.length - 1); }
    });

    const nav = el('div', { class: 'match-nav' }, [
      el('button', { class: 'btn small', type: 'button', text: '\u2039',
                     title: 'Previous match', on: { click: () => show(index - 1) } }),
      position,
      el('button', { class: 'btn small', type: 'button', text: '\u203a',
                     title: 'Next match', on: { click: () => show(index + 1) } }),
    ]);

    const panel = el('div', { class: 'matches-panel' }, [nav, structure, caption, list]);
    show(0);
    return panel;
  }

  showEmpty();

  return {
    node,
    render,
    isEmpty: () => !store.get('selectedFeature'),
    applyWidth,
    setCollapsed(value) {
      prefs.collapsed = Boolean(value);
      savePrefs();
      applyWidth();
    },
    isCollapsed: () => Boolean(prefs.collapsed),
    setWidth(width) {
      prefs.width = width || 300;
      savePrefs();
      applyWidth();
    },
  };
}


/* One rail for the whole app. Every view addresses this instance, so the
 * width, the collapsed state and which sections are open are the same
 * wherever you are. */
let instance = null;

export function detailsRail(options) {
  if (!instance) instance = createDetailsRail(options);
  return instance;
}
