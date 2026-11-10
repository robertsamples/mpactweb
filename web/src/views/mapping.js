/* Sample list + metadata editor.
 *
 * MPACT needs two small tables: the sample list (Injection -> Sample_Code,
 * which groups technical replicates) and the metadata (Sample_Code ->
 * Biological_Group, which groups biological replicates). Both are nearly
 * always implied by how the injections are named, so this builds them from
 * the names with a handful of rules -- guessed from the names, adjustable,
 * and overridable cell by cell -- and saves both files at once.
 *
 * Naming model: a name is split into tokens on delimiter characters. A
 * replicate marker is a token made of a prefix and a small number ("r2",
 * "S3", "rep1", or a bare "2"). Removing the technical-replicate token gives
 * the Sample_Code; removing the biological-replicate token from that gives
 * the Biological_Group. Names containing a blank term all go to "Blank".
 * Controls with no marker token (media_1, Media2) can be grouped by their
 * trailing number, but only where those numbers run 1..n, so an ID such as
 * CR13 is never mistaken for a replicate.
 *
 * With a peak table selected, the editor works on that peak table's
 * injections. A sample list that also covers other datasets -- a class's
 * composite sheet -- keeps its other rows exactly as they were when the
 * edits are saved; only repeated rows are merged, since a repeat is what
 * stops a run. */

import { api } from '../api.js';
import { store } from '../state.js';
import { clear, el, toast } from '../util.js';

const SEQUENCE_HEADERS = ['file name', 'filename', 'data file', 'datafile', 'raw file',
  'injection', 'sample name', 'name', 'sample id', 'sample'];
const RAW_EXTENSIONS = /\.(raw|d|wiff2?|mzml|mzxml|mzdata|lcd|baf|tdf|cdf)$/i;
const MARKER = /^([A-Za-z]{0,5})0*(\d{1,3})$/;

let dialog = null;

// --------------------------------------------------------------------------
// Name rules (pure functions)
// --------------------------------------------------------------------------

const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\/-]/g, '\\$&');

function tokenize(name, delims) {
  if (!delims) return [{ t: name, d: '' }];
  const parts = name.split(new RegExp(`([${escapeRe(delims)}]+)`));
  const tokens = [];
  for (let i = 0; i < parts.length; i += 2) {
    if (parts[i] !== '') tokens.push({ t: parts[i], d: i ? parts[i - 1] : '' });
  }
  return tokens.length ? tokens : [{ t: name, d: '' }];
}

const join = (tokens) => tokens.map((tok, i) => (i ? tok.d || '_' : '') + tok.t).join('');

const markerRe = (prefix) => new RegExp(`^${escapeRe(prefix)}0*(\\d{1,3})$`, 'i');

/** Index of the replicate token for `prefix`, searching from the end. A bare
 *  number ('' prefix) only counts as the last token: PK_1_S1 is strain PK_1. */
function findMarker(tokens, prefix) {
  if (tokens.length < 2) return -1;
  const re = markerRe(prefix);
  if (prefix === '') return re.test(tokens[tokens.length - 1].t) ? tokens.length - 1 : -1;
  for (let i = tokens.length - 1; i >= 0; i -= 1) if (re.test(tokens[i].t)) return i;
  return -1;
}

function sampleFor(name, rules) {
  if (!rules.techOn) return name;
  const tokens = tokenize(name, rules.delims);
  const index = findMarker(tokens, rules.tech);
  if (index < 0) return name;
  tokens.splice(index, 1);
  return join(tokens);
}

const blankTerms = (rules) => String(rules.blank || '').split(',')
  .map((term) => term.trim().toLowerCase()).filter(Boolean);

function groupParts(code, rules) {
  const lower = code.toLowerCase();
  if (blankTerms(rules).some((term) => lower.includes(term))) return { group: 'Blank' };
  const tokens = tokenize(code, rules.delims);
  let found = false;
  if (rules.bioOn) {
    const index = findMarker(tokens, rules.bio);
    if (index >= 0) { tokens.splice(index, 1); found = true; }
  }
  if (rules.dropShared && rules.shared && tokens.length > 1 && tokens[0].t === rules.shared) {
    tokens.shift();
  }
  let trailing = null;
  if (!found && rules.trailing) {
    const last = tokens[tokens.length - 1];
    const glued = /^(.*[A-Za-z])0*(\d{1,3})$/.exec(last.t);
    if (tokens.length > 1 && /^\d{1,3}$/.test(last.t)) {
      trailing = { stem: join(tokens.slice(0, -1)), n: Number(last.t) };
    } else if (glued) {
      trailing = { stem: join([...tokens.slice(0, -1), { ...last, t: glued[1] }]), n: Number(glued[2]) };
    }
  }
  return { group: join(tokens), trailing };
}

/** True when the numbers run 1..n (or 0..n-1) with n >= 2. */
function isRun(numbers) {
  const sorted = [...new Set(numbers)].sort((a, b) => a - b);
  if (sorted.length < 2 || sorted.length !== numbers.length || sorted[0] > 1) return false;
  return sorted.every((n, i) => n === sorted[0] + i);
}

function groupsFor(codes, rules) {
  const parts = new Map(codes.map((code) => [code, groupParts(code, rules)]));
  const stems = new Map();
  for (const part of parts.values()) {
    if (!part.trailing) continue;
    if (!stems.has(part.trailing.stem)) stems.set(part.trailing.stem, []);
    stems.get(part.trailing.stem).push(part.trailing.n);
  }
  const out = new Map();
  for (const [code, part] of parts) {
    const useStem = part.trailing && isRun(stems.get(part.trailing.stem));
    out.set(code, useStem ? part.trailing.stem : part.group);
  }
  return out;
}

/** Most common value of `key(item)`, keeping the first-seen spelling. */
function mostCommon(items, key) {
  const counts = new Map();
  for (const item of items) {
    const value = key(item);
    if (value === null || value === undefined) continue;
    const id = value.toLowerCase();
    const entry = counts.get(id) || { value, count: 0 };
    entry.count += 1;
    counts.set(id, entry);
  }
  return [...counts.values()].sort((a, b) => b.count - a.count)[0] || null;
}

function guessRules(names) {
  const rules = {
    delims: '_-', techOn: false, tech: 'r', bioOn: false, bio: 'S',
    trailing: true, blank: 'blank', shared: '', dropShared: false,
  };
  if (!names.length) return rules;

  const used = ['_', '-', ' '].filter((ch) => names.filter((n) => n.includes(ch)).length
                                              >= names.length * 0.3);
  if (used.length) rules.delims = used.join('');

  // Technical replicates: the commonest marker prefix on the last token.
  const tech = mostCommon(names, (name) => {
    const tokens = tokenize(name, rules.delims);
    if (tokens.length < 2) return null;
    const match = MARKER.exec(tokens[tokens.length - 1].t);
    return match ? match[1] : null;
  });
  if (tech && tech.count >= names.length * 0.25) {
    const trial = { ...rules, techOn: true, tech: tech.value };
    const codes = new Set(names.map((name) => sampleFor(name, trial)));
    if (codes.size < names.length * 0.9) Object.assign(rules, { techOn: true, tech: tech.value });
  }

  // Shared leading token (a run date, a project code).
  const firsts = names.map((name) => tokenize(name, rules.delims))
    .filter((tokens) => tokens.length > 1).map((tokens) => tokens[0].t);
  const shared = mostCommon(firsts, (value) => value);
  if (shared && names.length > 3 && shared.count >= names.length * 0.9) {
    // Case-sensitive match only, as groupParts compares exactly.
    rules.shared = shared.value;
    rules.dropShared = firsts.filter((value) => value === shared.value).length >= names.length * 0.9;
  }

  // Biological replicates: the lettered marker whose removal turns the most
  // samples into groups numbered 1..n.
  const codes = [...new Set(names.map((name) => sampleFor(name, rules)))]
    .filter((code) => !blankTerms(rules).some((term) => code.toLowerCase().includes(term)));
  const prefixes = new Map();
  for (const code of codes) {
    for (const tok of tokenize(code, rules.delims)) {
      const match = MARKER.exec(tok.t);
      if (match && match[1]) prefixes.set(match[1].toLowerCase(), match[1]);
    }
  }
  let best = null;
  for (const prefix of prefixes.values()) {
    const groups = new Map();
    let withToken = 0;
    for (const code of codes) {
      const tokens = tokenize(code, rules.delims);
      const index = findMarker(tokens, prefix);
      if (index < 0) continue;
      withToken += 1;
      const number = Number(markerRe(prefix).exec(tokens[index].t)[1]);
      tokens.splice(index, 1);
      const key = join(tokens);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(number);
    }
    let covered = 0;
    let runs = 0;
    for (const numbers of groups.values()) {
      if (isRun(numbers)) { covered += numbers.length; runs += 1; }
    }
    // Two markers can explain the same samples (MJS_C1_S1_...: two cultures
    // C1/C2, biological replicates S1-S3). Prefer the one leaving fewer,
    // larger groups: replicates are the bigger sets.
    if (covered >= 4 && covered >= withToken * 0.6
        && (!best || covered > best.covered || (covered === best.covered && runs < best.runs))) {
      best = { prefix, covered, runs };
    }
  }
  if (best) Object.assign(rules, { bioOn: true, bio: best.prefix });
  return rules;
}

// --------------------------------------------------------------------------
// Instrument sequence files
// --------------------------------------------------------------------------

function parseDelimited(text) {
  text = text.replace(/^﻿/, '');
  const head = text.split(/\r?\n/).slice(0, 30).join('\n');
  const delim = ['\t', ',', ';'].map((d) => [d, head.split(d).length])
    .sort((a, b) => b[1] - a[1])[0][0];
  const rows = [];
  let row = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i += 1; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"' && cell === '') quoted = true;
    else if (ch === delim) { row.push(cell); cell = ''; }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i += 1;
      row.push(cell); rows.push(row); row = []; cell = '';
    } else cell += ch;
  }
  if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

function sequenceHeader(rows) {
  for (let r = 0; r < Math.min(rows.length, 15); r += 1) {
    const cells = rows[r].map((cell) => cell.trim().toLowerCase());
    for (const known of SEQUENCE_HEADERS) {
      const col = cells.indexOf(known);
      if (col >= 0) return { row: r, col };
    }
  }
  return { row: 0, col: 0 };
}

function sequenceNames(sequence) {
  const names = [];
  for (const row of sequence.rows.slice(sequence.headerRow + 1)) {
    let name = String(row[sequence.col] || '').trim();
    if (!name) continue;
    name = name.split(/[\\/]/).pop().replace(RAW_EXTENSIONS, '');
    if (name) names.push(name);
  }
  return [...new Set(names)];
}

// --------------------------------------------------------------------------
// Dialog
// --------------------------------------------------------------------------

/** Rows by key (first occurrence wins), cells trimmed the way the run trims
 *  them, and how many later rows repeat a key already seen. */
function tableIndex(table, keyColumn) {
  const byKey = new Map();
  let repeats = 0;
  if (table.columns.indexOf(keyColumn) < 0) return { byKey, repeats };
  for (const row of table.rows) {
    const record = Object.fromEntries(table.columns.map((c, i) => [c, String(row[i] ?? '').trim()]));
    const key = record[keyColumn];
    if (!key) continue;
    if (byKey.has(key)) repeats += 1;
    else byKey.set(key, record);
  }
  return { byKey, repeats };
}

const stem = (name) => String(name || 'samples').replace(/\.[^.]+$/, '');

export async function openMappingEditor({ mode = 'new', onSaved } = {}) {
  if (!dialog) {
    dialog = el('dialog', { class: 'mapping' });
    dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
    document.body.append(dialog);
  }
  clear(dialog).append(el('div', { class: 'm-head' }, [
    el('span', { class: 'spinner' }), el('span', { class: 'muted', text: 'Loading…' }),
  ]));
  dialog.showModal();

  const inputs = store.get('inputs');
  const files = store.get('files');
  const peakRecord = files.find((f) => f.id === inputs.peaktable) || null;
  const state = {
    mode,
    source: null,          // 'peaktable' | 'samplelist' | 'sequence'
    names: [],
    peakNames: null,
    tables: null,          // { sample, meta, sampleRows, metaRows } when editing
    sequence: null,        // { name, rows, headerRow, col }
    rules: null,
    rulesOn: mode === 'new',
    overrides: { sample: new Map(), group: new Map() },
    groupFilter: null,
    textFilter: '',
    saving: false,
    error: null,
  };

  try {
    const [peak, sample, meta] = await Promise.all([
      peakRecord ? api.injections(peakRecord.id).catch(() => null) : null,
      mode === 'edit' ? api.readTable(inputs.samplelist) : null,
      mode === 'edit' ? api.readTable(inputs.metadata) : null,
    ]);
    if (peak) state.peakNames = peak.names;
    if (sample && meta) {
      const samples = tableIndex(sample, 'Injection');
      const metas = tableIndex(meta, 'Sample_Code');
      state.tables = {
        sample, meta, sampleRows: samples.byKey, metaRows: metas.byKey,
        repeats: samples.repeats + metas.repeats,
      };
    }
  } catch (error) {
    clear(dialog).append(el('div', { class: 'm-head' }, [
      el('div', { class: 'notice error', text: error.message }),
      el('button', { class: 'btn small', text: 'Close', on: { click: () => dialog.close() } }),
    ]));
    return;
  }

  const setSource = (source) => {
    state.source = source;
    if (source === 'peaktable') state.names = [...new Set(state.peakNames || [])];
    else if (source === 'samplelist') state.names = [...state.tables.sampleRows.keys()];
    else state.names = state.sequence ? sequenceNames(state.sequence) : [];
    if (!state.rules || state.rulesOn) state.rules = guessRules(state.names);
  };

  // The selected peak table's injections when there is one: rows a composite
  // sample list holds for other datasets are left alone.
  if (state.peakNames) setSource('peaktable');
  else if (state.tables) setSource('samplelist');
  else setSource('sequence');

  const sequenceInput = el('input', {
    type: 'file', accept: '.csv,.txt,.tsv', class: 'hidden',
    on: { change: async (event) => {
      const [file] = event.target.files;
      event.target.value = '';
      if (!file) return;
      const rows = parseDelimited(await file.text());
      const header = sequenceHeader(rows);
      state.sequence = { name: file.name, rows, headerRow: header.row, col: header.col };
      if (state.mode === 'new') state.rulesOn = true;
      state.rules = null;
      setSource('sequence');
      render();
    } },
  });

  // --- derived rows ----------------------------------------------------

  function computeRows() {
    const { rules, tables, overrides } = state;
    const samples = state.names.map((name) => {
      if (overrides.sample.has(name)) return overrides.sample.get(name);
      if (!state.rulesOn && tables && tables.sampleRows.has(name)) {
        return tables.sampleRows.get(name).Sample_Code || '';
      }
      return sampleFor(name, rules);
    });
    const codes = [...new Set(samples.filter(Boolean))];
    const ruled = groupsFor(codes, rules);
    const groupOf = (code) => {
      if (!code) return '';
      if (overrides.group.has(code)) return overrides.group.get(code);
      if (!state.rulesOn && tables && tables.metaRows.has(code)) {
        return tables.metaRows.get(code).Biological_Group || '';
      }
      return ruled.get(code) || '';
    };
    return state.names.map((name, i) => ({ name, sample: samples[i], group: groupOf(samples[i]) }));
  }

  // --- rendering -------------------------------------------------------

  let tableScroll = 0;

  function applyRules(patch) {
    Object.assign(state.rules, patch);
    state.rulesOn = true;
    render();
  }

  function rulesBar() {
    const r = state.rules;
    const ruleCheck = (key, label) => el('label', { class: 'check' }, [
      el('input', { type: 'checkbox', checked: r[key],
                    on: { change: (e) => applyRules({ [key]: e.target.checked }) } }),
      el('span', { text: label }),
    ]);
    const ruleText = (key, attrs = {}) => el('input', {
      type: 'text', value: r[key], class: 'short', ...attrs,
      on: { change: (e) => applyRules({ [key]: e.target.value }) },
    });

    return el('div', { class: 'm-rules' }, [
      el('button', {
        class: `btn small ${state.rulesOn ? '' : 'primary'}`, text: 'Guess',
        title: 'Detect replicate markers from the names',
        on: { click: () => { state.rules = guessRules(state.names); state.rulesOn = true; render(); } },
      }),
      el('label', { class: 'check' }, [el('span', { class: 'muted', text: 'Split on' }),
                                       ruleText('delims', { title: 'Delimiter characters' })]),
      el('span', { class: 'rule' }, [ruleCheck('techOn', 'Technical rep'),
                                     ruleText('tech', { title: 'Marker before the number, e.g. r for _r1' }),
                                     el('span', { class: 'muted', text: '#' })]),
      el('span', { class: 'rule' }, [ruleCheck('bioOn', 'Biological rep'),
                                     ruleText('bio', { title: 'Marker before the number, e.g. S for _S1' }),
                                     el('span', { class: 'muted', text: '#' })]),
      ruleCheck('trailing', 'Trailing number = rep'),
      el('label', { class: 'check' }, [el('span', { class: 'muted', text: 'Blank' }),
                                       ruleText('blank', { class: 'mid', title: 'Names containing any of these (comma-separated) go to Blank' })]),
      r.shared ? ruleCheck('dropShared', `Drop "${r.shared}"`) : null,
    ]);
  }

  /** One example name, its tokens coloured by role. Clicking a numbered
   *  token cycles it: technical marker -> biological marker -> neither. */
  function exampleBar(rows) {
    const r = state.rules;
    const name = state.names.find((n) => {
      const lower = n.toLowerCase();
      return !blankTerms(r).some((t) => lower.includes(t)) && tokenize(n, r.delims).length >= 3;
    }) || state.names[0];
    if (!name) return null;

    const tokens = tokenize(name, r.delims);
    const techAt = r.techOn ? findMarker(tokens, r.tech) : -1;
    const rest = tokens.filter((_, i) => i !== techAt);
    const bioRest = r.bioOn ? findMarker(rest, r.bio) : -1;
    const bioAt = bioRest >= 0 ? tokens.indexOf(rest[bioRest]) : -1;

    const chips = [];
    tokens.forEach((tok, i) => {
      if (i) chips.push(el('span', { class: 'sep', text: tok.d }));
      const match = MARKER.exec(tok.t);
      const role = i === techAt ? 'tech' : i === bioAt ? 'bio'
        : (i === 0 && r.dropShared && tok.t === r.shared) ? 'drop' : '';
      const click = () => {
        if (role === 'drop') return applyRules({ dropShared: false });
        if (!match) return null;
        if (role === 'tech') return applyRules({ techOn: false, bioOn: true, bio: match[1] });
        if (role === 'bio') return applyRules({ bioOn: false });
        return applyRules({ techOn: true, tech: match[1] });
      };
      chips.push(el('span', {
        class: `tok ${role} ${match || role === 'drop' ? 'click' : ''}`, text: tok.t,
        title: role ? { tech: 'technical replicate', bio: 'biological replicate', drop: 'dropped from groups' }[role]
                    : (match ? 'click to mark as a replicate number' : ''),
        on: { click },
      }));
    });
    const row = rows.find((x) => x.name === name);
    return el('div', { class: 'm-example' }, [
      el('span', { class: 'tokens' }, chips),
      el('span', { class: 'muted', text: '→' }),
      el('span', { class: 'mono', text: row ? row.sample : '' }),
      el('span', { class: 'muted', text: '→' }),
      el('span', { class: 'mono', text: row ? row.group : '' }),
      el('span', { class: 'legend' }, [
        el('span', { class: 'tok tech', text: 'technical' }),
        el('span', { class: 'tok bio', text: 'biological' }),
      ]),
    ]);
  }

  function sourceBar() {
    const options = [];
    if (state.tables) options.push(['samplelist', `Sample list (${state.tables.sampleRows.size})`]);
    if (state.peakNames) options.push(['peaktable', `Peak table (${state.peakNames.length})`]);
    options.push(['sequence', state.sequence ? `${state.sequence.name}` : 'Instrument sequence file…']);

    const pick = el('select', { on: { change: (e) => {
      if (e.target.value === 'sequence' && !state.sequence) {
        e.target.value = state.source;
        sequenceInput.click();
        return;
      }
      setSource(e.target.value);
      render();
    } } });
    for (const [value, label] of options) {
      pick.append(el('option', { value, text: label, selected: value === state.source }));
    }

    const columnPick = state.source === 'sequence' && state.sequence ? (() => {
      const header = state.sequence.rows[state.sequence.headerRow] || [];
      const node = el('select', { on: { change: (e) => {
        state.sequence.col = Number(e.target.value);
        setSource('sequence');
        render();
      } } });
      header.forEach((label, i) => {
        if (!String(label).trim()) return;
        node.append(el('option', { value: i, text: label, selected: i === state.sequence.col }));
      });
      return node;
    })() : null;

    return el('div', { class: 'm-head' }, [
      el('h3', { text: state.mode === 'edit' ? 'Edit sample list & metadata'
                                             : 'Create sample list & metadata' }),
      el('label', { class: 'check' }, [el('span', { class: 'muted', text: 'Injections from' }), pick]),
      columnPick ? el('label', { class: 'check' }, [el('span', { class: 'muted', text: 'Column' }), columnPick]) : null,
      state.source === 'sequence' && state.sequence
        ? el('button', { class: 'btn small', text: 'Other file…', on: { click: () => sequenceInput.click() } })
        : null,
      el('span', { class: 'spacer' }),
      el('button', { class: 'btn small', text: 'Close', on: { click: () => dialog.close() } }),
      sequenceInput,
    ]);
  }

  function groupList(rows) {
    const groups = new Map();
    for (const row of rows) {
      if (!row.group) continue;
      const entry = groups.get(row.group) || { samples: new Set(), injections: 0 };
      entry.samples.add(row.sample);
      entry.injections += 1;
      groups.set(row.group, entry);
    }
    const list = el('div', { class: 'm-groups' }, [
      el('div', { class: 'g head' }, [el('span', { text: 'Groups' }),
                                      el('span', { class: 'n', text: 'samples · inj' })]),
    ]);
    for (const [name, entry] of groups) {
      list.append(el('div', { class: `g ${state.groupFilter === name ? 'active' : ''}` }, [
        el('input', {
          type: 'text', value: name, title: 'Rename this group',
          on: { change: (e) => {
            const next = e.target.value.trim();
            if (!next) { e.target.value = name; return; }
            for (const code of entry.samples) state.overrides.group.set(code, next);
            if (state.groupFilter === name) state.groupFilter = next;
            render();
          } },
        }),
        el('span', {
          class: `n ${entry.samples.size < 2 ? 'single' : ''}`,
          text: `${entry.samples.size} · ${entry.injections}`,
          title: 'Show only this group',
          on: { click: () => { state.groupFilter = state.groupFilter === name ? null : name; render(); } },
        }),
      ]));
    }
    return list;
  }

  function table(rows) {
    const peak = state.peakNames ? new Set(state.peakNames) : null;
    const text = state.textFilter.toLowerCase();
    const body = el('tbody');
    for (const row of rows) {
      if (state.groupFilter && row.group !== state.groupFilter) continue;
      if (text && !`${row.name} ${row.sample} ${row.group}`.toLowerCase().includes(text)) continue;
      const missing = peak && !peak.has(row.name);
      body.append(el('tr', { class: missing ? 'missing' : '' }, [
        el('td', { class: 'inj', text: row.name, title: missing ? 'not in the peak table' : '' }),
        el('td', {}, [el('input', {
          type: 'text', value: row.sample, placeholder: 'required',
          on: { change: (e) => { state.overrides.sample.set(row.name, e.target.value.trim()); render(); } },
        })]),
        el('td', {}, [el('input', {
          type: 'text', value: row.group, placeholder: 'required', disabled: !row.sample,
          on: { change: (e) => {
            state.overrides.group.set(row.sample, e.target.value.trim());
            render();
          } },
        })]),
      ]));
    }
    const wrap = el('div', { class: 'm-table' }, [el('table', {}, [
      el('thead', {}, [el('tr', {}, [
        el('th', { text: 'Injection' }), el('th', { text: 'Sample_Code' }),
        el('th', { text: 'Biological_Group' }),
      ])]),
      body,
    ])]);
    wrap.addEventListener('scroll', () => { tableScroll = wrap.scrollTop; });
    return wrap;
  }

  function statusBar(rows) {
    const samples = new Set(rows.map((r) => r.sample).filter(Boolean));
    const groups = new Set(rows.map((r) => r.group).filter(Boolean));
    const parts = [`${rows.length} injections`, `${samples.size} samples`, `${groups.size} groups`];
    const notes = [];
    if (state.peakNames) {
      const names = new Set(state.names);
      const peak = new Set(state.peakNames);
      const absent = state.names.filter((n) => !peak.has(n)).length;
      const unmapped = state.peakNames.filter((n) => !names.has(n)).length;
      if (absent) notes.push(el('span', { class: 'warn', text: `${absent} not in peak table` }));
      if (unmapped) notes.push(el('span', { class: 'warn', text: `${unmapped} peak-table injections not listed` }));
      if (!absent && !unmapped) notes.push(el('span', { class: 'okay', text: 'matches peak table' }));
    }
    if (state.tables) {
      const shown = new Set(state.names);
      const others = [...state.tables.sampleRows.keys()].filter((n) => !shown.has(n)).length;
      const added = state.names.filter((n) => !state.tables.sampleRows.has(n)).length;
      if (added && state.source !== 'samplelist') {
        notes.push(el('span', { class: 'warn', text: `${added} not in the sample list yet` }));
      }
      if (others) {
        notes.push(el('span', { class: 'muted', text: `${others} other injection${others === 1 ? '' : 's'} `
                                                      + 'in the list kept as they are' }));
      }
      const repeats = state.tables.repeats;
      if (repeats) {
        notes.push(el('span', { class: 'muted', text: `${repeats} repeated row${repeats === 1 ? '' : 's'} `
                                                      + 'will be merged' }));
      }
    }
    const empty = rows.filter((r) => !r.sample || !r.group).length;
    if (empty) notes.push(el('span', { class: 'bad', text: `${empty} incomplete` }));

    const edits = state.overrides.sample.size + state.overrides.group.size;
    const filter = el('input', {
      type: 'search', placeholder: 'Filter', value: state.textFilter,
      on: { input: (e) => {
        state.textFilter = e.target.value;
        const caret = e.target.selectionStart;
        render();
        const again = dialog.querySelector('.m-status input[type="search"]');
        if (again) { again.focus(); again.setSelectionRange(caret, caret); }
      } },
    });
    return el('div', { class: 'm-status' }, [
      filter,
      state.groupFilter ? el('span', { class: 'pill click', text: `${state.groupFilter} ×`,
        on: { click: () => { state.groupFilter = null; render(); } } }) : null,
      el('span', { class: 'stats', text: parts.join(' · ') }),
      ...notes,
      el('span', { class: 'spacer' }),
      edits ? el('button', {
        class: 'btn small', text: `Reset ${edits} edit${edits === 1 ? '' : 's'}`,
        on: { click: () => { state.overrides.sample.clear(); state.overrides.group.clear(); render(); } },
      }) : null,
    ]);
  }

  function footer(rows) {
    const incomplete = rows.some((r) => !r.sample || !r.group);
    return el('div', { class: 'm-foot' }, [
      state.error ? el('div', { class: 'notice error', text: state.error }) : null,
      el('span', { class: 'spacer' }),
      el('button', { class: 'btn', text: 'Cancel', on: { click: () => dialog.close() } }),
      el('button', {
        class: 'btn primary', text: state.saving ? 'Saving…' : 'Save',
        disabled: state.saving || !rows.length || incomplete,
        on: { click: () => save(rows) },
      }),
    ]);
  }

  function render() {
    const rows = state.names.length ? computeRows() : [];
    const parts = [sourceBar()];
    if (!state.names.length) {
      parts.push(el('div', { class: 'm-empty' }, [
        el('button', { class: 'btn primary', text: 'Choose instrument sequence file…',
                       on: { click: () => sequenceInput.click() } }),
        el('span', { class: 'muted', text: 'or select a peak table first' }),
      ]));
    } else {
      parts.push(rulesBar(), exampleBar(rows), statusBar(rows),
                 el('div', { class: 'm-body' }, [table(rows), groupList(rows)]),
                 footer(rows));
    }
    clear(dialog).append(...parts.filter(Boolean));
    const scroller = dialog.querySelector('.m-table');
    if (scroller) scroller.scrollTop = tableScroll;
  }

  // --- saving ----------------------------------------------------------

  async function save(rows) {
    state.saving = true;
    state.error = null;
    render();

    const ensure = (columns, required) => [...required, ...columns.filter((c) => !required.includes(c))];
    const t = state.tables;
    const sampleColumns = ensure(t ? t.sample.columns : [], ['Injection', 'Sample_Code']);
    const metaColumns = ensure(t ? t.meta.columns : [], ['Sample_Code', 'Biological_Group']);

    const editedSample = (row) => {
      const extra = (t && t.sampleRows.get(row.name)) || {};
      return sampleColumns.map((c) => (c === 'Injection' ? row.name
        : c === 'Sample_Code' ? row.sample : String(extra[c] ?? '')));
    };
    const groupOf = new Map();
    for (const row of rows) if (!groupOf.has(row.sample)) groupOf.set(row.sample, row.group);
    const editedMeta = (code, group) => {
      const extra = (t && t.metaRows.get(code)) || {};
      return metaColumns.map((c) => (c === 'Sample_Code' ? code
        : c === 'Biological_Group' ? group : String(extra[c] ?? '')));
    };

    let sampleRows;
    let metaRows;
    if (state.mode === 'edit' && t) {
      ({ sampleRows, metaRows } = mergeTables(t, rows, groupOf, sampleColumns,
                                               editedSample, editedMeta));
    } else {
      sampleRows = rows.map(editedSample);
      metaRows = [...groupOf].map(([code, group]) => editedMeta(code, group));
    }

    try {
      let saved;
      if (state.mode === 'edit') {
        saved = [
          await api.replaceTable(inputs.samplelist, { kind: 'samplelist', columns: sampleColumns, rows: sampleRows }),
          await api.replaceTable(inputs.metadata, { kind: 'metadata', columns: metaColumns, rows: metaRows }),
        ];
      } else {
        const base = stem(state.source === 'sequence' && state.sequence ? state.sequence.name
                                                                         : peakRecord && peakRecord.name);
        const sl = await api.createTable({ kind: 'samplelist', name: `${base}_samplelist.csv`,
                                           columns: sampleColumns, rows: sampleRows });
        const md = await api.createTable({ kind: 'metadata', name: `${base}_metadata.csv`,
                                           columns: metaColumns, rows: metaRows });
        saved = [sl, md];
      }
      dialog.close();
      toast('Sample list and metadata saved', 'ok');
      if (onSaved) onSaved(saved);
    } catch (error) {
      state.saving = false;
      state.error = error.message;
      render();
    }
  }

  render();
}

/** The edited rows merged back into the full sample list and metadata.
 *
 * Rows the editor showed are replaced by their edited versions and new ones
 * are added; every other row is kept as it was, in its place. A repeated key
 * keeps its first row (a repeat is what stops a run); a row missing its key
 * or its value is of no use to any analysis and is dropped; and a metadata
 * row is dropped only when this edit moved every injection off it. */
function mergeTables(t, rows, groupOf, sampleColumns, editedSample, editedMeta) {
  const shown = new Map(rows.map((row) => [row.name, row]));
  const record = (table, row) => Object.fromEntries(table.columns.map((c, i) => [c, row[i] ?? '']));

  const sampleRows = [];
  const seen = new Set();
  for (const row of t.sample.rows) {
    const values = record(t.sample, row);
    const key = String(values.Injection || '').trim();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    if (shown.has(key)) sampleRows.push(editedSample(shown.get(key)));
    else if (String(values.Sample_Code || '').trim()) {
      sampleRows.push(editedSample({ name: key, sample: String(values.Sample_Code).trim() }));
    }
  }
  for (const row of rows) {
    if (!seen.has(row.name)) { seen.add(row.name); sampleRows.push(editedSample(row)); }
  }

  const codeAt = sampleColumns.indexOf('Sample_Code');
  const used = new Set(sampleRows.map((row) => row[codeAt]));
  const before = new Set(rows.map((row) => (t.sampleRows.get(row.name) || {}).Sample_Code)
    .filter(Boolean).map((code) => String(code).trim()));
  const metaRows = [];
  const codes = new Set();
  for (const row of t.meta.rows) {
    const values = record(t.meta, row);
    const code = String(values.Sample_Code || '').trim();
    if (!code || codes.has(code) || (before.has(code) && !used.has(code))) continue;
    codes.add(code);
    if (groupOf.has(code)) metaRows.push(editedMeta(code, groupOf.get(code)));
    else if (String(values.Biological_Group || '').trim()) {
      metaRows.push(editedMeta(code, String(values.Biological_Group).trim()));
    }
  }
  for (const [code, group] of groupOf) {
    if (!codes.has(code)) { codes.add(code); metaRows.push(editedMeta(code, group)); }
  }
  return { sampleRows, metaRows };
}

// Exposed for tests and the console.
export const _rules = { tokenize, sampleFor, groupsFor, guessRules, parseDelimited,
                        sequenceHeader, sequenceNames, mergeTables, tableIndex };
