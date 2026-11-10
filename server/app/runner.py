"""The child-process entry point that executes one analysis run.

Runs in its own OS process (see ``jobs.py``) for three reasons:

1. ``MSFaST``/``filter``/``stats`` communicate through a module-level
   ``global analysis_params``. Two concurrent runs inside one interpreter
   would overwrite each other's parameters mid-pipeline. Process isolation
   makes that structurally impossible without touching the vendored code.
2. The pipeline is CPU-bound pandas/numpy work; it must not sit on the
   server's event loop.
3. ``pvclust`` spawns its own worker pool for bootstrap dendrograms. A
   non-daemonic child process can have children of its own; a thread-pool or
   daemonic worker could not.

The sequence below mirrors ``MainWindow.run_analysis`` ->
``AnalysisWorker.run`` -> ``MainWindow._finish_analysis`` in the desktop app,
minus the Qt plot construction: convert the peak table, resolve biological
groups, build parameters, call ``run_MSFaST``, then do the same post-run
bookkeeping the desktop does (``maxval``, the volcano plot's ``logfc``
column, ``export_filtered_outputs``, ``write_save``).
"""

import json
import os
import pickle
import sys
import time
import traceback
from pathlib import Path


class _FormatCheckShim:
    """Duck-typed stand-in for ``MainWindow`` in ``mzmineimport.format_check``.

    ``format_check`` only ever touches ``parent.filename`` (which it rewrites
    when MS-DIAL conversion produces a sibling .csv) and ``parent.error()``.
    Passing this instead of adapting the function keeps the vendored
    translator layer unmodified.
    """

    def __init__(self, filename):
        self.filename = Path(filename)
        self.messages = []

    def error(self, message):
        self.messages.append(str(message))
        print('warning: ' + str(message))


class InputError(Exception):
    """A problem with the inputs, reported to the user as written."""


def _write_status(run, **fields):
    status = run.read_status() or {}
    status.update(fields)
    run.write_status(status)


def _stage(run, name):
    print(f'--- {name}')
    _write_status(run, stage=name)


def execute(session_id, run_id):
    """Run the pipeline for ``run_id``. Returns 0 on success, 1 on failure."""
    # Imported here, not at module scope: on a spawn-based platform this
    # module is re-imported in a fresh interpreter, and the heavy scientific
    # stack should be loaded inside the child's own working directory setup.
    from . import inspect as inspection, msdial, params as params_builder
    from . import samplemap, storage
    from .core import MSFaST, biogroups, mzmineimport, translators

    session = storage.Session(session_id)
    run = storage.Run(session, run_id)
    request = run.read_request()
    if request is None:
        _write_status(run, state='failed', error='run request missing',
                      finished_at=time.time())
        return 1

    # Only the settings block is re-validated here. The stored request also
    # carries fields the server added after validation (resolved filenames,
    # created_at), which the strict RunRequest model would reject on re-parse.
    from .schemas import AnalysisSettings
    settings = AnalysisSettings.model_validate(request.get('settings') or {})

    _write_status(run, state='running', started_at=time.time(), stage='starting',
                  error=None)
    warnings = []

    try:
        # --- 1. peak-table format detection + in-place conversion ---------
        # Conversion rewrites the file, which is why it happens on the run's
        # own copy in rawdata/ and never on the library original.
        _stage(run, 'Converting peak table')
        peaktable = run.rawdata / request['inputs']['peaktable_name']
        # An MS-DIAL export uploaded before newer versions' layouts were
        # understood is still in the library as it came; lay it out the way
        # the vendored reader expects. Already-converted files are untouched.
        peaktable = msdial.normalize(peaktable) or peaktable
        shim = _FormatCheckShim(peaktable)
        detected = translators.detect_peaktable_format(peaktable)
        mzmineimport.format_check(shim)
        peaktable = Path(shim.filename)
        warnings.extend(shim.messages)

        input_paths = {
            'peaktable': peaktable,
            'samplelist': run.rawdata / request['inputs']['samplelist_name'],
            'metadata': run.rawdata / request['inputs']['metadata_name'],
        }
        if request['inputs'].get('fragments_name'):
            input_paths['fragments'] = run.rawdata / request['inputs']['fragments_name']

        # The converted file can have a different name (MS-DIAL .txt -> .csv),
        # and every output path is derived from its stem -- record it so the
        # results layer resolves the same outputdir the pipeline wrote to.
        request.setdefault('resolved', {})
        request['resolved']['peaktable_name'] = peaktable.name
        request['resolved']['detected_format'] = detected
        run.write_request(request)

        # --- 2. biological groups (desktop: MainWindow.getgroups) ---------
        _stage(run, 'Resolving biological groups')
        # This run's copies of the sheets, cut down to this peak table's
        # injections with repeats collapsed, so a class's composite sample
        # list and metadata work with any one dataset (see samplemap.py).
        warnings.extend(samplemap.fit(inspection.injections_of(peaktable),
                                      input_paths['samplelist'], input_paths['metadata']))
        groups, unresolved = biogroups.compute_biological_groups(
            input_paths['metadata'], input_paths['samplelist'], input_paths['peaktable'])
        if unresolved:
            # The pipeline looks every injection up and stops with a bare
            # KeyError on the first one it can't find; say which, and how
            # to fix it, before starting.
            unique = list(dict.fromkeys(map(str, unresolved)))
            raise InputError(
                f'{len(unique)} injection(s) in the peak table have no Sample_Code and '
                f'Biological_Group in the sample list and metadata: '
                f'{", ".join(unique[:10])}' + (' ...' if len(unique) > 10 else '')
                + '. Add them (File Selection > Edit) and run again.')

        # --- 3. parameters ------------------------------------------------
        params, model = params_builder.build_params(run, settings, groups, input_paths)
        (run.root / 'params.json').write_text(
            json.dumps(params_builder.params_to_json(params, model), indent=2))

        # --- 4. the pipeline, unmodified ----------------------------------
        _stage(run, 'Filtering and statistics')
        result = MSFaST.run_MSFaST(params)

        # --- 5. post-run bookkeeping (desktop: _finish_analysis) ----------
        _stage(run, 'Writing outputs')
        _post_run(run, params, result, model, warnings)

        _write_status(run, state='complete', stage='complete',
                      finished_at=time.time(), warnings=warnings,
                      groups=list(groups))
        print('--- analysis complete')
        return 0
    except InputError as exc:
        print(f'error: {exc}')
        _write_status(run, state='failed', stage='failed', error=str(exc),
                      finished_at=time.time(), warnings=warnings)
        return 1
    except Exception as exc:  # noqa: BLE001 - report any pipeline failure verbatim
        traceback.print_exc()
        _write_status(run, state='failed', stage='failed', error=f'{type(exc).__name__}: {exc}',
                      finished_at=time.time(), warnings=warnings)
        return 1


def _post_run(run, params, result, model, warnings):
    """Everything the desktop does after ``run_MSFaST`` returns, minus plots."""
    import numpy as np
    import pandas as pd

    from .core import translators

    outputdir = Path(params.outputdir)

    # maxval: the desktop reads it off iondict for abundance-tied point
    # opacity on the m/z-vs-RT and mass-defect plots. Persisted here so the
    # plot endpoints don't each recompute it.
    iondict_path = outputdir / 'iondict.csv'
    iondict = pd.read_csv(iondict_path, sep=',', header=[0], index_col=None)
    maxval = float(iondict['logmax'].max()) if 'logmax' in iondict else 0.0

    # logfc: on the desktop this column is added to iondict.csv as a side
    # effect of drawing the volcano plot. Do it here instead so the column
    # exists whether or not anyone opens that tab.
    if 'fc' in iondict.columns and 'logfc' not in iondict.columns:
        with np.errstate(divide='ignore', invalid='ignore'):
            iondict['logfc'] = np.log2(iondict['fc'])
        iondict.to_csv(iondict_path, header=True, index=False)

    # Per-feature-set ion lists. The pipeline returns these in memory only;
    # the web layer needs them per request, so persist the membership rather
    # than re-deriving the groupset rules on every plot call.
    sets_payload = []
    for key in params.querylist:
        groupset = result.groupsets[key]
        sets_payload.append({
            'key': key,
            'name': groupset.legendname,
            'colour': groupset.plotcol,
            'incl': list(groupset.incl),
            'excl': list(groupset.excl),
            'ions': list(groupset.ionlist),
        })
    (outputdir / 'groupsets.json').write_text(json.dumps(sets_payload))

    # Filter-level ion lists, for the treemap/upset/feature-quality views that
    # the desktop reads off ``parent.ionfilters`` / ``parent.groupionlists``.
    filter_counts = {name: len(getattr(result.ionfilters[name], 'ions', []))
                     for name in result.ionfilters}
    group_ion_counts = {str(name): len(ions)
                        for name, ions in result.groupionlists.items()}
    (outputdir / 'runmeta.json').write_text(json.dumps({
        'maxval': maxval,
        'filter_counts': filter_counts,
        'group_ion_counts': group_ion_counts,
        'blank_ions': list(result.groupionlists.get(params.blnkgrp, []))
        if params.blnkfltr else [],
    }))

    # --- exports (desktop: export_filtered_outputs) ----------------------
    filtered_internal = outputdir / (Path(params.filename).stem + '_filtered.csv')
    try:
        source = Path(params.filename)
        out = outputdir / (source.stem + '_filtered_source' + source.suffix)
        count = translators.filter_source_peaktable(source, filtered_internal, out)
        print(f'Wrote filtered source peak table ({count} features): {out}')
    except Exception:  # noqa: BLE001 - best effort, matching the desktop
        traceback.print_exc()
        warnings.append('Could not export filtered source peak table')

    fragfile = getattr(params, 'fragfilename', '')
    if fragfile:
        try:
            fragpath = Path(fragfile)
            out = outputdir / (fragpath.stem + '_reindexed' + fragpath.suffix)
            count = translators.reindex_fragments(filtered_internal, fragpath, out)
            print(f'Wrote GNPS2 re-indexed fragments ({count} entries): {out}')
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            warnings.append('Could not re-index fragmentation file')

    # --- .mpct session file (desktop: write_save) ------------------------
    # Written so a web analysis can be downloaded and reopened in the desktop
    # app -- same pickle payload, same atomic temp-file replace.
    try:
        _write_mpct(params, model)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        warnings.append('Analysis complete but .mpct save failed')


def _write_mpct(params, model):
    """Write the desktop-compatible ``.mpct`` pickle for this run."""
    import pandas as pd

    outputdir = Path(params.outputdir)
    params.queries = model.to_legacy_list()

    def capture(label, reader, default=None):
        try:
            return reader()
        except Exception:  # noqa: BLE001
            print(f'Warning: could not capture {label} for save:')
            traceback.print_exc()
            return default

    savedata = {'parameters': params}
    savedata['peaktable'] = capture(
        'peak table',
        lambda: pd.read_csv(outputdir / Path(params.filename).name, sep=',',
                            header=None, index_col=None, low_memory=False))
    savedata['samplelist'] = capture(
        'sample list',
        lambda: pd.read_csv(params.samplelistfilename, sep=',', header=None, index_col=None))
    savedata['extractmetadata'] = capture(
        'extract metadata',
        lambda: pd.read_csv(params.extractmetadatafilename, sep=',', header=None, index_col=None))
    if params.fragfilename:
        savedata['fragdb'] = capture(
            'fragment database', lambda: Path(params.fragfilename).read_text(), default='None')
    else:
        savedata['fragdb'] = 'None'

    target = outputdir / (Path(params.filename).stem + '.mpct')
    tmp = target.with_name(target.name + '.tmp')
    with open(tmp, 'wb') as handle:
        pickle.dump(savedata, handle)
    os.replace(tmp, target)
    print(f'Wrote session file: {target}')


def child_main(session_id, run_id, log_path):
    """Process entry point: tee stdout/stderr into the run log, then execute.

    The pipeline reports progress by printing ('Loading files', 'Filtering
    data', tqdm bars); capturing that stream is what lets the browser show a
    live log instead of an opaque spinner.
    """
    os.environ.setdefault('MPLBACKEND', 'Agg')
    with open(log_path, 'a', encoding='utf-8', errors='replace', buffering=1) as log:
        sys.stdout = _Tee(sys.__stdout__, log)
        sys.stderr = _Tee(sys.__stderr__, log)
        try:
            code = execute(session_id, run_id)
        finally:
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__
    return code


class _Tee:
    """Write to the real stream and the log file at once."""

    def __init__(self, primary, secondary):
        self._primary = primary
        self._secondary = secondary

    def write(self, text):
        try:
            if self._primary is not None:
                self._primary.write(text)
        except Exception:  # noqa: BLE001 - a detached parent stream must not break logging
            pass
        self._secondary.write(text)
        return len(text)

    def flush(self):
        for stream in (self._primary, self._secondary):
            try:
                if stream is not None:
                    stream.flush()
            except Exception:  # noqa: BLE001
                pass

    def isatty(self):
        return False
