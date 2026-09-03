/* Setup views: File Selection, Filtering Settings, Analysis Settings, Run.
 *
 * These are the desktop's first three tabs plus its Run button, in the same
 * order with the same controls and the same defaults. Two things the desktop
 * can't do are added, because the web changes the constraints rather than the
 * workflow: uploads persist in a per-session library so a re-run never
 * re-uploads, and the pipeline's console output streams into the Run view
 * instead of a terminal the user may not have. */

import { registerView, watchRun } from '../app.js';
import { api } from '../api.js';
import { store } from '../state.js';
import { collapsible } from '../components.js';
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

  const node = el('div', { class: 'slot' }, [
    el('div', { class: 'slot-name' }, [
      el('span', { text: slot.label }),
      el('small', { text: slot.hint }),
    ]),
    value,
    el('div', { class: 'btn-row' }, [
      el('button', { class: 'btn small', text: 'Upload…',
                     on: { click: () => fileInput.click() } }),
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
    store.set({ groups: [], groupError: null, groupWarnings: [] });
    refreshFiles();
    return;
  }
  store.set({ groupError: null });
  try {
    const body = await api.groupsFor({
      peaktable: inputs.peaktable,
      samplelist: inputs.samplelist,
      metadata: inputs.metadata,
    });
    store.set({ groups: body.groups, groupWarnings: body.warnings || [], groupError: null });
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
    store.set({ groups: [], groupError: error.message, groupWarnings: [] });
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

/** "Try it with an example" -- loads the bundled PTY087I2 dataset into this
 *  session's library and selects it in all four slots. */
function demoPanel() {
  const info = store.get('demo');
  if (!info || info.available === false) return null;

  const inputs = store.get('inputs');
  const alreadyLoaded = store.get('files').some(
    (file) => file.kind === 'peaktable' && inputs.peaktable === file.id
              && file.name.includes('PTY087I2'));

  const button = el('button', {
    class: 'btn primary', text: alreadyLoaded ? 'Demo dataset loaded' : 'Load demo dataset',
    disabled: alreadyLoaded,
  });
  button.addEventListener('click', async () => {
    button.disabled = true;
    button.textContent = 'Loading...';
    try {
      const records = await api.loadDemo();
      const merged = [...records];
      for (const file of store.get('files')) {
        if (!records.some((r) => r.id === file.id)) merged.push(file);
      }
      const picked = { ...store.get('inputs') };
      for (const record of records) picked[record.kind] = record.id;
      store.set({ files: merged, inputs: picked });
      store.persistSetup();
      refreshFiles();
      deriveGroups();
      toast('Demo dataset loaded - go to Run, or change the settings first', 'ok');
    } catch (error) {
      toast(error.message, 'error');
      button.disabled = false;
      button.textContent = 'Load demo dataset';
    }
  });

  // Collapsed by default: one line and a button is all most visitors need,
  // and the dataset's description shouldn't push the real file pickers down
  // the page every time.
  return el('div', { class: 'panel' }, [
    el('div', { class: 'btn-row', style: { gap: '12px' } }, [
      button,
      collapsible({
        title: 'New here? Load a demo dataset',
        open: false,
        body: el('div', {}, [
          el('p', { class: 'muted', style: { maxWidth: '70ch', margin: '0 0 6px' },
                    text: info.summary || '' }),
          el('p', { class: 'muted', style: { margin: '0' },
                    text: 'Fills all four slots; you can still change any of them.' }),
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

    node.append(el('div', { class: 'grid cols-2' }, [
      // --- mispicked peaks -------------------------------------------
      el('div', { class: 'panel' }, [
        el('h3', { text: 'Mispicked peaks' }),
        checkbox('Mispicked Peak Correction', s.relfil, (v) => { updateSetting({ relfil: v }); refreshFiltering(); }),
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
                     { disabled: !s.relfil })),
      ]),

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

      // --- in-source deconvolution -----------------------------------
      el('div', { class: 'panel' }, [
        el('h3', { text: 'In-source fragment deconvolution' }),
        checkbox('Insource Ion Filter', s.decon, (v) => { updateSetting({ decon: v }); refreshFiltering(); }),
        field('Correlation threshold',
              numberInput(s.deconthresh, (v) => updateSetting({ deconthresh: v }),
                          { disabled: !s.decon, min: 0, max: 1 }),
              'a high threshold avoids filtering genuinely co-regulated analytes'),
        el('p', { class: 'muted',
                  text: 'Within a highly correlated cluster of co-eluting features, the '
                        + 'highest-mass feature is taken as the parent ion and the rest are '
                        + 'filtered as likely in-source fragments.' }),
      ]),
    ]));
  };

  refreshFiltering();
  return { id: 'filtering', label: 'Filtering Settings', group: 'Setup', node,
           onShow: refreshFiltering };
}

// --------------------------------------------------------------------------
// Analysis Settings (calculations, plots, Plot Feature Sets, database)
// --------------------------------------------------------------------------

function groupsetEditor() {
  const groups = store.get('groups');
  const groupsets = store.get('groupsets');
  const selected = store.get('selectedGroupset');
  const current = groupsets[selected];
  const wrap = el('div', { class: 'groupsets' });

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

  const move = (group, to) => {
    const patch = { src: [...current.src], incl: [...current.incl], excl: [...current.excl] };
    for (const key of ['src', 'incl', 'excl']) patch[key] = patch[key].filter((g) => g !== group);
    patch[to] = [...patch[to], group];
    store.updateGroupset(selected, patch);
    refreshAnalysis();
  };

  const bucketNodes = el('div', { class: 'group-buckets' });
  for (const [key, title, hint] of buckets) {
    const items = el('ul');
    for (const group of current[key]) {
      items.append(el('li', {
        draggable: true, text: group, title: 'Drag to another list',
        on: { dragstart: (event) => event.dataTransfer.setData('text/plain', group) },
      }));
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
      const group = event.dataTransfer.getData('text/plain');
      if (group) move(group, key);
    });
    bucketNodes.append(bucket);
  }
  editor.append(bucketNodes);
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
      ['Which statistics to calculate, which plots to generate, and the colour-coded '
       + 'Plot Feature Sets used across every plot.']));

    node.append(el('div', { class: 'grid cols-2' }, [
      el('div', { class: 'panel' }, [
        el('h3', {}, [el('span', { text: 'Calculations' }),
                      el('span', { class: 'hint',
                                   text: 'every plot is generated; these control the statistics' })]),
        checkbox('Fold Change', s.FC, (v) => { updateSetting({ FC: v }); refreshAnalysis(); }),
        checkbox('T test', s.Ttest, (v) => { updateSetting({ Ttest: v }); refreshAnalysis(); }),
        checkbox('FDR Correction', s.FDR, (v) => updateSetting({ FDR: v })),
        field('Experimental Group',
              select([['', 'Select...'], ...groups], s.expgrp,
                     (v) => updateSetting({ expgrp: v }),
                     { disabled: !groups.length })),
        field('Control Group',
              select([['', 'Select...'], ...groups], s.ctrgrp,
                     (v) => updateSetting({ ctrgrp: v }),
                     { disabled: !groups.length })),
        el('p', { class: 'muted',
                  text: 'A volcano plot needs both Fold Change and T test, with an '
                        + 'experimental and a control group selected.' }),
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
        field('Kingdom', select([['', 'Any'], 'Bacterium', 'Fungus'], s.kingdom,
                                (v) => updateSetting({ kingdom: v }))),
        field('Genus', el('input', {
          type: 'text', value: s.genus || '', placeholder: 'Any',
          on: { change: (e) => updateSetting({ genus: e.target.value }) },
        })),
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
  if (s.blnkfltr && !s.blnkgrp) problems.push('Blank filtering is on but no blank group is selected.');
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
        s.relfil && 'mispicked-peak correction',
        s.blnkfltr && `blank filtering (${s.blnkgrp})`,
        s.CVfil && `CV filter (${s.cvparam} ≥ ${s.cvthresh})`,
        s.decon && `in-source deconvolution (r ≥ ${s.deconthresh})`,
      ].filter(Boolean);
      summaryPanel.append(el('div', { class: 'notice ok' },
        [`Filters: ${active.length ? active.join(', ') : 'none'}`]));
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
    summaryPanel.append(el('div', { class: 'btn-row' }, [
      el('button', {
        class: 'btn primary', text: running ? 'Running…' : 'Run',
        disabled: Boolean(problems.length) || running,
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
          store.set({ settings: body.settings, groupsets: [], selectedGroupset: -1 });
          refreshFiltering(); refreshAnalysis(); refreshRun();
          toast('Settings reset to defaults', 'info');
        } },
      }),
    ]));
    node.append(summaryPanel);

    if (run) {
      const statusPanel = el('div', { class: 'panel' }, [
        el('h3', {}, [el('span', { text: 'Progress' }),
                      el('span', { class: 'hint', text: run.stage || run.state })]),
      ]);
      if (running) statusPanel.append(el('div', { class: 'progress' }, [el('div')]));
      if (run.error) statusPanel.append(el('div', { class: 'notice error', text: run.error }));
      for (const warning of run.warnings || []) {
        statusPanel.append(el('div', { class: 'notice warn', text: warning }));
      }
      const log = el('pre', { class: 'log', text: 'Waiting for output…' });
      statusPanel.append(log);
      node.append(statusPanel);

      const pull = async () => {
        try {
          const body = await api.runLog(run.run_id);
          const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 40;
          log.textContent = body.log || 'No output yet.';
          if (atBottom) log.scrollTop = log.scrollHeight;
        } catch { /* the run may have been deleted */ }
      };
      pull();
      stopLogPolling();
      if (running) logTimer = setInterval(pull, 2000);
    } else {
      stopLogPolling();
    }
  };

  async function startRun() {
    const inputs = store.get('inputs');
    const payload = {
      inputs: {
        peaktable: inputs.peaktable,
        samplelist: inputs.samplelist,
        metadata: inputs.metadata,
        fragments: inputs.fragments || null,
      },
      settings: { ...settings(), groupsets: store.get('groupsets') },
    };
    try {
      const body = await api.createRun(payload);
      store.set({ run: { run_id: body.run_id, state: 'queued', stage: 'queued' },
                  catalogue: null, selectedFeature: null });
      watchRun(body.run_id);
      refreshRun();
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  store.on('run', () => { if (node.classList.contains('active')) refreshRun(); });
  refreshRun();
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
      if (!store.get('settings')) {
        const body = await api.defaults();
        store.set({ settings: body.settings });
      }
      store.set({ files: await api.listFiles() });
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

export const refreshSetupViews = () => {
  refreshFiles(); refreshFiltering(); refreshAnalysis(); refreshRun();
};
