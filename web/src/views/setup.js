/* Setup views: File Selection, Filtering Settings, Analysis Settings, Run.
 *
 * These are the desktop's first three tabs plus its Run button, in the same
 * order with the same controls and the same defaults. Two things the desktop
 * can't do are added, because the web changes the constraints rather than the
 * workflow: uploads persist in a per-session library so a re-run never
 * re-uploads, and the pipeline's console output streams into the Run view
 * instead of a terminal the user may not have. */

import { registerView, selectRun, watchRun } from '../app.js';
import { openMappingEditor } from './mapping.js';
import { attachDino, dinoPressed } from './dino.js';
import { api } from '../api.js';
import { store } from '../state.js';
import { collapsible } from '../components.js';
import { taxonPicker } from './taxonpicker.js';
import { $, bytes, checkbox, clear, debounce, el, field, numberInput, select, timeAgo, toast } from '../util.js';

const SLOTS = [
  { kind: 'peaktable', label: 'Peak Table',
    hint: 'Progenesis QI, MZmine, MS-DIAL or Metaboscape', accept: '.csv,.txt', required: true },
  { kind: 'samplelist', label: 'Sample List',
    hint: 'Injection -> Sample_Code', accept: '.csv', required: true },
  { kind: 'metadata', label: 'Sample Metadata',
    hint: 'Sample_Code -> Biological_Group', accept: '.csv', required: true },
  { kind: 'fragments', label: 'Fragment Database',
    hint: 'MSP or MGF (optional)', accept: '.msp,.mgf', required: false },
];

let refreshFiles = () => {};
let refreshFiltering = () => {};
let refreshAnalysis = () => {};
let refreshRun = () => {};

const settings = () => store.get('settings') || {};

/** The database-search taxon, from the web field or the desktop's pair. */
function savedTaxon(s) {
  if (s.taxon) return { level: s.taxon_level || 'genus', value: s.taxon };
  if (s.genus) return { level: 'genus', value: s.genus };
  if (s.kingdom) return { level: 'kingdom', value: s.kingdom };
  return { level: 'genus', value: '' };
}

function updateSetting(patch) {
  store.set({ settings: { ...settings(), ...patch } });
  store.persistSetup();
}

// --------------------------------------------------------------------------
// File Selection
// --------------------------------------------------------------------------

function slotDetail(record) {
  if (!record) return null;
  const detail = record.detail || {};
  if (detail.error) return el('div', { class: 'meta' }, [el('span', { class: 'bad', text: detail.error })]);

  const parts = [];
  if (record.kind === 'peaktable') {
    parts.push(`format: ${detail.format || 'unknown'}`);
    if (detail.features !== undefined) parts.push(`${detail.features.toLocaleString()} features`);
    if (detail.injections !== undefined) parts.push(`${detail.injections} injections`);
  } else if (record.kind === 'fragments') {
    parts.push(`${(detail.entries || 0).toLocaleString()} spectra`);
  } else {
    if (detail.rows !== undefined) parts.push(`${detail.rows} rows`);
    if (detail.columns) parts.push(`columns: ${detail.columns.slice(0, 4).join(', ')}`);
  }
  parts.push(bytes(record.size));

  const node = el('div', { class: 'meta', text: parts.join(' · ') });
  if (detail.missing_columns && detail.missing_columns.length) {
    node.append(el('span', { class: 'bad',
                             text: `  missing required column(s): ${detail.missing_columns.join(', ')}` }));
  }
  if (detail.format === 'unknown') {
    node.append(el('span', { class: 'bad', text: '  unrecognised peak-table format' }));
  }
  return node;
}

function buildSlot(slot) {
  const inputs = store.get('inputs');
  const files = store.get('files').filter((file) => file.kind === slot.kind);
  const selectedId = inputs[slot.kind];
  const record = files.find((file) => file.id === selectedId) || null;

  const fileInput = el('input', {
    type: 'file', accept: slot.accept, class: 'hidden',
    on: { change: (event) => {
      const [file] = event.target.files;
      if (file) upload(slot.kind, file);
      event.target.value = '';
    } },
  });

  const value = el('div', { class: 'slot-value' });
  if (files.length) {
    value.append(select(
      [['', record ? 'None selected' : 'Select an uploaded file…'],
       ...files.map((file) => [file.id, `${file.name} · ${timeAgo(file.uploaded_at)}`])],
      selectedId || '',
      (id) => {
        store.set({ inputs: { ...store.get('inputs'), [slot.kind]: id || null } });
        store.persistSetup();
        refreshFiles();
        deriveGroups();
      }));
    value.append(slotDetail(record));
  } else {
    value.append(el('div', { class: 'filename empty', text: 'Nothing uploaded yet' }));
  }

  const isTable = slot.kind === 'samplelist' || slot.kind === 'metadata';
  const bothTables = inputs.samplelist && inputs.metadata;

  const node = el('div', { class: 'slot' }, [
    el('div', { class: 'slot-name' }, [
      el('span', { text: slot.label }),
      el('small', { text: slot.hint }),
    ]),
    value,
    el('div', { class: 'btn-row' }, [
      el('button', { class: 'btn small', text: 'Upload…',
                     on: { click: () => fileInput.click() } }),
      isTable ? el('button', {
        class: 'btn small', text: 'Create…',
        title: 'Build a sample list and metadata from injection names',
        on: { click: () => openMappingEditor({ mode: 'new', onSaved: tablesSaved }) },
      }) : null,
      isTable && record ? el('button', {
        class: 'btn small', text: 'Edit',
        disabled: !bothTables,
        title: bothTables ? 'Edit the replicate mapping'
                          : 'Select a sample list and a metadata file to edit them',
        on: { click: () => openMappingEditor({ mode: 'edit', onSaved: tablesSaved }) },
      }) : null,
      isTable && record ? el('a', {
        class: 'btn small', text: 'Download', href: api.fileDownloadUrl(record.id),
        download: record.name,
      }) : null,
      record ? el('button', {
        class: 'btn small danger', text: 'Remove', title: 'Delete from the library',
        on: { click: () => removeFile(record.id) },
      }) : null,
    ]),
    fileInput,
  ]);

  // Drag and drop onto a slot, matching how a desktop user would expect to
  // hand a file to a labelled target.
  node.addEventListener('dragover', (event) => {
    event.preventDefault();
    node.classList.add('dragover');
  });
  node.addEventListener('dragleave', () => node.classList.remove('dragover'));
  node.addEventListener('drop', (event) => {
    event.preventDefault();
    node.classList.remove('dragover');
    const [file] = event.dataTransfer.files;
    if (file) upload(slot.kind, file);
  });
  return node;
}

/** The mapping editor saved a sample list and/or metadata file: put them in
 *  the library, select them, and re-derive the groups. */
function tablesSaved(records) {
  let files = store.get('files');
  const inputs = { ...store.get('inputs') };
  for (const record of records) {
    files = [record, ...files.filter((f) => f.id !== record.id)];
    inputs[record.kind] = record.id;
  }
  store.set({ files, inputs });
  store.persistSetup();
  refreshFiles();
  deriveGroups();
}

async function upload(kind, file) {
  const progress = el('div', { class: 'notice info' }, [
    el('div', { text: `Uploading ${file.name}…` }),
    el('div', { class: 'progress' }, [el('div')]),
  ]);
  $('#view-files .upload-status').replaceChildren(progress);
  try {
    const record = await api.uploadFile(kind, file);
    store.set({
      files: [record, ...store.get('files').filter((f) => f.id !== record.id)],
      inputs: { ...store.get('inputs'), [kind]: record.id },
    });
    store.persistSetup();
    $('#view-files .upload-status').replaceChildren();
    refreshFiles();
    deriveGroups();
  } catch (error) {
    $('#view-files .upload-status').replaceChildren(
      el('div', { class: 'notice error', text: error.message }));
  }
}

async function removeFile(id) {
  try {
    await api.deleteFile(id);
  } catch (error) {
    toast(error.message, 'error');
    return;
  }
  const inputs = { ...store.get('inputs') };
  for (const key of Object.keys(inputs)) if (inputs[key] === id) inputs[key] = null;
  store.set({ files: store.get('files').filter((file) => file.id !== id), inputs });
  store.persistSetup();
  refreshFiles();
  deriveGroups();
}

/** Ask the server for the biological groups, once all three required files
 *  are chosen -- the moment `MainWindow.getgroups` runs on the desktop. */
const deriveGroups = debounce(async () => {
  const inputs = store.get('inputs');
  if (!(inputs.peaktable && inputs.samplelist && inputs.metadata)) {
    store.set({ groups: [], groupError: null, groupWarnings: [], groupUnresolved: null });
    refreshFiles();
    refreshRun();
    return;
  }
  store.set({ groupError: null });
  try {
    const body = await api.groupsFor({
      peaktable: inputs.peaktable,
      samplelist: inputs.samplelist,
      metadata: inputs.metadata,
    });
    store.set({ groups: body.groups, groupWarnings: body.warnings || [], groupError: null,
                groupUnresolved: body.unresolved_total
                  ? { total: body.unresolved_total, names: body.unresolved || [] } : null });
    store.reconcileGroupsets(body.groups);

    // Seed the group pickers the way the desktop seeds its combo boxes, but
    // only where the user hasn't already chosen something valid.
    const current = settings();
    const patch = {};
    const valid = (name) => name && body.groups.includes(name);
    if (!valid(current.blnkgrp)) patch.blnkgrp = body.suggested.blank || '';
    if (!valid(current.expgrp)) patch.expgrp = body.suggested.experimental || '';
    if (!valid(current.ctrgrp)) patch.ctrgrp = body.suggested.control || '';
    if (patch.blnkgrp === '' && current.blnkfltr) patch.blnkfltr = false;
    if (Object.keys(patch).length) updateSetting(patch);
  } catch (error) {
    store.set({ groups: [], groupError: error.message, groupWarnings: [], groupUnresolved: null });
  }
  refreshFiles();
  refreshFiltering();
  refreshAnalysis();
  refreshRun();
}, 200);

/** Open a saved ``.mpct``: the desktop's File > Open.
 *
 * A save file carries its own copies of all four inputs alongside every
 * parameter, so this restores a whole analysis -- files, filters, statistics
 * groups and Plot Feature Sets -- in one step, and it can then be edited and
 * re-run like anything else rather than only replayed. */
function sessionPanel() {
  const status = el('div', { class: 'session-status' });

  const input = el('input', {
    type: 'file', accept: '.mpct,.mpact', style: { display: 'none' },
    on: {
      change: (event) => {
        const file = event.target.files[0];
        event.target.value = '';
        if (file) openSession(file);
      },
    },
  });

  const button = el('button', {
    class: 'btn', text: 'Open session file...',
    on: { click: () => input.click() },
  });

  async function openSession(file) {
    button.disabled = true;
    clear(status).append(el('p', { class: 'muted', text: `Reading ${file.name}...` }));
    try {
      const body = await api.importSession(file);

      // Library: the imported records first, then whatever was already there.
      const merged = [...body.files];
      for (const existing of store.get('files')) {
        if (!body.files.some((record) => record.id === existing.id)) merged.push(existing);
      }

      // The session defines the whole input set, so slots it does not name
      // are cleared rather than left pointing at the previous dataset.
      const inputs = { peaktable: null, samplelist: null, metadata: null,
                       fragments: null, ...body.inputs };

      // Feature sets live beside the settings in the store; the run request
      // puts them back together.
      const { groupsets = [], ...restored } = body.settings;

      store.set({ files: merged, inputs, settings: restored, groupsets,
                  selectedGroupset: groupsets.length ? 0 : -1 });
      store.persistSetup();

      refreshFiles();
      refreshFiltering();
      refreshAnalysis();
      refreshRun();
      deriveGroups();

      clear(status);
      for (const warning of body.warnings || []) {
        status.append(el('div', { class: 'notice warn', text: warning }));
      }
      toast(`Opened ${body.name}`, 'ok');
    } catch (error) {
      clear(status).append(el('div', { class: 'notice error', text: error.message }));
    } finally {
      button.disabled = false;
    }
  }

  const panel = el('div', { class: 'panel' }, [
    el('div', { class: 'btn-row', style: { gap: '12px', alignItems: 'baseline' } }, [
      button,
      el('span', { class: 'muted',
                   text: 'Restores the files, filters and settings from a saved '
                         + 'MPACT session (.mpct), from this app or the desktop one.' }),
      input,
    ]),
    status,
  ]);

  // Dropping the file on the panel does the same, the way the slots below
  // accept a dragged file.
  panel.addEventListener('dragover', (event) => {
    event.preventDefault();
    panel.classList.add('dragover');
  });
  panel.addEventListener('dragleave', () => panel.classList.remove('dragover'));
  panel.addEventListener('drop', (event) => {
    event.preventDefault();
    panel.classList.remove('dragover');
    const file = event.dataTransfer.files[0];
    if (file) openSession(file);
  });

  return panel;
}

/** Do the selected sample list and metadata say anything about this peak
 *  table's injections? Used when a demo without its own sheets is loaded:
 *  a class's composite sheets stay selected, another dataset's are cleared. */
async function tablesFit(inputs) {
  if (!inputs.peaktable || !inputs.samplelist) return false;
  try {
    const [peak, table] = await Promise.all([api.injections(inputs.peaktable),
                                             api.readTable(inputs.samplelist)]);
    const at = table.columns.indexOf('Injection');
    if (at < 0) return false;
    const names = new Set(peak.names);
    return table.rows.some((row) => names.has(String(row[at] || '').trim()));
  } catch {
    return false;
  }
}

/** "Try it with an example": the bundled datasets -- PTY087I2 with all four
 *  files, and the CHM350 class alignments with a peak table and spectra. */
let demoChoice = null;

function demoPanel() {
  const info = store.get('demo');
  const datasets = ((info && info.datasets) || []).filter((d) => d.available);
  if (!datasets.length) return null;
  const inputs = store.get('inputs');
  const current = store.get('files').find((file) => file.id === inputs.peaktable);
  // The one picked here, else the one already in the peak-table slot.
  const chosen = datasets.find((d) => d.id === demoChoice)
    || datasets.find((d) => current && d.peaktable === current.name)
    || datasets.find((d) => d.id === info.default) || datasets[0];

  const alreadyLoaded = Boolean(current && chosen.peaktable && current.name === chosen.peaktable);
  const fullSet = chosen.kinds.includes('samplelist') && chosen.kinds.includes('metadata');

  const button = el('button', {
    class: 'btn primary', text: alreadyLoaded ? 'Loaded' : 'Load',
    disabled: alreadyLoaded,
  });
  button.addEventListener('click', async () => {
    button.disabled = true;
    button.textContent = 'Loading...';
    try {
      const records = await api.loadDemo(chosen.id);
      const merged = [...records];
      for (const file of store.get('files')) {
        if (!records.some((r) => r.id === file.id)) merged.push(file);
      }
      const picked = { ...store.get('inputs') };
      for (const record of records) picked[record.kind] = record.id;
      // Nothing from the previous dataset is left in a slot this one doesn't
      // fill -- except sheets that also cover this peak table (a class's
      // composite sample list and metadata).
      if (!records.some((r) => r.kind === 'fragments')) picked.fragments = null;
      if (!fullSet && !(await tablesFit(picked))) {
        picked.samplelist = null;
        picked.metadata = null;
      }
      store.set({ files: merged, inputs: picked });
      // A dataset can come set up: the groups to compare, blank filtering,
      // and its Plot Feature Sets (the CHM350 ones do).
      if (chosen.preset) {
        const { settings: patch = {}, groupsets = null } = chosen.preset;
        store.set({ settings: { ...settings(), ...patch } });
        if (groupsets) store.set({ groupsets, selectedGroupset: groupsets.length ? 0 : -1 });
      }
      store.persistSetup();
      refreshFiles();
      refreshFiltering();
      refreshAnalysis();
      deriveGroups();
      toast(chosen.preset
        ? `${chosen.name} loaded and set up - go to Run, or change the settings first`
        : fullSet
        ? `${chosen.name} loaded - go to Run, or change the settings first`
        : picked.samplelist
          ? `${chosen.name} loaded with the selected sample list and metadata`
          : `${chosen.name} loaded - now create or select a sample list and metadata`, 'ok');
    } catch (error) {
      toast(error.message, 'error');
      button.disabled = false;
      button.textContent = 'Load';
    }
  });

  const picker = select(
    datasets.map((d) => [d.id, d.description ? `${d.name} · ${d.description}` : d.name]),
    chosen.id,
    (id) => { demoChoice = id; refreshFiles(); });

  // Collapsed by default: one line and a button is all most visitors need,
  // and the description shouldn't push the real file pickers down the page.
  return el('div', { class: 'panel' }, [
    el('div', { class: 'btn-row', style: { gap: '12px' } }, [
      el('label', { class: 'check' }, [el('span', { class: 'muted', text: 'Demo dataset' }), picker]),
      button,
      collapsible({
        title: 'What is in it?',
        open: false,
        body: el('div', {}, [
          el('p', { class: 'muted', style: { maxWidth: '70ch', margin: '0' },
                    text: chosen.summary || '' }),
          chosen.preset ? null : el('p', { class: 'muted', style: { margin: '6px 0 0' },
                    text: fullSet ? 'Fills all four slots; you can still change any of them.'
                                  : 'Fills the peak table and fragment database slots.' }),
        ]),
      }),
    ]),
  ]);
}

function filesView() {
  const node = el('div');

  refreshFiles = () => {
    clear(node);
    node.append(el('h2', { text: 'File Selection' }));
    node.append(el('p', { class: 'lede' },
      ['Upload a peak table, sample list and metadata file. Uploads stay in this '
       + 'session\'s library, so you can re-select them for another analysis without '
       + 'uploading again.']));

    node.append(el('div', { class: 'upload-status' }));
    node.append(demoPanel());
    node.append(sessionPanel());

    const panel = el('div', { class: 'panel' }, [
      el('h3', {}, [el('span', { text: 'Inputs' }),
                    el('span', { class: 'hint', text: 'peak table, sample list and metadata are required' })]),
    ]);
    for (const slot of SLOTS) panel.append(buildSlot(slot));
    node.append(panel);

    const groups = store.get('groups');
    const groupError = store.get('groupError');
    const warnings = store.get('groupWarnings') || [];
    const inputs = store.get('inputs');
    const ready = inputs.peaktable && inputs.samplelist && inputs.metadata;

    const groupPanel = el('div', { class: 'panel' }, [
      el('h3', {}, [el('span', { text: 'Biological groups' }),
                    el('span', { class: 'hint', text: 'derived from your metadata' })]),
    ]);
    if (!ready) {
      groupPanel.append(el('p', { class: 'muted',
                                  text: 'Select all three required files to detect groups.' }));
    } else if (groupError) {
      groupPanel.append(el('div', { class: 'notice error', text: groupError }));
      groupPanel.append(el('p', { class: 'muted' }, [
        'The sample list needs ', el('code', { text: 'Injection' }), ' and ',
        el('code', { text: 'Sample_Code' }), ' columns; the metadata file needs ',
        el('code', { text: 'Sample_Code' }), ' and ', el('code', { text: 'Biological_Group' }), '.',
      ]));
    } else if (!groups.length) {
      groupPanel.append(el('div', { class: 'notice warn', text: 'No biological groups were found.' }));
    } else {
      for (const warning of warnings) {
        groupPanel.append(el('div', { class: 'notice warn', text: warning }));
      }
      groupPanel.append(el('div', { class: 'btn-row' },
        groups.map((group) => el('span', { class: 'pill', text: group }))));
    }
    node.append(groupPanel);
  };

  refreshFiles();
  return { id: 'files', label: 'File Selection', group: 'Setup', node,
           onShow: refreshFiles };
}

// --------------------------------------------------------------------------
// Filtering Settings
// --------------------------------------------------------------------------

const betaTag = () => el('span', { class: 'beta-tag', text: 'beta' });

/** The switch at the top of a section that hands it to a beta filter. While
 *  it is on, the beta filter runs in place of the section's standard one;
 *  the standard settings stay in the form, so switching back restores them. */
function betaSwitch(label, on, onChange, title) {
  const node = checkbox(label, on, onChange);
  node.classList.add('beta-switch');
  node.title = title;
  node.append(betaTag());
  return node;
}

/** The ion forms the current mode offers, from the server's table. */
function modeForms() {
  const table = store.get('ions') || {};
  return table[settings().ionmode || 'pos'] || { forms: [], base: '', name: '' };
}

/** A checklist of the current mode's ion forms, kept in `settings[key]` as
 *  one list of labels across both modes: a label belongs to one mode, so
 *  switching mode and back leaves each mode's ticks as they were. */
function ionFormList(key, { withBase, onChange }) {
  const mode = modeForms();
  const chosen = new Set(settings()[key] || []);
  const rows = mode.forms.filter((form) => withBase || form.kind !== 'base').map((form) => {
    const input = el('input', {
      type: 'checkbox', checked: chosen.has(form.label),
      on: { change: (event) => {
        const next = new Set(settings()[key] || []);
        if (event.target.checked) next.add(form.label); else next.delete(form.label);
        updateSetting({ [key]: [...next] });
        if (onChange) onChange();
      } },
    });
    // What the number means differs: a mass difference from the base ion for
    // the adduct filter, the mass added to the neutral molecule for a search.
    const value = withBase ? form.added : form.delta;
    return el('label', { class: 'check ion-form' }, [
      input,
      el('span', { class: 'ion-label', text: form.label }),
      el('span', { class: 'ion-delta', text: `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(4)}` }),
    ]);
  });
  return el('div', { class: 'ion-forms' }, rows);
}

/** What the selected peak table or fragment file says its polarity is. */
function detectedIonMode() {
  const inputs = store.get('inputs');
  for (const kind of ['fragments', 'peaktable']) {
    const record = store.get('files').find((file) => file.id === inputs[kind]);
    const mode = record && record.detail && record.detail.ionmode;
    if (mode) return { mode, file: record.name };
  }
  return null;
}

function ionModePanel() {
  const s = settings();
  const setMode = (mode) => {
    updateSetting({ ionmode: mode });
    refreshFiltering(); refreshAnalysis(); refreshRun();
  };
  const radio = (mode, label) => el('label', {}, [
    el('input', { type: 'radio', name: 'ionmode', checked: (s.ionmode || 'pos') === mode,
                  on: { change: () => setMode(mode) } }),
    el('span', { text: label }),
  ]);
  const panel = el('div', { class: 'panel ion-mode' }, [
    el('h3', {}, [el('span', { text: 'Ion mode' }),
                  el('span', { class: 'hint',
                               text: 'used by the adduct filter and the database search' })]),
    el('div', { class: 'radio-row' }, [radio('pos', 'Positive'), radio('neg', 'Negative'), ]),
  ]);
  if (s.ionmode === 'neg') {
    panel.append(el('p', { class: 'muted' }, [
      betaTag(), ' Negative mode: features are matched to the database as the ion forms chosen '
      + 'in Analysis Settings, and the adduct filter measures from [M−H]−. The MS/MS library '
      + 'holds positive-mode spectra only, so the MS/MS search is not available.']));
  }
  const detected = detectedIonMode();
  if (detected && detected.mode !== (s.ionmode || 'pos')) {
    const name = detected.mode === 'neg' ? 'negative' : 'positive';
    panel.append(el('div', { class: 'notice warn' }, [
      el('span', { text: `${detected.file} looks like ${name}-mode data. ` }),
      el('button', { class: 'btn small', text: `Switch to ${name}`,
                     on: { click: () => setMode(detected.mode) } }),
    ]));
  }
  return panel;
}

function mispickedPanel() {
  const s = settings();
  const rerender = () => refreshFiltering();
  const panel = el('div', { class: 'panel' }, [
    el('h3', { text: 'Mispicked peaks' }),
    betaSwitch('Beta isotope filter', s.relfil_beta,
               (v) => { updateSetting({ relfil_beta: v }); rerender(); refreshRun(); },
               'Run the beta isotope filter in place of the mispicked-peak correction'),
  ]);
  if (!s.relfil_beta) {
    panel.append(
      checkbox('Mispicked Peak Correction', s.relfil, (v) => { updateSetting({ relfil: v }); rerender(); }),
      checkbox('Merge Peaks', s.merge, (v) => updateSetting({ merge: v })),
      field('Ringing Mass Window', numberInput(s.ringingwin, (v) => updateSetting({ ringingwin: v }),
                                               { disabled: !s.relfil })),
      field('Isotope Mass Window', numberInput(s.isopeakwin, (v) => updateSetting({ isopeakwin: v }),
                                               { disabled: !s.relfil }),
            'also used as the dimer window'),
      field('tR Window', numberInput(s.RTwin, (v) => updateSetting({ RTwin: v }),
                                     { disabled: !s.relfil })),
      field('Max Isotopic Mass Shift',
            select([0, 1, 2, 3, 4, 5], s.maxisowin, (v) => updateSetting({ maxisowin: Number(v) }),
                   { disabled: !s.relfil })));
    return panel;
  }
  panel.append(
    el('p', { class: 'muted',
              text: 'Runs in place of the mispicked-peak correction. Removes isotope, ringing and '
                    + 'doubly charged peaks, and only when they are weaker than the co-eluting '
                    + 'peak they belong to.' }),
    checkbox('Merge Peaks', s.merge, (v) => updateSetting({ merge: v })),
    field('tR Window', numberInput(s.RTwin, (v) => updateSetting({ RTwin: v })),
          'min; shared with the standard correction'),
    field('Isotope Mass Tolerance', numberInput(s.iso_ppm, (v) => updateSetting({ iso_ppm: v }), { min: 0 }),
          'ppm, around each multiple of the 13C spacing (1.00335)'),
    field('Isotope Peaks per Feature',
          select([1, 2, 3, 4, 5], s.iso_max_n, (v) => updateSetting({ iso_max_n: Number(v) }))),
    field('Ringing Window', numberInput(s.ring_ppm, (v) => updateSetting({ ring_ppm: v }), { min: 0 }),
          'ppm above the parent peak'),
    field('Ringing Intensity Limit',
          numberInput(s.ring_ratio_max, (v) => updateSetting({ ring_ratio_max: v }), { min: 0, max: 1 }),
          'fraction of the parent peak; a ringing peak is weaker than this'),
    collapsible({
      title: 'More',
      open: false,
      body: el('div', {}, [
        field('Isotope Intensity Limit',
              numberInput(s.iso_ratio_max, (v) => updateSetting({ iso_ratio_max: v }), { min: 0, max: 1 }),
              'fraction of the parent peak; an isotope peak is no stronger than this'),
        field('Doubly Charged Tolerance',
              numberInput(s.dimer_tol, (v) => updateSetting({ dimer_tol: v }), { min: 0 }),
              'Da, around a half-integer mass difference'),
        field('Correlation Threshold',
              numberInput(s.corr_cut, (v) => updateSetting({ corr_cut: v }), { min: 0, max: 1 }),
              'Pearson r across samples; shared with the beta adduct filter'),
        field('Correlation p Value',
              numberInput(s.corr_p, (v) => updateSetting({ corr_p: v }), { min: 0, max: 1 }),
              'the correlation must be significant below this'),
      ]),
    }));
  return panel;
}

function insourcePanel() {
  const s = settings();
  const rerender = () => refreshFiltering();
  const panel = el('div', { class: 'panel' }, [
    el('h3', { text: 'In-source fragments & adducts' }),
    betaSwitch('Beta adduct filter', s.adductgrp,
               (v) => { updateSetting({ adductgrp: v }); rerender(); refreshRun(); },
               'Run the beta adduct filter in place of in-source fragment deconvolution'),
  ]);
  if (!s.adductgrp) {
    panel.append(
      checkbox('Insource Ion Filter', s.decon, (v) => { updateSetting({ decon: v }); rerender(); }),
      field('Correlation threshold',
            numberInput(s.deconthresh, (v) => updateSetting({ deconthresh: v }),
                        { disabled: !s.decon, min: 0, max: 1 }),
            'a high threshold avoids filtering genuinely co-regulated analytes'),
      el('p', { class: 'muted',
                text: 'Within a highly correlated cluster of co-eluting features, the '
                      + 'highest-mass feature is taken as the parent ion and the rest are '
                      + 'filtered as likely in-source fragments.' }));
    return panel;
  }
  const mode = modeForms();
  panel.append(
    el('p', { class: 'muted',
              text: 'Runs in place of in-source deconvolution. Co-eluting features whose m/z '
                    + 'differ by one of the ion forms ticked below, and whose abundances rise and '
                    + 'fall together across samples, are grouped as one molecule; the most '
                    + 'abundant is kept.' }),
    el('div', { class: 'field' }, [
      el('label', { text: `Ion forms to group, as m/z difference from ${mode.base || 'the base ion'}` }),
      ionFormList('adducts', { withBase: false, onChange: refreshRun }),
      el('div', { class: 'suffix',
                  text: `${mode.name || 'Positive'} mode. Change the mode under Ion mode above.` }),
    ]),
    field('m/z Tolerance', numberInput(s.adduct_ppm, (v) => updateSetting({ adduct_ppm: v }), { min: 0 }),
          'ppm'),
    field('tR Window', numberInput(s.adduct_rt_win, (v) => updateSetting({ adduct_rt_win: v }), { min: 0 }),
          'min; forms of one molecule elute together'),
    field('Correlation Threshold',
          numberInput(s.corr_cut, (v) => updateSetting({ corr_cut: v }), { min: 0, max: 1 }),
          'Pearson r across samples; shared with the beta isotope filter'));
  return panel;
}

function massDefectPanel() {
  const s = settings();
  return el('div', { class: 'panel' }, [
    el('h3', { text: 'Mass defect filtering' }),
    el('div', { class: 'beta-line' }, [betaTag()]),
    checkbox('Mass Defect Filter', s.mdfil,
             (v) => { updateSetting({ mdfil: v }); refreshFiltering(); refreshRun(); }),
    (() => {
      const node = checkbox('Allow halogenated compounds', s.md_halogen,
                            (v) => updateSetting({ md_halogen: v }));
      node.querySelector('input').disabled = !s.mdfil;
      node.classList.toggle('disabled', !s.mdfil);
      node.title = 'Widens the allowed band for compounds carrying several Cl or Br';
      return node;
    })(),
    el('p', { class: 'muted',
              text: 'Removes features whose m/z cannot belong to a singly charged CHNOPS '
                    + 'molecule: mostly multiply charged ions, salt clusters and noise. It runs '
                    + 'first, and can only rule features out below about m/z 690.' }),
  ]);
}

function filteringView() {
  const node = el('div');

  refreshFiltering = () => {
    const s = settings();
    const groups = store.get('groups');
    clear(node);
    node.append(el('h2', { text: 'Filtering Settings' }));
    node.append(el('p', { class: 'lede' },
      ['Mispicked peaks, solvent-blank features, nonreproducible features and '
       + 'in-source fragments.']));

    node.append(ionModePanel());

    node.append(el('div', { class: 'grid cols-2' }, [
      // --- mispicked peaks -------------------------------------------
      mispickedPanel(),

      // --- blank filtering / group parsing ---------------------------
      el('div', { class: 'panel' }, [
        el('h3', { text: 'Blank filtering & group parsing' }),
        checkbox('Solvent Blank Filtering', s.blnkfltr,
                 (v) => { updateSetting({ blnkfltr: v }); refreshFiltering(); }),
        field('Blank Code',
              groups.length
                ? select([['', 'Select a group...'], ...groups], s.blnkgrp,
                         (v) => updateSetting({ blnkgrp: v }), { disabled: !s.blnkfltr })
                : el('input', { type: 'text', value: s.blnkgrp || '', disabled: !s.blnkfltr,
                                placeholder: 'select input files to list groups',
                                on: { change: (e) => updateSetting({ blnkgrp: e.target.value }) } })),
        el('div', { class: 'radio-row' }, [
          el('label', {}, [
            el('input', { type: 'radio', name: 'blankparam', checked: s.blankfilparam === 'relative',
                          on: { change: () => { updateSetting({ blankfilparam: 'relative', blankfilthresh: 0.01 }); refreshFiltering(); } } }),
            el('span', { text: 'Relative Threshold' }),
          ]),
          el('label', {}, [
            el('input', { type: 'radio', name: 'blankparam', checked: s.blankfilparam === 'absolute',
                          on: { change: () => { updateSetting({ blankfilparam: 'absolute', blankfilthresh: 100 }); refreshFiltering(); } } }),
            el('span', { text: 'Absolute Threshold' }),
          ]),
        ]),
        field(s.blankfilparam === 'relative' ? 'Relative threshold' : 'Absolute threshold',
              numberInput(s.blankfilthresh, (v) => updateSetting({ blankfilthresh: v })),
              s.blankfilparam === 'relative'
                ? "fraction of the feature's abundance in its most abundant group. "
                  + '0.01 to 0.1 is a good threshold for presence/absence determination across groups'
                : 'raw or normalised abundance value'),
      ]),

      // --- CV filtering ----------------------------------------------
      el('div', { class: 'panel' }, [
        el('h3', { text: 'CV (reproducibility) filtering' }),
        checkbox('CV Filter', s.CVfil, (v) => { updateSetting({ CVfil: v }); refreshFiltering(); }),
        el('div', { class: 'radio-row' }, [
          el('label', {}, [
            el('input', { type: 'radio', name: 'cvparam', checked: s.cvparam === 'median CV',
                          disabled: !s.CVfil,
                          on: { change: () => updateSetting({ cvparam: 'median CV' }) } }),
            el('span', { text: 'Median CV' }),
          ]),
          el('label', {}, [
            el('input', { type: 'radio', name: 'cvparam', checked: s.cvparam === 'average CV',
                          disabled: !s.CVfil,
                          on: { change: () => updateSetting({ cvparam: 'average CV' }) } }),
            el('span', { text: 'Mean CV' }),
          ]),
        ]),
        field('CV Threshold', numberInput(s.cvthresh, (v) => updateSetting({ cvthresh: v }),
                                          { disabled: !s.CVfil }),
              'published thresholds are 0.2 to 0.3; up to 0.5 for exploratory work'),
        el('p', { class: 'muted',
                  text: 'Median CV is recommended: it is less sensitive to a few very '
                        + 'nonreproducible replicates.' }),
      ]),

      // --- in-source deconvolution / adducts --------------------------
      insourcePanel(),

      // --- mass defect ------------------------------------------------
      massDefectPanel(),
    ]));
  };

  refreshFiltering();
  return { id: 'filtering', label: 'Filtering Settings', group: 'Setup', node,
           onShow: refreshFiltering };
}

// --------------------------------------------------------------------------
// Analysis Settings (calculations, plots, Plot Feature Sets, database)
// --------------------------------------------------------------------------

// Groups picked in the bucket lists, to be moved together. Kept outside the
// editor because the editor is rebuilt after every change.
const pickedGroups = new Set();
let pickAnchor = null;        // {bucket, group} a Shift-click extends from
let pickedIn = -1;            // the feature set the picks belong to

function groupsetEditor() {
  const groups = store.get('groups');
  const groupsets = store.get('groupsets');
  const selected = store.get('selectedGroupset');
  const current = groupsets[selected];
  const wrap = el('div', { class: 'groupsets' });

  // Picks do not carry over to another feature set, or outlive their groups.
  if (pickedIn !== selected) { pickedGroups.clear(); pickAnchor = null; pickedIn = selected; }
  for (const group of [...pickedGroups]) if (!groups.includes(group)) pickedGroups.delete(group);

  // --- list ---------------------------------------------------------
  const list = el('ul');
  groupsets.forEach((item, index) => {
    list.append(el('li', {
      class: index === selected ? 'active' : '',
      on: { click: () => { store.set({ selectedGroupset: index }); refreshAnalysis(); } },
    }, [
      el('span', { class: 'swatch', style: { background: item.colour } }),
      el('span', { class: 'name', text: item.name }),
    ]));
  });
  if (!groupsets.length) {
    list.append(el('li', { class: 'muted', text: 'No feature sets; one will be created for you' }));
  }

  wrap.append(el('div', { class: 'groupset-list' }, [
    list,
    el('div', { class: 'list-actions' }, [
      el('button', {
        class: 'btn small', text: '+', title: 'Add a feature set',
        disabled: !groups.length,
        on: { click: () => { store.addGroupset(null, groups); refreshAnalysis(); } },
      }),
      el('button', {
        class: 'btn small', text: '−', title: 'Remove the selected feature set',
        disabled: selected < 0,
        on: { click: () => { store.removeGroupset(selected); refreshAnalysis(); } },
      }),
    ]),
  ]));

  // --- editor -------------------------------------------------------
  if (!current) {
    wrap.append(el('div', { class: 'muted' },
      ['With no feature set defined, one is created for you: ',
       el('em', { text: 'Features not in blanks' }),
       ' when blank filtering is on, otherwise ', el('em', { text: 'All features' }), '.']));
    return wrap;
  }

  const editor = el('div');
  editor.append(el('div', { class: 'btn-row', style: { marginBottom: '10px' } }, [
    el('input', {
      type: 'text', value: current.name, style: { flex: '1', minWidth: '160px' },
      on: { change: (e) => { store.updateGroupset(selected, { name: e.target.value }); refreshAnalysis(); } },
    }),
    el('label', { class: 'check' }, [
      el('span', { class: 'muted', text: 'Colour' }),
      el('input', {
        type: 'color', value: current.colour,
        on: { change: (e) => { store.updateGroupset(selected, { colour: e.target.value }); refreshAnalysis(); } },
      }),
    ]),
  ]));

  // Three buckets: Available (excl), May be in (src), Must be in (incl).
  // A feature matches if it is in ALL "Must be in" groups and NONE of the
  // groups left in Available -- exactly the desktop's rule.
  const buckets = [
    ['excl', 'Available groups', 'feature must NOT be in these'],
    ['src', 'May be in', 'presence here does not matter'],
    ['incl', 'Must be in', 'feature must be in ALL of these'],
  ];

  /** Move one or several groups to a bucket, keeping the dataset's order. */
  const move = (moving, to) => {
    const going = new Set(moving);
    const patch = { src: [...current.src], incl: [...current.incl], excl: [...current.excl] };
    for (const key of ['src', 'incl', 'excl']) patch[key] = patch[key].filter((g) => !going.has(g));
    patch[to] = [...patch[to], ...groups.filter((g) => going.has(g))];
    pickedGroups.clear();
    pickAnchor = null;
    store.updateGroupset(selected, patch);
    refreshAnalysis();
  };

  // Picking works like a file list: click picks one, Ctrl-click adds or
  // removes one, Shift-click picks the run from the last one clicked. What
  // is picked then moves together, by dragging any of it or with the
  // buttons underneath. Classes are updated in place so a click does not
  // rebuild the lists under the pointer.
  const rows = new Map();          // group -> its <li>
  const moveButtons = [];
  const pickedNote = el('span', { class: 'muted pick-note' });
  const syncPicked = () => {
    for (const [group, row] of rows) row.classList.toggle('selected', pickedGroups.has(group));
    for (const button of moveButtons) button.disabled = !pickedGroups.size;
    pickedNote.textContent = pickedGroups.size
      ? `${pickedGroups.size} selected — move to:`
      : 'Click groups to select them (Shift or Ctrl for several), then drag or move to:';
  };
  const pick = (group, key, event) => {
    const list = current[key];
    const additive = event.ctrlKey || event.metaKey;
    if (event.shiftKey && pickAnchor && pickAnchor.bucket === key
        && list.includes(pickAnchor.group)) {
      const from = list.indexOf(pickAnchor.group);
      const to = list.indexOf(group);
      if (!additive) pickedGroups.clear();
      for (const item of list.slice(Math.min(from, to), Math.max(from, to) + 1)) {
        pickedGroups.add(item);
      }
    } else if (additive) {
      if (pickedGroups.has(group)) pickedGroups.delete(group);
      else pickedGroups.add(group);
      pickAnchor = { bucket: key, group };
    } else {
      const wasOnlyPick = pickedGroups.size === 1 && pickedGroups.has(group);
      pickedGroups.clear();
      if (!wasOnlyPick) pickedGroups.add(group);
      pickAnchor = { bucket: key, group };
    }
    syncPicked();
  };

  const bucketNodes = el('div', { class: 'group-buckets' });
  for (const [key, title, hint] of buckets) {
    const items = el('ul');
    for (const group of current[key]) {
      const row = el('li', {
        draggable: true, text: group,
        title: 'Click to select (Shift or Ctrl for several); drag to another list',
        on: {
          click: (event) => pick(group, key, event),
          // Shift-click would otherwise select the text in between.
          mousedown: (event) => { if (event.shiftKey) event.preventDefault(); },
          dragstart: (event) => {
            // Dragging a picked group takes every picked group with it.
            const moving = pickedGroups.has(group)
              ? groups.filter((g) => pickedGroups.has(g)) : [group];
            event.dataTransfer.setData('text/plain', JSON.stringify(moving));
            event.dataTransfer.effectAllowed = 'move';
          },
        },
      });
      rows.set(group, row);
      items.append(row);
    }
    const bucket = el('div', { class: 'bucket' }, [
      el('h4', { text: title, title: hint }),
      items,
    ]);
    bucket.addEventListener('dragover', (event) => {
      event.preventDefault();
      bucket.classList.add('dragover');
    });
    bucket.addEventListener('dragleave', () => bucket.classList.remove('dragover'));
    bucket.addEventListener('drop', (event) => {
      event.preventDefault();
      bucket.classList.remove('dragover');
      const raw = event.dataTransfer.getData('text/plain');
      let moving = [raw];
      try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) moving = parsed;
      } catch { /* a plain group name */ }
      // Only this dataset's groups: a drop can carry any text at all.
      moving = moving.filter((group) => groups.includes(group));
      if (moving.length) move(moving, key);
    });
    bucketNodes.append(bucket);
  }
  editor.append(bucketNodes);

  const moveRow = el('div', { class: 'btn-row move-row', style: { marginTop: '8px' } }, [pickedNote]);
  for (const [key, title] of buckets) {
    const button = el('button', {
      class: 'btn small', type: 'button', text: title,
      on: { click: () => { if (pickedGroups.size) move([...pickedGroups], key); } },
    });
    moveButtons.push(button);
    moveRow.append(button);
  }
  editor.append(moveRow);
  syncPicked();

  editor.append(el('div', { class: 'btn-row', style: { marginTop: '10px' } }, [
    el('button', {
      class: 'btn small', text: 'Plot every feature',
      title: 'Move every group except the blank group into "May be in"',
      on: {
        click: () => {
          const blank = settings().blnkgrp;
          const src = groups.filter((g) => !(settings().blnkfltr && g === blank));
          const excl = settings().blnkfltr && blank ? [blank] : [];
          store.updateGroupset(selected, { src, incl: [], excl });
          refreshAnalysis();
        },
      },
    }),
  ]));
  wrap.append(editor);
  return wrap;
}

function analysisView() {
  const node = el('div');

  refreshAnalysis = () => {
    const s = settings();
    const groups = store.get('groups');
    clear(node);
    node.append(el('h2', { text: 'Analysis Settings' }));
    node.append(el('p', { class: 'lede' },
      ['Which two groups the statistics compare, the colour-coded Plot Feature Sets '
       + 'used across every plot, and the database search.']));

    node.append(el('div', { class: 'grid cols-2' }, [
      el('div', { class: 'panel' }, [
        // Fold change, the t test and FDR correction have no toggles: they are
        // hidden on the desktop too and always run, so the only choice here is
        // which two groups to compare.
        el('h3', {}, [el('span', { text: 'Statistics' }),
                      el('span', { class: 'hint',
                                   text: 'fold change, t test and FDR correction always run' })]),
        el('div', { class: 'grid cols-2' }, [
          field('Experimental Group',
                select([['', 'Select...'], ...groups], s.expgrp,
                       (v) => updateSetting({ expgrp: v }),
                       { disabled: !groups.length })),
          field('Control Group',
                select([['', 'Select...'], ...groups], s.ctrgrp,
                       (v) => updateSetting({ ctrgrp: v }),
                       { disabled: !groups.length })),
        ]),
        el('p', { class: 'muted',
                  text: 'The volcano plot compares these two groups.' }),
      ]),
    ]));

    node.append(el('div', { class: 'panel' }, [
      el('h3', {}, [el('span', { text: 'Plot Feature Sets' }),
                    el('span', { class: 'hint',
                                 text: 'colour features by which biological groups they were detected in' })]),
      groups.length
        ? groupsetEditor()
        : el('p', { class: 'muted', text: 'Select your input files first. Feature sets are built from your biological groups.' }),
    ]));

    node.append(el('div', { class: 'panel' }, [
      el('h3', {}, [el('span', { text: 'Database search' }),
                    el('span', { class: 'hint', text: 'Natural Products Atlas, used in Feature Info' })]),
      el('div', { class: 'grid cols-3' }, [
        field('ppm threshold', numberInput(s.ppmthresh, (v) => updateSetting({ ppmthresh: v }))),
        field('Taxon', taxonPicker({
          ...savedTaxon(s),
          suggest: async (level, text) => (await api.taxaAll(level, text)).names,
          onChange: (level, value) => updateSetting({
            taxon_level: level, taxon: value,
            kingdom: level === 'kingdom' ? value : '',
            genus: level === 'genus' ? value : '',
          }),
        }).node),
      ]),
      el('div', { class: 'field' }, [
        el('label', { text: `Ion forms a feature is searched as (${modeForms().name || 'Positive'} mode), `
                            + 'with the mass each adds to the molecule' }),
        ionFormList('search_adducts', { withBase: true, onChange: refreshRun }),
        el('div', { class: 'suffix',
                    text: 'With none ticked, the first two are used. More forms means more matches '
                          + 'by chance.' }),
      ]),
      (store.get('health') || {}).npatlas === false
        ? el('div', { class: 'notice warn',
                      text: 'NPAtlas is not installed on this server, so database matching '
                            + 'is unavailable. Run scripts/fetch_reference_data.py to enable it.' })
        : null,
    ]));
  };

  refreshAnalysis();
  return { id: 'analysis', label: 'Analysis Settings', group: 'Setup', node,
           onShow: refreshAnalysis };
}

// --------------------------------------------------------------------------
// Run
// --------------------------------------------------------------------------

function validate() {
  const s = settings();
  const inputs = store.get('inputs');
  const problems = [];
  if (!inputs.peaktable) problems.push('Select a peak table.');
  if (!inputs.samplelist) problems.push('Select a sample list.');
  if (!inputs.metadata) problems.push('Select a sample metadata file.');
  if (store.get('groupError')) problems.push(store.get('groupError'));
  const unresolved = store.get('groupUnresolved');
  if (unresolved && !store.get('groupError')) {
    const shown = unresolved.names.slice(0, 3).join(', ');
    problems.push(`${unresolved.total} peak-table injection${unresolved.total === 1 ? ' is' : 's are'} `
                  + `not in the sample list and metadata (${shown}${unresolved.total > 3 ? ', …' : ''}). `
                  + 'Add them with Edit on File Selection.');
  }
  if (s.blnkfltr && !s.blnkgrp) problems.push('Blank filtering is on but no blank group is selected.');
  if (s.adductgrp) {
    const grouped = new Set(s.adducts || []);
    if (!modeForms().forms.some((form) => form.kind !== 'base' && grouped.has(form.label))) {
      problems.push('The beta adduct filter is on but no ion forms are ticked for this ion mode.');
    }
  }
  if (s.Ttest && !(s.expgrp && s.ctrgrp)) {
    problems.push('The t-test needs an experimental and a control group.');
  }
  if (s.expgrp && s.expgrp === s.ctrgrp) {
    problems.push('The experimental and control groups must be different.');
  }
  return problems;
}

function runView() {
  const node = el('div');
  let logTimer = null;
  let submitting = false;

  const stopLogPolling = () => { clearInterval(logTimer); logTimer = null; };

  refreshRun = () => {
    const run = store.get('run');
    const problems = validate();
    clear(node);
    node.append(el('h2', { text: 'Run Analysis' }));

    const summaryPanel = el('div', { class: 'panel' }, [el('h3', { text: 'Ready to run' })]);
    if (problems.length) {
      for (const problem of problems) {
        summaryPanel.append(el('div', { class: 'notice error', text: problem }));
      }
    } else {
      const s = settings();
      const active = [
        s.mdfil && 'mass defect filter (beta)',
        s.relfil_beta ? 'isotope filter (beta)' : s.relfil && 'mispicked-peak correction',
        s.blnkfltr && `blank filtering (${s.blnkgrp})`,
        s.CVfil && `CV filter (${s.cvparam} ≥ ${s.cvthresh})`,
        s.adductgrp ? `adduct filter (beta, ${(s.adducts || []).filter((label) =>
          modeForms().forms.some((form) => form.label === label)).length} ion forms)`
          : s.decon && `in-source deconvolution (r ≥ ${s.deconthresh})`,
      ].filter(Boolean);
      summaryPanel.append(el('div', { class: 'notice ok' },
        [`Filters: ${active.length ? active.join(', ') : 'none'}`]));
      if (s.ionmode === 'neg') {
        summaryPanel.append(el('div', { class: 'notice info' }, ['Ion mode: negative (beta)']));
      }
      if (s.Ttest || s.FC) {
        summaryPanel.append(el('div', { class: 'notice info' },
          [`Statistics: ${s.expgrp} vs ${s.ctrgrp}${s.FDR ? ' with FDR correction' : ''}`]));
      }
      summaryPanel.append(el('p', { class: 'muted' },
        [`${store.get('groupsets').length || 'the default'} plot feature set`
         + `${store.get('groupsets').length === 1 ? '' : 's'} · `
         + `${store.get('groups').length} biological groups`]));
    }

    const running = run && (run.state === 'running' || run.state === 'queued');
    // A name for the run about to start, so a parameter sweep is more than a
    // list of identical peak-table names in the run history.
    summaryPanel.append(el('label', { class: 'run-label' }, [
      el('span', { class: 'muted', text: 'Label' }),
      el('input', {
        type: 'text', maxLength: 120, value: store.get('runLabel') || '',
        placeholder: 'optional, e.g. "CV 0.3, no in-source filter"',
        on: { input: (event) => store.set({ runLabel: event.target.value }) },
      }),
    ]));
    summaryPanel.append(el('div', { class: 'btn-row' }, [
      el('button', {
        class: 'btn primary',
        text: run && run.state === 'queued' ? 'Queued…' : running ? 'Running…' : 'Run',
        disabled: Boolean(problems.length) || running || submitting,
        on: { click: startRun },
      }),
      running ? el('button', {
        class: 'btn danger', text: 'Cancel',
        on: { click: async () => {
          try { await api.cancelRun(run.run_id); } catch (error) { toast(error.message, 'error'); }
        } },
      }) : null,
      el('button', {
        class: 'btn', text: 'Reset settings',
        on: { click: async () => {
          store.clearSetup();
          const body = await api.defaults();
          store.set({ settings: body.settings, ions: body.ions, groupsets: [], selectedGroupset: -1 });
          refreshFiltering(); refreshAnalysis(); refreshRun();
          toast('Settings reset to defaults', 'info');
        } },
      }),
    ]));
    node.append(summaryPanel);

    // The terminal stays up with no run too: the dinosaur idles in it.
    const statusPanel = el('div', { class: 'panel progress-panel' }, [
      el('h3', {}, [el('span', { text: 'Progress' }),
                    el('span', { class: 'hint', text: run ? run.stage || run.state : 'idle' })]),
    ]);
    if (running) statusPanel.append(el('div', { class: 'progress' }, [el('div')]));
    if (run && run.error) statusPanel.append(el('div', { class: 'notice error', text: run.error }));
    for (const warning of (run && run.warnings) || []) {
      statusPanel.append(el('div', { class: 'notice warn', text: warning }));
    }
    const log = el('pre', { class: 'log', text: run ? 'Waiting for output…' : '' });
    const terminal = el('div', { class: 'terminal' }, [log]);
    statusPanel.append(terminal);
    node.append(statusPanel);
    attachDino(terminal);

    if (run) {
      let pulling = false;
      const pull = async () => {
        // Not while another pull is still out (a slow connection would stack
        // them up), and not for a view nobody is looking at.
        if (pulling || !node.classList.contains('active')) return;
        pulling = true;
        try {
          const body = await api.runLog(run.run_id);
          const text = body.log || 'No output yet.';
          if (log.textContent !== text) {
            const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 40;
            log.textContent = text;
            if (atBottom) log.scrollTop = log.scrollHeight;
          }
        } catch { /* the run may have been deleted */ } finally {
          pulling = false;
        }
      };
      pull();
      stopLogPolling();
      if (running) logTimer = setInterval(pull, 2000);
    } else {
      stopLogPolling();
    }
  };

  async function startRun(event) {
    if (submitting) return;
    submitting = true;
    dinoPressed(true);
    if (event && event.currentTarget) event.currentTarget.disabled = true;
    const inputs = store.get('inputs');
    const payload = {
      inputs: {
        peaktable: inputs.peaktable,
        samplelist: inputs.samplelist,
        metadata: inputs.metadata,
        fragments: inputs.fragments || null,
      },
      settings: { ...settings(), groupsets: store.get('groupsets') },
      label: (store.get('runLabel') || '').trim() || null,
    };
    try {
      const body = await api.createRun(payload);
      store.set({ run: { run_id: body.run_id, state: 'queued', stage: 'queued',
                         label: payload.label },
                  catalogue: null, selectedFeature: null });
      watchRun(body.run_id);
    } catch (error) {
      toast(error.message, 'error');
      // Already queued or running: show that run rather than a dead button.
      if (error.status === 409 && error.body && error.body.run_id) {
        try { await selectRun(error.body.run_id); } catch { /* deleted meanwhile */ }
      }
    } finally {
      submitting = false;
      dinoPressed(false);   // the store's run (or its absence, on an error) takes over
      refreshRun();
    }
  }

  store.on('run', () => { if (node.classList.contains('active')) refreshRun(); });
  refreshRun();
  // The log takes whatever height is left and scrolls inside itself, so the
  // page behind it never grows a scrollbar of its own.
  node.classList.add('run-view');
  return { id: 'run', label: 'Run', group: 'Setup', node, onShow: refreshRun };
}

// --------------------------------------------------------------------------

export function buildSetupViews() {
  // Restore the previous form state before the views first render, so a
  // reload lands on the same configuration rather than the bare defaults.
  const saved = store.restoreSetup();
  if (saved) {
    store.set({
      inputs: { ...store.get('inputs'), ...(saved.inputs || {}) },
      settings: saved.settings || null,
      groupsets: saved.groupsets || [],
      selectedGroupset: (saved.groupsets || []).length ? 0 : -1,
    });
  }

  registerView(filesView());
  registerView(filteringView());
  registerView(analysisView());
  registerView(runView());

  // Load defaults and the library, then let the views repaint.
  (async () => {
    try {
      // Always asked for: it carries the ion-form table, and a form saved in
      // this browser by an older version lacks the settings added since.
      const body = await api.defaults();
      store.set({ ions: body.ions,
                  settings: { ...body.settings, ...(store.get('settings') || {}) } });
      store.set({ files: await api.listFiles() });
      // The slots are restored from this browser's storage, the library from
      // the server. A slot can therefore name a file that is no longer there
      // (the session expired, or it was removed from another tab): it looked
      // empty but still counted as filled, so the form reported "file not
      // found" as a group error instead of asking for the file.
      const known = new Set(store.get('files').map((file) => file.id));
      const inputs = { ...store.get('inputs') };
      let dropped = false;
      for (const kind of Object.keys(inputs)) {
        if (inputs[kind] && !known.has(inputs[kind])) { inputs[kind] = null; dropped = true; }
      }
      if (dropped) { store.set({ inputs }); store.persistSetup(); }
      // Optional: a server may ship without the demo data.
      try { store.set({ demo: await api.demoInfo() }); } catch { /* no demo */ }
    } catch (error) {
      toast(error.message, 'error');
    }
    refreshFiles();
    refreshFiltering();
    refreshAnalysis();
    refreshRun();
    deriveGroups();
  })();
}

/** Put a previous run's settings, feature sets and input files back in the
 *  form, ready to change and run again.
 *
 *  Returns the kinds of input that could not be restored (the library file
 *  has since been removed); those slots are left empty to be filled. */
export async function applyRunRequest(request) {
  const [defaults, files] = await Promise.all([api.defaults(), api.listFiles()]);
  const inputsOf = request.inputs || {};
  const inputs = { peaktable: null, samplelist: null, metadata: null, fragments: null };
  const missing = [];
  for (const kind of Object.keys(inputs)) {
    if (!inputsOf[kind]) continue;
    // The same library file if it is still there; otherwise one of the same
    // kind and name (it may have been uploaded again since).
    const record = files.find((file) => file.id === inputsOf[kind])
      || files.find((file) => file.kind === kind && file.name === inputsOf[`${kind}_name`]);
    if (record) inputs[kind] = record.id;
    else missing.push(kind);
  }

  // Only the settings this version of the form has: an old run can carry
  // fields that have since gone, and the server rejects unknown ones.
  const { groupsets = [], ...saved } = request.settings || {};
  const restored = { ...defaults.settings };
  delete restored.groupsets;
  for (const key of Object.keys(restored)) if (key in saved) restored[key] = saved[key];

  store.set({ files, inputs, settings: restored, groupsets,
              selectedGroupset: groupsets.length ? 0 : -1,
              runLabel: request.label || '' });
  store.persistSetup();
  refreshFiles();
  refreshFiltering();
  refreshAnalysis();
  refreshRun();
  deriveGroups();
  return missing;
}

export const refreshSetupViews = () => {
  refreshFiles(); refreshFiltering(); refreshAnalysis(); refreshRun();
};
