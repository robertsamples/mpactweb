"""Build the desktop pipeline's ``analysis_parameters`` object from a web request.

This is the web equivalent of ``MainWindow.enumerate_inputs`` in the desktop
``main.py``, and is deliberately written to be read side by side with it: same
order, same derived values, same implicit defaults. The pipeline itself
(``MSFaST.run_MSFaST``) is vendored unmodified, so everything it needs has to
be assembled here exactly as the Qt version assembles it.
"""

import json
from pathlib import Path

from .core import MSFaST, groupsets


def default_groupsets(all_groups, blnkfltr, blnkgrp):
    """The single Plot Feature Set the desktop creates when the user never
    opened the groupset editor (``enumerate_inputs``'s ``len(...) == 0`` branch,
    via ``UIFunctions.addgroup`` -> ``GroupSetModel.add``, which seeds ``excl``
    with every known biological group).

    With blank filtering on: "Features not in blanks" -- may be in any group
    except the blank group, must not be in the blank group. With it off: "All
    features" -- may be in anything, excludes nothing.
    """
    model = groupsets.GroupSetModel()
    if blnkfltr:
        model.add('Features not in blanks', all_groups)
        src = list(model.selected.excl)
        if blnkgrp in src:
            src.remove(blnkgrp)
        model.update(model.selected_index, src=src, excl=[blnkgrp])
    else:
        model.add('All features', all_groups)
        model.update(model.selected_index, src=model.selected.excl, excl=[])
    return model


def build_groupset_model(settings, all_groups):
    """A ``GroupSetModel`` from the request, or the desktop default if the
    request carried no feature sets."""
    if not settings.groupsets:
        return default_groupsets(all_groups, settings.blnkfltr, settings.blnkgrp)
    model = groupsets.GroupSetModel()
    for item in settings.groupsets:
        model.add(item.name, [])
        model.update(model.selected_index, name=item.name, src=item.src,
                     incl=item.incl, excl=item.excl, colour=item.colour)
    model.select(0)
    return model


def build_params(run, settings, all_groups, input_paths):
    """Assemble ``analysis_parameters`` for one run.

    Args:
        run: the ``storage.Run`` whose ``rawdata/`` holds the inputs and whose
            ``output/`` is the pipeline's output directory.
        settings: an ``AnalysisSettings``.
        all_groups: every biological group in the metadata, in the order
            ``biogroups.compute_biological_groups`` returned them (the desktop
            fills its combo boxes from the same list).
        input_paths: ``{kind: Path}`` for the copies inside ``rawdata/``.

    Returns:
        (params, groupset_model) -- the model is returned too so the caller can
        persist the feature sets that were actually used, including the
        defaults filled in here.
    """
    params = MSFaST.analysis_parameters()

    # --- blank group / blank filtering (first, like the desktop: the default
    # groupset below depends on it) --------------------------------------
    params.blnkfltr = bool(settings.blnkfltr)
    params.blnkgrp = str(settings.blnkgrp) if params.blnkfltr else ''

    model = build_groupset_model(settings, all_groups)

    # --- NPAtlas filtering selectors (applied when the atlas is loaded for a
    # database search, not here -- the desktop filters its in-memory copy at
    # this point, but the web server loads the atlas per search request) --
    params.kingdom = str(settings.kingdom or '')
    params.genus = str(settings.genus or '')

    # --- graph-level filters, as a token list ----------------------------
    params.graphfilters = []
    if settings.CVfil:
        params.graphfilters.append('cv')
    if settings.relfil:
        params.graphfilters.append('rel')
    if settings.decon:
        params.graphfilters.append('insource')
        params.deconthresh = float(settings.deconthresh)

    # --- Plot Feature Sets -> querydict/querylist ------------------------
    querydict = groupsets.build_query_dict(model, params.graphfilters)
    params.querydict = querydict
    params.querylist = list(querydict.keys())
    # ``queries`` is what the desktop pickles into .mpct and reloads via
    # GroupSetModel.from_legacy_list; keep it populated for parity.
    params.queries = model.to_legacy_list()

    # --- input files ------------------------------------------------------
    params.filename = Path(input_paths['peaktable'])
    params.samplelistfilename = Path(input_paths['samplelist'])
    params.extractmetadatafilename = Path(input_paths['metadata'])
    params.fragfilename = Path(input_paths['fragments']) if input_paths.get('fragments') else ''

    # --- output directory -------------------------------------------------
    # Same shape as the desktop: the chosen directory gets a subdirectory
    # named after the peak table, a plots/ folder inside it, and outputdir
    # then points *at* that subdirectory.
    outputdir = Path(run.output_root)
    (outputdir / params.filename.stem).mkdir(parents=True, exist_ok=True)
    (outputdir / params.filename.stem / 'plots').mkdir(parents=True, exist_ok=True)
    params.outputdir = outputdir / params.filename.stem

    # --- statistics groups + CV parameter ---------------------------------
    params.statstgrps = [str(settings.expgrp), str(settings.ctrgrp)]
    params.cvthresh = float(settings.cvthresh)
    params.cvparam = settings.cvparam

    # --- plot/calculation toggles (the desktop's CHECKBOX_FIELDS) ---------
    params.PCA = bool(settings.PCA)
    params.Dendrogram = bool(settings.Dendrogram)
    params.MZRTplt = bool(settings.MZRTplt)
    params.KMD = bool(settings.KMD)
    params.FC = bool(settings.FC)
    params.FC3Dplt = bool(settings.FC3Dplt)
    params.Ttest = bool(settings.Ttest)
    params.Volcanoplt = bool(settings.Volcanoplt)
    params.FDR = bool(settings.FDR)

    # --- filtering toggles and windows ------------------------------------
    params.relfil = bool(settings.relfil)
    params.CVfil = bool(settings.CVfil)
    params.decon = bool(settings.decon)
    params.merge = bool(settings.merge)
    params.RTwin = float(settings.RTwin)
    params.ringingwin = float(settings.ringingwin)
    params.isopeakwin = float(settings.isopeakwin)
    # The desktop reads the isotope window twice, once into each field -- one
    # control drives both the isotope and dimer windows.
    params.dimerpeakwin = float(settings.isopeakwin)
    params.maxisowin = float(settings.maxisowin)
    # Forced on by the desktop UI (there is no widget for either); the
    # pipeline's group-average and error-propagation steps are what every
    # group-level plot reads.
    params.grpave = True
    params.prperr = True

    # --- blank threshold --------------------------------------------------
    params.blankfilparam = settings.blankfilparam
    params.blankfilthresh = float(settings.blankfilthresh)
    if params.blnkfltr and params.blnkgrp == '':
        params.blnkgrp = 'Blanks'

    params.ppmthresh = float(settings.ppmthresh)
    return params, model


def params_to_json(params, model):
    """A JSON-safe snapshot of the parameters actually used, for the run
    record and for the results view's "Analysis Info" panel."""
    def encode(value):
        if isinstance(value, Path):
            return str(value)
        return value

    skip = {'querydict', 'querylist', 'queries', 'init'}
    snapshot = {key: encode(value) for key, value in vars(params).items()
                if key not in skip}
    snapshot['groupsets'] = [
        {'name': gs.name, 'src': list(gs.src), 'incl': list(gs.incl),
         'excl': list(gs.excl), 'colour': gs.colour}
        for gs in model
    ]
    snapshot['querylist'] = list(params.querylist)
    return json.loads(json.dumps(snapshot, default=str))
