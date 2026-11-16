"""Feature scatter plots: m/z vs RT, mass defect, 3D fold change, and the
per-feature quality view from the Data Review tab.

These are the web halves of ``plotting.plot_mzrt``, ``plotting.kendrick``,
``plotting.plot_fc3d`` and ``plotting.show_featureplt``. The desktop bakes
colour, per-point opacity and pick handling into matplotlib artists; here the
same numbers go to the browser as arrays and the client draws them, which is
what makes the points hoverable and the selection shareable across tabs.

One deliberate difference from the desktop: axis ranges are data-driven
rather than hardcoded. The desktop pins m/z-vs-RT to (0, 11.5) x (0, 1850)
and the quality view to (0, 11) x (0, 2000), which silently clips any dataset
with later-eluting or heavier features. The desktop's limits are still sent
as ``desktop_range`` so a client can offer them as a "classic view".
"""

import numpy as np

from .. import config
from ..results import column
from .quality import ADDUCT_COLOUR, MASS_DEFECT_COLOUR


def _series(results, rows, entry, extra=None):
    """One feature set's points, with the fields every scatter tab needs."""
    payload = {
        'key': entry['key'],
        'name': entry['name'],
        'colour': entry['colour'],
        'ids': column(rows, 'Compound', dtype=str) or [],
        'alpha': [float(a) for a in results.opacities(rows)],
        'count': int(len(rows)),
    }
    if extra:
        payload.update(extra)
    return payload


def _guard(series_list):
    """Refuse to stream an unreasonable number of points in one response."""
    total = sum(s['count'] for s in series_list)
    if total > config.MAX_SCATTER_POINTS:
        raise ValueError(
            f'{total} points exceeds the {config.MAX_SCATTER_POINTS}-point response limit; '
            'tighten the filters or narrow the feature sets')
    return series_list


def mzrt(results, options=None):
    """m/z vs retention time, coloured by feature set, opacity by abundance."""
    series = []
    for entry, rows in results.feature_sets():
        series.append(_series(results, rows, entry, {
            'x': column(rows, 'Retention time (min)'),
            'y': column(rows, 'm/z'),
        }))
    return {
        'plot': 'mzrt',
        'x_label': 'Retention time (min)',
        'y_label': 'm/z',
        'series': _guard(series),
        'desktop_range': {'x': [0, 11.5], 'y': [0, 1850]},
    }


# The three mass-defect reference lines drawn by ``plotting.kendrick`` when
# its "Mass Defect Guidelines" control is on. Endpoints copied verbatim.
MASS_DEFECT_GUIDELINES = (
    {'x': [0, 733.314], 'y': [0.00112, 0.8408]},
    {'x': [63.6623, 733.314], 'y': [1, 0.8408]},
    {'x': [0, 465.456], 'y': [0.9884, 0.5408]},
)


def massdefect(results, options=None):
    """Mass defect (``kmd``) vs m/z."""
    options = options or {}
    series = []
    for entry, rows in results.feature_sets():
        series.append(_series(results, rows, entry, {
            'x': column(rows, 'm/z'),
            'y': column(rows, 'kmd'),
        }))
    return {
        'plot': 'massdefect',
        'x_label': 'm/z',
        'y_label': 'Mass Defect',
        'series': _guard(series),
        'y_range': [0, 1],
        'guidelines': [dict(line) for line in MASS_DEFECT_GUIDELINES]
        if options.get('guidelines', True) else [],
    }


def fc3d(results, options=None):
    """3D fold change: log2 FC vs retention time vs m/z."""
    series = []
    for entry, rows in results.feature_sets():
        logfc = column(rows, 'logfc')
        if logfc is None:
            fold = column(rows, 'fc')
            if fold is None:
                raise ValueError('fold change was not calculated for this run '
                                 '(enable Fold Change in Analysis Settings)')
            with np.errstate(divide='ignore', invalid='ignore'):
                values = np.log2(np.asarray([v if v else np.nan for v in fold], dtype=float))
            logfc = [None if not np.isfinite(v) else float(v) for v in values]
        series.append(_series(results, rows, entry, {
            'x': logfc,
            'y': column(rows, 'Retention time (min)'),
            'z': column(rows, 'm/z'),
        }))
    return {
        'plot': 'fc3d',
        'x_label': 'log2 fold change',
        'y_label': 'Retention time (min)',
        'z_label': 'm/z',
        'series': _guard(series),
    }


# Colour/label pairs from ``plotting.show_featureplt``, in its drawing order.
# The desktop assigns each feature a single colour by *last matching* filter
# (the conditionals overwrite in this order: insource, cv, blank, mispicked),
# so a feature failing several filters is attributed to the last one -- kept
# identical here so the counts match the desktop's legend.
QUALITY_CLASSES = (
    ('#0000ff', 'Mispicked'),
    ('#00aaaa', 'Blank features'),
    ('#ff0000', 'Nonreproducable'),
    ('#00aa00', 'In-source ions'),
    ('#000000', 'High Quality'),
)
# The beta filters' classes, drawn only for a run that used them. An adduct
# is marked before the desktop's four, so any of those still wins; a mass
# defect failure is marked last, since it left the data before the others ran.
BETA_QUALITY_CLASSES = (
    ('mdfil', MASS_DEFECT_COLOUR, 'Mass defect (beta)'),
    ('adductgrp', ADDUCT_COLOUR, 'Adducts (beta)'),
)


def quality(results, options=None):
    """Data Review's per-feature quality scatter: every feature, coloured by
    which filter removed it."""
    iondict = results.iondict()
    params = results.params()
    colours = np.full(len(iondict), '#000000', dtype=object)

    def mark(mask, colour):
        colours[np.asarray(mask, dtype=bool)] = colour

    if params.get('adductgrp') and 'pass_adduct' in iondict:
        mark(~iondict['pass_adduct'].fillna(True).astype(bool), ADDUCT_COLOUR)
    if params.get('decon') and 'pass_insource' in iondict:
        mark(~iondict['pass_insource'].fillna(True).astype(bool), '#00aa00')
    if params.get('CVfil'):
        cvparam = params.get('cvparam', 'median CV')
        if cvparam in iondict:
            threshold = float(params.get('cvthresh', 0.2))
            mark(iondict[cvparam].fillna(-np.inf) >= threshold, '#ff0000')
    if params.get('blnkfltr') and 'pass_blnkfil' in iondict:
        mark(~iondict['pass_blnkfil'].fillna(True).astype(bool), '#00aaaa')
    if params.get('relfil') and 'pass_relfil' in iondict:
        mark(~iondict['pass_relfil'].fillna(True).astype(bool), '#0000ff')
    if params.get('mdfil') and 'pass_mdfil' in iondict:
        mark(~iondict['pass_mdfil'].fillna(True).astype(bool), MASS_DEFECT_COLOUR)

    classes = [(colour, label) for flag, colour, label in BETA_QUALITY_CLASSES
               if params.get(flag)] + list(QUALITY_CLASSES)
    iondict['_colour'] = colours
    series = []
    for colour, label in classes:
        rows = iondict[iondict['_colour'] == colour]
        series.append({
            'key': label,
            'name': label,
            'colour': colour,
            'x': column(rows, 'Retention time (min)'),
            'y': column(rows, 'm/z'),
            'ids': column(rows, 'Compound', dtype=str) or [],
            'count': int(len(rows)),
        })
    return {
        'plot': 'quality',
        'x_label': 'Retention time (min)',
        'y_label': 'm/z',
        'series': _guard(series),
        'desktop_range': {'x': [0, 11], 'y': [0, 2000]},
    }
