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
import { exportSelected } from '../export.js';
import { flagControls, flags, toggleFlag } from '../flags.js';
import { selection, store } from '../state.js';
import { collapsible, makeRowResizer } from '../components.js';
import { clear, el, num, toast } from '../util.js';
import { UNIT, UNITS, analogueVectors, clearVectors, findAnalogues, removeVector,
         stepsText } from '../analogues.js';
import { FEATURE_FIELDS } from '../fields.js';
import { abundance as drawAbundance, spectrum as drawSpectrum } from '../plots/review.js';
import { plotlyReady } from '../plots/base.js';

const runId = () => (store.get('run') || {}).run_id;

// Layout preferences, shared by every rail instance and persisted so the app
// reopens the way it was left.
const PREF_KEY = 'mpact.rail.v1';
const prefs = loadPrefs();

function loadPrefs() {
  const fallback = { width: 320, collapsed: false,
                     sections: { identity: true, analogues: false, abundance: true,
                                 spectrum: false, matches: true },
                     // Panel heights, dragged by the boundary under each plot.
                     heights: { abundance: 235, spectrum: 200 },
                     // The analogue search: which steps, pairs of them, and
                     // the allowed error on the m/z difference (mDa).
                     analogues: { units: UNITS.map((unit) => unit.key), combine: true,
                                  tolerance: 2 } };
  try {
    const raw = localStorage.getItem(PREF_KEY);
    if (!raw) return fallback;
    const saved = JSON.parse(raw);
    // Heights were added after the first release of this key, so merge rather
    // than trust whatever shape is in storage.
    return { ...fallback, ...saved,
             sections: { ...fallback.sections, ...(saved.sections || {}) },
             heights: { ...fallback.heights, ...(saved.heights || {}) },
             analogues: { ...fallback.analogues, ...(saved.analogues || {}) } };
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
  // With several features selected the top of the rail becomes the
  // selection: how many, the list (click one to inspect it below), and the
  // export. The sections underneath go on describing the focused feature.
  const selectionBox = el('div', { class: 'rail-selection hidden' });
  node.append(header, selectionBox, sections);
  applyWidth();

  const LISTED = 150;       // names shown; a lasso can select thousands

  function renderSelection() {
    const ids = selection.ids();
    const several = ids.length > 1;
    selectionBox.classList.toggle('hidden', !several);
    clear(selectionBox);
    if (!several) return;
    const focused = store.get('selectedFeature');

    const list = el('div', { class: 'selection-list' }, ids.slice(0, LISTED).map((id) =>
      el('div', { class: `selection-item ${id === focused ? 'active' : ''}`.trim() }, [
        el('button', {
          class: 'selection-name', type: 'button', text: id, title: 'Show this feature below',
          on: { click: () => selection.focus(id) },
        }),
        el('button', {
          class: 'selection-remove', type: 'button', text: '×',
          title: 'Remove from the selection', on: { click: () => selection.remove(id) },
        }),
      ])));
    if (ids.length > LISTED) {
      list.append(el('div', { class: 'selection-more muted',
                              text: `… and ${(ids.length - LISTED).toLocaleString()} more` }));
    }

    selectionBox.append(
      el('div', { class: 'selection-head' }, [
        el('strong', { text: `${ids.length.toLocaleString()} features selected` }),
        el('span', { class: 'spacer' }),
        el('button', {
          class: 'btn small', type: 'button', text: 'Export CSV',
          title: 'Save the selected features, with their best database matches',
          on: { click: (event) => exportSelected(event.currentTarget) },
        }),
        el('button', {
          class: 'btn small', type: 'button', text: 'Clear', title: 'Deselect everything',
          on: { click: () => selection.clear() },
        }),
      ]),
      // Flag them: a lasso on a plot can be marked without a trip to the table.
      el('div', { class: 'selection-flag' }, [flagControls().node]),
      list);
    revealIn(list, list.querySelector('.selection-item.active'));
  }
  store.on(['selectedFeatures', 'selectedFeature', 'flags'], renderSelection);

  // The focused feature's own flag, in its heading.
  let paintFlag = null;
  store.on('flags', () => { if (paintFlag) paintFlag(); });

  let token = 0;

  function applyWidth() {
    node.classList.toggle('collapsed', Boolean(prefs.collapsed));
    if (!prefs.collapsed) {
      node.style.flexBasis = `${prefs.width}px`;
      node.style.width = `${prefs.width}px`;
    }
  }

  const section = (key, title, hint, body, onOpen) => collapsible({
    title, hint, body,
    open: prefs.sections[key] !== false,
    onToggle: (open) => {
      prefs.sections[key] = open;
      savePrefs();
      // Expanding a section fills it -- the plot must not wait for the user
      // to re-select the feature it already knows about.
      if (open && onOpen) onOpen();
    },
  });

  const showEmpty = () => {
    node.classList.add('empty');
    clear(sections).append(el('p', { class: 'muted', style: { textAlign: 'center' } },
      ['Click a point, cell or row to inspect a feature.']));
  };

  let refreshMatches = null;
  store.on(['hitFilter', 'msmsVersion'], () => { if (refreshMatches) refreshMatches(); });
  let refreshAnalogues = null;
  store.on('analogues', () => { if (refreshAnalogues) refreshAnalogues(); });

  // Re-rendering the same feature (every view switch asks) would reload
  // every section and make the rail flash.
  let rendered = null;
  let actions = null;
  store.on('view', () => {
    if (actions) actions.classList.toggle('hidden', store.get('view') === 'features');
  });

  const render = async ({ force = false } = {}) => {
    const compound = store.get('selectedFeature');
    const id = runId();
    if (!compound || !id) { rendered = null; showEmpty(); return; }
    const key = `${id}\u0000${compound}`;
    if (!force && key === rendered) return;
    rendered = key;

    const mine = ++token;
    // Where you had the rail scrolled to, so the next feature opens at the
    // same place: stepping through features to compare their hit structures
    // should not mean scrolling back down to the matches every time.
    if (node.classList.contains('empty')) wantedTop = 0;
    const keepTop = wantedTop;
    node.classList.remove('empty');

    const flagDot = el('span', { class: 'flag-dot' });
    const flagButton = el('button', {
      class: 'flag-toggle', type: 'button', on: { click: () => toggleFlag(compound) },
    }, [flagDot]);
    paintFlag = () => {
      const colour = flags.colourOf(compound);
      flagDot.style.background = colour || 'transparent';
      flagButton.classList.toggle('flagged', Boolean(colour));
      flagButton.title = colour ? 'Flagged: click to unflag' : 'Flag this feature';
    };
    paintFlag();

    const heading = el('div', { class: 'rail-head' }, [
      el('h4', { text: compound }),
      flagButton,
      el('button', {
        class: 'btn small', text: '×', type: 'button',
        title: selection.count() > 1 ? 'Remove this feature from the selection'
                                     : 'Clear selection',
        // Among several, closing one feature takes that one out; the focus
        // moves to another and the rest stay selected.
        on: { click: () => (selection.count() > 1 ? selection.remove(compound)
                                                  : selection.clear()) },
      }),
    ]);

    const identityBody = el('div', { class: 'muted', text: 'Loading...' });
    const abundance = resizablePlot('abundance');
    const spectrum = resizablePlot('spectrum');
    const abundanceBody = abundance.plot;
    const spectrumBody = spectrum.plot;
    const matchesBody = el('div', { class: 'muted', text: 'Searching...' });

    // Each section fills itself, once per selected feature: render() calls
    // the loaders for the sections that start open, and expanding a section
    // later calls its own, so the content never waits on a re-selection.
    const loaded = new Set();

    const loadIdentity = async () => {
      if (loaded.has('identity')) return;
      loaded.add('identity');
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
        loaded.delete('identity');
        if (mine !== token) return;
        identitySection.setBody(el('div', { class: 'notice error', text: error.message }));
      }
    };

    const loadAbundance = async () => {
      if (loaded.has('abundance')) return;
      loaded.add('abundance');
      abundanceBody.replaceChildren(el('div', { class: 'placeholder', text: 'Loading...' }));
      try {
        const [payload] = await Promise.all([api.abundance(id, compound), plotlyReady()]);
        if (mine !== token) return;
        drawAbundance(abundanceBody, payload, { compact: true });
      } catch {
        loaded.delete('abundance');
        if (mine !== token) return;
        abundanceBody.replaceChildren(el('div', { class: 'placeholder',
          text: 'No abundance data: this feature did not pass filtering.' }));
      }
    };

    // One request per feature to each match source, shared by the spectrum
    // (its mirror reference and its fragment formulae) and the match browser.
    let atlasRequest = null;
    const atlasMatches = () => {
      if (!atlasRequest) atlasRequest = api.matches(id, compound, store.get('hitFilter'));
      return atlasRequest;
    };
    let msmsRequest = null;
    const msmsMatches = () => {
      if (!msmsRequest) {
        msmsRequest = (store.get('health') || {}).msms
          ? api.msms(id, compound, store.get('hitFilter')).catch(() => null)
          : Promise.resolve(null);
      }
      return msmsRequest;
    };
    let spectrumPayload = null;
    let reference = null;
    // Fragment formulae are those of one candidate parent: the match being
    // looked at in the browser, or until one is, the best match there is.
    let formulas = null;
    let formulaChosen = false;
    const spectrumNote = el('div', { class: 'spectrum-note muted' });
    const drawSpec = () => {
      if (spectrumPayload) {
        drawSpectrum(spectrumBody, spectrumPayload, { compact: true, reference, formulas });
      }
    };
    const useReference = (match, payload) => {
      reference = match ? {
        mz: match.peak_mz, intensity: match.peak_intensity, name: match.name,
        shift: payload && payload.match_precursor === false ? match.delta_mz : null,
      } : null;
      drawSpec();
    };
    const useFormulas = (payload, match, chosen = true) => {
      const entry = match && match.fragment_key && payload && payload.fragments
        ? payload.fragments[match.fragment_key] : null;
      if (chosen) formulaChosen = true;
      formulas = entry ? new Map(entry.peaks.map((peak) => [peak.index, peak])) : null;
      spectrumNote.textContent = entry
        ? `Fragment formulae as ${entry.formula} ${entry.adduct}` + fragmentSummary(entry, ': ')
        : '';
      drawSpec();
    };
    /** Until a match is picked in the browser: the MS/MS library match the
     *  mirror shows if it has a formula, otherwise the closest mass match
     *  that does. */
    const defaultFormulas = (msms) => {
      if (formulaChosen) return;
      const matches = (msms && msms.matches) || [];
      const mirrored = matches.find((match) => match.passes) || matches[0];
      if (mirrored && mirrored.fragment_key) { useFormulas(msms, mirrored, false); return; }
      atlasMatches().then((atlas) => {
        if (mine !== token || formulaChosen) return;
        const first = atlas.matches.find((match) => match.fragment_key);
        if (first) useFormulas(atlas, first, false);
      }).catch(() => { /* no mass matches: the peaks keep their m/z labels */ });
    };

    const loadSpectrum = async () => {
      if (loaded.has('spectrum')) return;
      loaded.add('spectrum');
      spectrumBody.replaceChildren(el('div', { class: 'placeholder', text: 'Loading...' }));
      try {
        const [payload, msms] = await Promise.all([api.spectrum(id, compound), msmsMatches(),
                                                   plotlyReady()]);
        if (mine !== token) return;
        spectrumPayload = payload;
        if (!reference && msms && msms.matches && msms.matches.length) {
          useReference(msms.matches.find((match) => match.passes) || msms.matches[0], msms);
        } else {
          drawSpec();
        }
        if (payload.found) defaultFormulas(msms);
      } catch (error) {
        loaded.delete('spectrum');
        if (mine !== token) return;
        spectrumBody.replaceChildren(el('div', { class: 'placeholder', text: error.message }));
      }
    };

    const loadMatches = async () => {
      if (loaded.has('matches')) return;
      loaded.add('matches');
      const health = store.get('health') || {};
      const [atlas, msms] = await Promise.allSettled([
        atlasMatches(),
        health.msms ? msmsMatches() : Promise.resolve(null),
      ]);
      if (mine !== token) return;
      const sources = [atlasSource(atlas, (match) => useFormulas(atlas.value, match))];
      if (msms.status === 'fulfilled' && msms.value && msms.value.searched) {
        sources.push(msmsSource(msms.value, (match) => {
          useReference(match, msms.value);
          useFormulas(msms.value, match);
        }));
      }
      matchesSection.setBody(matchesPanel(sources, id));
    };

    refreshMatches = () => {
      if (mine !== token) return;
      atlasRequest = null;
      msmsRequest = null;
      reference = null;
      formulaChosen = false;
      useFormulas(null, null, false);
      loaded.delete('matches');
      if (matchesSection.isOpen()) loadMatches();
      if (spectrumPayload) {
        msmsMatches().then((msms) => {
          if (mine !== token) return;
          const matches = (msms && msms.matches) || [];
          useReference(matches.find((match) => match.passes) || matches[0], msms);
          if (spectrumPayload.found) defaultFormulas(msms);
        });
      }
    };

    const analogueBody = el('div', { class: 'analogues' });
    refreshAnalogues = () => {
      if (mine === token) analogueBody.replaceChildren(...analoguesPanel(compound));
    };
    refreshAnalogues();

    const identitySection = section('identity', 'Details', null, identityBody, loadIdentity);
    const analogueSection = section('analogues', 'Analogues', 'by formula step', analogueBody);
    const abundanceSection = section('abundance', 'Abundance', 'by group', abundance.node,
                                     loadAbundance);
    const spectrumSection = section('spectrum', 'MS2 spectrum', null,
                                    el('div', {}, [spectrum.node, spectrumNote]), loadSpectrum);
    const matchesSection = section('matches', 'Database matches', null, matchesBody, loadMatches);

    const children = [heading, identitySection, analogueSection, abundanceSection,
                      spectrumSection, matchesSection];
    if (onOpenFeatureInfo) {
      children.push(actions = el('div', {
        class: `rail-actions ${store.get('view') === 'features' ? 'hidden' : ''}` }, [
        el('button', {
          class: 'btn small', text: 'Open in Feature Info', type: 'button',
          on: { click: () => onOpenFeatureInfo() },
        }),
      ]));
    }
    // The new sections start as short "Loading..." placeholders, and with
    // the content that short the browser has nowhere to scroll to: it reset
    // the rail to the top on every new feature. A spacer at the end keeps the
    // content as tall as it was until the sections have filled in, so the
    // scroll position holds; then it is taken away.
    const hold = el('div', { class: 'rail-hold', 'aria-hidden': 'true' });
    clear(sections).append(...children, hold);
    if (keepTop > 0) {
      // How tall the new content is, measured to the spacer. (Not from
      // scrollHeight: that never reads less than the rail's own height, so
      // while the content was shorter than the window the spacer came out
      // too small, and the position was lost anyway.)
      const box = sections.getBoundingClientRect();
      const used = hold.getBoundingClientRect().top - box.top + sections.scrollTop
        + (parseFloat(getComputedStyle(sections).paddingBottom) || 0);
      hold.style.height = `${Math.max(0, keepTop + sections.clientHeight - used)}px`;
      scrollRailTo(keepTop);
    }

    const loads = [];
    for (const [open, load] of [[identitySection.isOpen(), loadIdentity],
                                [abundanceSection.isOpen(), loadAbundance],
                                [spectrumSection.isOpen(), loadSpectrum],
                                [matchesSection.isOpen(), loadMatches]]) {
      if (open) loads.push(load());
    }
    await Promise.allSettled(loads);
    // The structure drawing arrives after its match does; wait for it too
    // (briefly -- a slow render must not pin the spacer in place).
    await imagesLoaded(sections, 3000);
    if (mine !== token || !hold.isConnected) return;
    const top = sections.scrollTop;
    hold.remove();
    // A shorter feature clamps this to its bottom, which is as near to the
    // place as it has. `wantedTop` is left alone, so the next feature that is
    // long enough opens at the place itself again.
    scrollRailTo(top);
  };

  // Where you last scrolled the rail to. Positions the rail sets itself are
  // told apart from yours by remembering what it set, so a clamp to a short
  // feature's bottom is not mistaken for you scrolling up.
  let wantedTop = 0;
  let setTop = null;
  function scrollRailTo(top) {
    sections.scrollTop = top;
    setTop = sections.scrollTop;          // after any clamping
  }
  sections.addEventListener('scroll', () => {
    if (setTop !== null && Math.abs(sections.scrollTop - setTop) <= 1) return;
    setTop = null;
    // While a feature is still loading the spacer is in, and the range is
    // the held one: a scroll then is still yours.
    wantedTop = sections.scrollTop;
  }, { passive: true });

  /** A plot panel whose bottom edge can be dragged, so the space between the
   *  abundance chart and the spectrum below it is yours to divide. Heights
   *  persist with the rest of the rail's layout; double-click resets. */
  function resizablePlot(key) {
    const plot = el('div', { class: 'mini-plot' });
    plot.style.height = `${prefs.heights[key]}px`;
    const handle = el('div', { class: 'section-resizer',
                               title: 'Drag to resize, double-click to reset' });
    const node = el('div', { class: 'mini-plot-wrap' }, [plot, handle]);

    const redraw = () => {
      if (plot._plotly && typeof window.Plotly !== 'undefined') {
        try { Plotly.Plots.resize(plot); } catch { /* not drawn yet */ }
      }
    };
    makeRowResizer(handle, plot, {
      min: 110,
      max: 640,
      onResize: redraw,
      onCommit: (height) => {
        prefs.heights[key] = height || (key === 'abundance' ? 235 : 200);
        savePrefs();
        redraw();
      },
    });
    return { node, plot };
  }

  /** The Analogues section for the focused feature: what to search for, the
   *  search, and every vector found so far (each removable). */
  function analoguesPanel(compound) {
    const options = prefs.analogues;
    const chips = el('div', { class: 'unit-chips' }, UNITS.map((unit) => el('button', {
      class: `unit-chip ${options.units.includes(unit.key) ? 'on' : ''}`.trim(), type: 'button',
      text: unit.text, title: `Search for features one ${unit.text} away`,
      on: { click: (event) => {
        const on = !options.units.includes(unit.key);
        options.units = UNITS.map((item) => item.key)
          .filter((key) => (key === unit.key ? on : options.units.includes(key)));
        event.currentTarget.classList.toggle('on', on);
        savePrefs();
      } },
    })));
    const pairs = el('label', {
      class: 'check', title: 'Also two steps together, such as +O −H₂ (an alcohol to an acid)',
    }, [
      el('input', { type: 'checkbox', checked: options.combine,
                    on: { change: (event) => { options.combine = event.target.checked; savePrefs(); } } }),
      el('span', { text: 'Pairs' }),
    ]);
    const tolerance = el('label', { title: 'Allowed error on the m/z difference' }, [
      el('span', { text: '±' }),
      el('input', {
        type: 'number', class: 'analogue-tolerance', min: 0.1, max: 20, step: 0.5,
        value: options.tolerance,
        on: { change: (event) => {
          const value = parseFloat(event.target.value);
          if (Number.isNaN(value) || value <= 0 || value > 20) {
            event.target.value = options.tolerance;
            return;
          }
          options.tolerance = value;
          savePrefs();
        } },
      }),
      el('span', { text: 'mDa' }),
    ]);

    const search = el('button', {
      class: 'btn small primary', type: 'button', text: 'Search analogues',
      title: 'Select the features one or two of these steps from this one, and draw the '
             + 'vectors to them on the Mass Defect plot',
      on: { click: async () => {
        if (!options.units.length) { toast('Choose at least one step to search for.', 'error'); return; }
        search.disabled = true;
        try {
          const found = await findAnalogues(compound, {
            units: options.units, combine: options.combine, tolerance: options.tolerance / 1000,
          });
          toast(found ? `${found} analogue${found === 1 ? '' : 's'} of ${compound} selected`
                      : `No analogues of ${compound} within ±${options.tolerance} mDa`,
                found ? 'ok' : 'info');
        } catch (error) {
          toast(error.message, 'error');
        } finally {
          search.disabled = false;
        }
      } },
    });

    const vectors = analogueVectors();
    const nodes = [el('div', { class: 'analogue-controls' }, [chips, pairs, tolerance]),
                   el('div', { class: 'analogue-actions' }, [
                     search, el('span', { class: 'spacer' }),
                     vectors.length ? el('button', {
                       class: 'btn small', type: 'button', text: 'Clear vectors',
                       title: 'Remove every vector from the plot (the selection stays)',
                       on: { click: () => clearVectors() },
                     }) : null,
                   ])];
    if (!vectors.length) return nodes;

    // Grouped by where each vector starts, in the order the starts appeared.
    const groups = new Map();
    for (const vector of vectors) {
      if (!groups.has(vector.from)) groups.set(vector.from, []);
      groups.get(vector.from).push(vector);
    }
    const focus = (feature) => (selection.asSet().has(feature) ? selection.focus(feature)
                                                               : selection.add([feature], feature));
    const list = el('div', { class: 'analogue-list' });
    for (const [from, members] of groups) {
      list.append(el('button', {
        class: `analogue-from ${from === compound ? 'active' : ''}`.trim(), type: 'button',
        text: `from ${from}`, title: 'Show this feature', on: { click: () => focus(from) },
      }));
      for (const vector of members) {
        const error = 1000 * vector.error;
        list.append(el('div', { class: `analogue-row ${vector.to === compound ? 'active' : ''}`.trim() }, [
          el('span', { class: 'analogue-steps', title: stepsText(vector.steps) },
             vector.steps.map((step) => el('span', { class: 'analogue-step' }, [
               el('span', { class: 'vector-swatch',
                            style: { background: UNIT.get(step.unit).colour } }),
               `${step.sign > 0 ? '+' : '−'}${UNIT.get(step.unit).text}`,
             ]))),
          el('button', { class: 'analogue-name', type: 'button', text: vector.to,
                         title: 'Show this feature', on: { click: () => focus(vector.to) } }),
          el('span', { class: 'analogue-error muted',
                       title: 'Observed m/z difference minus the expected one',
                       text: `${error >= 0 ? '+' : '−'}${num(Math.abs(error), 2)}` }),
          el('button', { class: 'analogue-remove', type: 'button', text: '×',
                         title: 'Remove this vector, and deselect the feature',
                         on: { click: () => removeVector(vector.key) } }),
        ]));
      }
    }
    nodes.push(list);
    return nodes;
  }

  /** ": 5 of 7 peaks, 86% of intensity" for a candidate's fragment score. */
  function fragmentSummary(fragments, lead = '') {
    if (!fragments || !fragments.total) return '';
    return `${lead}${fragments.matched} of ${fragments.total} peaks, `
           + `${Math.round(100 * (fragments.explained || 0))}% of intensity`;
  }

  function atlasSource(result, onShow) {
    if (result.status !== 'fulfilled') {
      return { key: 'npatlas', label: 'NPAtlas', items: [], empty: result.reason.message };
    }
    const payload = result.value;
    const filtered = payload.matches.some((match) => match.passes !== undefined);
    const scored = payload.matches.some((match) => typeof match.score === 'number');
    // Mass error first, as the search returns them, or the composite of
    // mass error and fragments explained (unscored matches last).
    const byScore = scored ? [...payload.matches].sort((a, b) =>
      (b.score ?? -1) - (a.score ?? -1) || a.ppm - b.ppm) : null;
    return {
      key: 'npatlas', label: 'NPAtlas', items: payload.matches, onShow,
      sorts: scored ? [['ppm', 'ppm'], ['score', 'score']] : null,
      order: (sort) => (sort === 'score' && byScore ? byScore : payload.matches),
      empty: `No match within ${payload.ppm_threshold} ppm of m/z ${num(payload.mz, 4)}.`,
      first: (items) => (filtered ? Math.max(0, items.findIndex((match) => match.passes)) : 0),
      dim: (match) => filtered && !match.passes,
      name: (match) => match.compound_name || '(unnamed)',
      right: (match) => `${num(match.ppm, 2)} ppm`
        + (match.fragments && match.fragments.total
          ? ` · ${Math.round(100 * (match.fragments.explained || 0))}%` : ''),
      smiles: (match) => match.compound_smiles,
      caption: (match) => [
        [match.compound_molecular_formula, match.adduct,
         `${num(match.compound_accurate_mass, 4)} Da`, `${num(match.ppm, 2)} ppm`],
        [fragmentSummary(match.fragments, 'Fragments: '),
         typeof match.score === 'number' ? `score ${num(match.score, 2)}` : ''],
        [`${match.genus || ''} ${match.origin_species || ''}`.trim(),
         match.source_database && match.source_database !== 'NPAtlas' ? match.source_database : ''],
      ],
      tags: (match) => [...(match.warheads || []), match.siderophore ? 'Siderophore' : '',
                        match.conjugated ? `${match.conjugated} conjugated atoms` : ''],
    };
  }

  function msmsSource(payload, onShow) {
    const filtered = payload.matches.some((match) => match.passes !== undefined);
    return {
      key: 'msms', label: 'MS-DIAL', items: payload.matches, onShow,
      empty: payload.has_spectrum ? `No match with cosine ≥ ${payload.min_score}.`
                                  : 'No MS/MS spectrum searched.',
      first: (items) => (filtered ? Math.max(0, items.findIndex((match) => match.passes)) : 0),
      dim: (match) => filtered && !match.passes,
      name: (match) => match.name || '(unnamed)',
      right: (match) => `cos ${num(match.score, 3)}`,
      smiles: (match) => match.smiles,
      caption: (match) => [
        [match.formula, match.precursortype, `m/z ${num(match.precursor_mz, 4)}`],
        [`cosine ${num(match.score, 3)}`, `${match.matched} peaks`,
         match.delta_mz === null ? '' : `Δm/z ${num(match.delta_mz, 4)}`,
         match.feature_ion ? `as ${match.feature_ion}` : ''],
        [fragmentSummary(match.fragments, 'Fragments: ')],
        [match.ontology],
      ],
      tags: (match) => [...(match.warheads || []), match.siderophore ? 'Siderophore' : '',
                        match.conjugated ? `${match.conjugated} conjugated atoms` : ''],
    };
  }

  /* Hit list and structure together, with the structure following the
   * highlighted hit, and a switch between sources when there is more than
   * one. The chosen source carries over to the next feature. */
  function matchesPanel(sources, id) {
    const structure = el('div', { class: 'structure' });
    const caption = el('div', { class: 'match-caption' });
    const position = el('span', { class: 'muted' });
    // Focusable so the arrow keys reach it: every list in this app should
    // step with the keyboard, not just the feature table.
    const list = el('div', { class: 'match-list', tabIndex: 0 });
    const body = el('div', { class: 'matches-body' });
    const prev = el('button', { class: 'btn small', type: 'button', text: '\u2039',
                                title: 'Previous match', on: { click: () => show(index - 1) } });
    const next = el('button', { class: 'btn small', type: 'button', text: '\u203a',
                                title: 'Next match', on: { click: () => show(index + 1) } });
    let source = sources.find((entry) => entry.key === preferredSource) || sources[0];
    let items = [];
    let index = 0;
    let rows = [];
    // Shown for a source whose matches can be put in more than one order.
    const sorter = el('button', {
      class: 'btn small sort-switch hidden', type: 'button',
      title: 'Order by mass error, or by the score that combines mass error with the '
             + 'fragments each formula explains',
      on: { click: () => {
        const keys = source.sorts.map(([key]) => key);
        preferredSort = keys[(keys.indexOf(preferredSort) + 1) % keys.length];
        use(source);
      } },
    });

    const toggle = sources.length > 1 ? el('div', { class: 'source-toggle' }, sources.map((entry) =>
      el('button', {
        class: 'btn small', type: 'button', text: entry.label, dataset: { key: entry.key },
        on: { click: () => { preferredSource = entry.key; use(entry); } },
      }))) : null;

    function use(entry) {
      source = entry;
      if (toggle) {
        for (const button of toggle.children) {
          button.classList.toggle('primary', button.dataset.key === entry.key);
        }
      }
      const sort = entry.sorts && (entry.sorts.find(([key]) => key === preferredSort)
                                   || entry.sorts[0]);
      sorter.classList.toggle('hidden', !sort);
      if (sort) sorter.textContent = `Sort: ${sort[1]}`;
      items = entry.order ? entry.order(sort ? sort[0] : null) : entry.items;
      rows = items.map((match, i) => el('button', {
        class: `match-row ${entry.dim(match) ? 'filtered-out' : ''}`, type: 'button',
        on: { click: () => show(i) },
      }, [
        el('span', { class: 'match-name', text: entry.name(match) }),
        el('span', { class: 'match-ppm', text: entry.right(match) }),
      ]));
      clear(list).append(...rows);
      const empty = !items.length;
      prev.disabled = empty;
      next.disabled = empty;
      if (empty) {
        position.textContent = '0 of 0';
        clear(body).append(el('p', { class: 'muted' }, [entry.empty]));
        return;
      }
      clear(body).append(structure, caption, list);
      show(entry.first ? entry.first(items) : 0);
    }

    function show(target) {
      if (!items.length) return;
      index = Math.max(0, Math.min(items.length - 1, target));
      const match = items[index];
      rows.forEach((row, i) => {
        row.classList.toggle('active', i === index);
        // Only the active row is a tab stop, so Tab moves past the list
        // rather than through every hit in it.
        row.tabIndex = i === index ? 0 : -1;
      });
      // Scrolls the hit list only. scrollIntoView would scroll every
      // container above it as well -- the whole rail included.
      revealIn(list, rows[index]);
      position.textContent = `${index + 1} of ${items.length}`;
      if (source.onShow) source.onShow(match);

      const line = (parts) => el('div', { class: 'muted',
                                          text: parts.filter(Boolean).join('  ·  ') });
      clear(caption).append(
        el('div', { class: 'match-title', text: source.name(match) }),
        ...source.caption(match).map(line),
        el('div', { class: 'match-tags', text: source.tags(match).filter(Boolean).join('  ·  ') }));

      clear(structure);
      const smiles = source.smiles(match);
      if (!smiles) {
        structure.append(el('div', { class: 'muted', text: 'No structure for this match.' }));
        return;
      }
      structure.append(el('img', {
        src: api.structureUrl(id, smiles),
        alt: source.name(match),
        on: { error: () => clear(structure).append(el('div', { class: 'muted',
          text: 'Structure rendering is unavailable on this server.' })) },
      }));
    }

    list.addEventListener('keydown', (event) => {
      const step = { ArrowDown: 1, ArrowUp: -1, PageDown: 10, PageUp: -10 }[event.key];
      if (step !== undefined) { event.preventDefault(); show(index + step); return; }
      if (event.key === 'Home') { event.preventDefault(); show(0); }
      if (event.key === 'End') { event.preventDefault(); show(items.length - 1); }
    });

    const nav = el('div', { class: `match-nav ${toggle ? 'with-toggle' : ''}` },
                   [toggle, el('div', { class: 'match-steps' }, [sorter, prev, position, next])]);
    const panel = el('div', { class: 'matches-panel' }, [nav, body]);
    use(source);
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


/** Scroll `container` (and nothing else) so that `child` is in view. Before
 *  the container is on the page there is nothing to measure, so it waits a
 *  frame -- a list built off-screen still opens at its active row. */
function revealIn(container, child) {
  if (!container || !child) return;
  if (!container.isConnected) {
    requestAnimationFrame(() => { if (container.isConnected) revealIn(container, child); });
    return;
  }
  const box = container.getBoundingClientRect();
  const item = child.getBoundingClientRect();
  if (item.top < box.top) container.scrollTop -= box.top - item.top;
  else if (item.bottom > box.bottom) container.scrollTop += item.bottom - box.bottom;
}

/** Resolves when every image inside `root` has loaded (or failed), or after
 *  `timeout` ms, whichever is first. */
function imagesLoaded(root, timeout) {
  const pending = [...root.querySelectorAll('img')].filter((img) => !img.complete);
  if (!pending.length) return Promise.resolve();
  return Promise.race([
    Promise.all(pending.map((img) => new Promise((resolve) => {
      img.addEventListener('load', resolve, { once: true });
      img.addEventListener('error', resolve, { once: true });
    }))),
    new Promise((resolve) => setTimeout(resolve, timeout)),
  ]);
}


/* One rail for the whole app. Every view addresses this instance, so the
 * width, the collapsed state and which sections are open are the same
 * wherever you are. */
let instance = null;
let preferredSource = 'npatlas';
let preferredSort = 'ppm';

export function detailsRail(options) {
  if (!instance) instance = createDetailsRail(options);
  return instance;
}
