"""Build the desktop pipeline's ``analysis_parameters`` object from a web request.

This is the web equivalent of ``MainWindow.enumerate_inputs`` in the desktop
``main.py``, and is deliberately written to be read side by side with it: same
order, same derived values, same implicit defaults. The pipeline itself
(``MSFaST.run_MSFaST``) is vendored unmodified, so everything it needs has to
be assembled here exactly as the Qt version assembles it.
"""

import json
from pathlib import Path

from . import ions
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
    level = str(settings.taxon_level or '').lower()
    taxon = str(settings.taxon or '').strip()
    if taxon:
        params.kingdom = taxon if level == 'kingdom' else ''
        params.genus = taxon if level == 'genus' else ''
        params.taxon_level, params.taxon = level, taxon
    else:
        params.kingdom = str(settings.kingdom or '')
        params.genus = str(settings.genus or '')
        params.taxon_level, params.taxon = '', ''

    # --- beta filters (the desktop reads these from adduct_config.txt) ----
    # Each of the isotope and adduct filters takes over a step of the
    # published pipeline while it is on: the isotope filter is the
    # mispicked-peak correction run in its beta mode, and the adduct filter
    # runs in place of in-source deconvolution. The settings of the step it
    # stands in for are kept in the form, so turning it off restores them.
    isotope_filter = bool(settings.relfil_beta)
    adduct_filter = bool(settings.adductgrp)
    relfil = bool(settings.relfil) or isotope_filter
    decon = bool(settings.decon) and not adduct_filter

    params.ionmode = settings.ionmode
    params.mdfil = bool(settings.mdfil)
    params.relfil_mode = 'beta' if isotope_filter else 'legacy'
    params.adductgrp = adduct_filter
    if params.mdfil:
        params.md_halogen = bool(settings.md_halogen)
    if isotope_filter:
        params.iso_ppm = float(settings.iso_ppm)
        params.iso_max_n = int(settings.iso_max_n)
        params.iso_ratio_max = float(settings.iso_ratio_max)
        params.ring_ppm = float(settings.ring_ppm)
        params.ring_ratio_max = float(settings.ring_ratio_max)
        params.dimer_tol = float(settings.dimer_tol)
    if adduct_filter:
        params.adduct_mode = settings.ionmode
        params.adduct_ppm = float(settings.adduct_ppm)
        params.adduct_rt_win = float(settings.adduct_rt_win)
        # (name, m/z difference from the base ion) for the forms of this mode.
        params.adduct_list = ions.grouping_list(settings.ionmode, settings.adducts)
    if isotope_filter or adduct_filter:
        params.corr_cut = float(settings.corr_cut)
        params.corr_p = float(settings.corr_p)
    # The whole selections, both modes: what a re-opened session restores.
    params.adducts = list(settings.adducts)
    params.search_adducts = list(settings.search_adducts)

    # --- graph-level filters, as a token list ----------------------------
    # Features the mass defect and adduct filters remove need no token: the
    # pipeline leaves them out of every feature set itself.
    params.graphfilters = []
    if settings.CVfil:
        params.graphfilters.append('cv')
    if relfil:
        params.graphfilters.append('rel')
    if decon:
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
    # Beta CV sample cutoff and sample normalization (core reads both with
    # getattr defaults, so a run without them is the published pipeline).
    params.cvgate = bool(settings.cvgate) and bool(settings.CVfil)
    params.cvgate_frac = float(settings.cvgate_frac)
    params.samplenorm = settings.samplenorm
    params.samplenorm_skip = [str(g) for g in dict.fromkeys(settings.samplenorm_skip)
                              if str(g) in set(map(str, all_groups))]

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
    params.relfil = relfil
    params.CVfil = bool(settings.CVfil)
    params.decon = decon
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
