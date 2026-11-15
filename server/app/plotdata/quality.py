"""Data Review tab: CV rarefaction curve, quality scores, and the filter
treemap / feature-set UpSet views.

Web halves of ``plotting.prev_cv``, ``plotting.plot_treemap`` and
``plotting.plot_upset``. The CV metrics come straight from the vendored
``qualityscore.compute_cv_quality``; the treemap and UpSet set-membership
counts are re-derived here the same way the desktop derives them, then laid
out client-side (``squarify``/``upsetplot`` are matplotlib renderers, and
their layout maths is small enough to do in the browser -- see
``web/src/plots/treemap.js``).
"""

import numpy as np
import pandas as pd

from ..core import qualityscore


def cvplot(results, options=None):
    """CV rarefaction curves plus the Reproducibility / Skewness / Overall
    scores shown beside the Data Review tab."""
    params = results.params()
    if not params.get('CVfil'):
        raise ValueError('the CV plot needs CV filtering enabled in Filtering Settings')

    iondict = results.load_iondict_indexed()
    header = results.raw_header()
    # Average injections per sample: the count-statistics noise model that
    # sets the CV axis scale.
    average_n = header['Injection'].nunique() / max(1, header['Sample'].nunique())

    result = qualityscore.compute_cv_quality(iondict, average_n)
    mean_curve, med_curve = result.iondictmean, result.iondictmed

    cvparam = params.get('cvparam', 'median CV')
    cvthresh = float(params.get('cvthresh', 0.2))
    # Percentage of features at the CV threshold: the horizontal guide line
    # the desktop draws, found by nearest CV value in the active ordering.
    threshold_percent = None
    if cvparam in med_curve.columns:
        nearest = (med_curve[cvparam] - cvthresh).abs().argsort()[:1]
        if len(nearest):
            threshold_percent = float(med_curve.iloc[nearest, 0].iloc[0])

    return {
        'plot': 'cvplot',
        'x_label': 'CV',
        'y_label': 'Percentage of Features',
        'x_range': [0, float(result.modelstdev)],
        'y_range': [0, 100],
        'series': [
            {'name': 'Median CV', 'colour': '#0000ff',
             'x': [float(v) for v in med_curve['median CV']],
             'y': [float(v) for v in med_curve.iloc[:, 0]]},
            {'name': 'Mean CV', 'colour': '#ff0000',
             'x': [float(v) for v in mean_curve['average CV']],
             # The desktop pairs the mean-CV x values with the *median* curve's
             # cumulative percentages; both orderings produce the same 0-100
             # ramp of identical length, so the curve is the same either way.
             'y': [float(v) for v in med_curve.iloc[:, 0]]},
        ],
        'threshold': {'cv': cvthresh, 'percent': threshold_percent, 'param': cvparam},
        'scores': {
            'reproducibility': float(100 * result.rep),
            'skewness': float(100 * result.sumskew),
            'overall': float(result.qualscore),
        },
        'model_stdev': float(result.modelstdev),
    }


# Filter classes in the desktop's treemap order, with its colours. The order
# matters: each entry's count is "features lost at this step", computed
# against the running total left by the previous steps.
TREEMAP_STEPS = (
    ('relfil', 'Mispicked', '#0000ff'),
    ('blnkfltr', 'Blank', '#00aaaa'),
    ('CVfil', 'Nonreproducible', '#ff0000'),
    ('decon', 'Insource', '#00aa00'),
)


def treemap(results, options=None):
    """How many features each enabled filter removed."""
    params = results.params()
    meta = results.meta()
    iondict = results.iondict(copy=False)

    counts, colours = {}, []
    current = len(iondict.index)

    if params.get('relfil') and 'pass_relfil' in iondict:
        remaining = int(iondict['pass_relfil'].fillna(False).astype(bool).sum())
        counts['Mispicked'] = current - remaining
        current = remaining
        colours.append('#0000ff')
    if params.get('blnkfltr') and 'pass_blnkfil' in iondict:
        remaining = int(iondict['pass_blnkfil'].fillna(False).astype(bool).sum())
        counts['Blank'] = current - remaining
        current = remaining
        colours.append('#00aaaa')
    if params.get('CVfil'):
        counts['Nonreproducible'] = int(meta.get('filter_counts', {}).get('cv', 0))
        current -= counts['Nonreproducible']
        colours.append('#ff0000')
    if params.get('decon'):
        counts['Insource'] = int(meta.get('filter_counts', {}).get('insource', 0))
        colours.append('#00aa00')

    # One row of the filtered table per surviving feature; counted off the
    # cached id list rather than by parsing the table under yet another
    # header shape.
    counts['High Quality'] = len(results.filtered_ids())
    colours.append('#000000')

    total = sum(counts.values()) or 1
    return {
        'plot': 'treemap',
        'items': [
            {'label': label, 'value': int(value), 'colour': colour,
             'percent': round(100 * value / total, 1)}
            for (label, value), colour in zip(counts.items(), colours)
        ],
        'total': int(total),
    }


def upset(results, options=None):
    """Feature counts per combination of biological groups.

    Reproduces ``plot_upset``'s derivation: apply every enabled filter to
    ``iondict``, tally the distinct ``groups`` strings, and expand each into
    booleans per biological group. The space prefix on comparisons is what
    keeps one group name that is a substring of another from matching.
    """
    params = results.params()
    iondict = results.iondict()
    for flag, column in (('relfil', 'pass_relfil'), ('decon', 'pass_insource'),
                         ('blnkfltr', 'pass_blnkfil'), ('CVfil', 'pass_cvfil')):
        if params.get(flag) and column in iondict:
            iondict = iondict[iondict[column].fillna(False).astype(bool)]

    frequency = {}
    for value in iondict['groups'].fillna('').tolist():
        frequency[value] = frequency.get(value, 0) + 1

    biolgroups = results.biolgroups()
    rows = []
    for combination, size in frequency.items():
        token = ' ' + str(combination)
        members = [group for group in biolgroups if (' ' + group) in token]
        rows.append({'groups': members, 'size': int(size)})

    # Largest intersections first -- the conventional UpSet ordering, and the
    # only sort that stays readable once there are more than a handful.
    rows.sort(key=lambda row: (-row['size'], len(row['groups'])))
    totals = {group: 0 for group in biolgroups}
    for row in rows:
        for group in row['groups']:
            totals[group] += row['size']

    grand_total = sum(row['size'] for row in rows) or 1
    for row in rows:
        row['percent'] = round(100 * row['size'] / grand_total, 1)

    return {
        'plot': 'upset',
        'categories': biolgroups,
        'totals': totals,
        'intersections': rows,
        'total': int(grand_total),
    }


def summary(results, options=None):
    """The Summary tab: run-level counts, filter yields and quality scores."""
    params = results.params()
    meta = results.meta()
    iondict = results.iondict(copy=False)
    header = results.raw_header()

    total_features = int(len(iondict.index))
    surviving = len(results.filtered_ids())
    filter_counts = meta.get('filter_counts', {})

    def yield_row(label, count):
        return {
            'label': label,
            'count': int(count),
            'total': total_features,
            'percent': round(100 * count / total_features, 2) if total_features else 0.0,
        }

    filters = []
    if params.get('relfil'):
        filters.append(yield_row('Failing peak correction filtering',
                                 filter_counts.get('relfil', 0)))
    if params.get('blnkfltr'):
        filters.append(yield_row('Failing blank filtering',
                                 len(meta.get('blank_ions', []))))
    if params.get('decon'):
        filters.append(yield_row('Failing in-source/deconvolution filtering',
                                 filter_counts.get('insource', 0)))
    if params.get('CVfil'):
        filters.append(yield_row('Failing CV filtering', filter_counts.get('cv', 0)))
    filters.append(yield_row('Failing any filter', total_features - surviving))
    filters.append(yield_row('Passing all filters', surviving))

    scores = None
    if params.get('CVfil'):
        try:
            scores = cvplot(results)['scores']
        except Exception:  # noqa: BLE001 - the summary must render regardless
            scores = None

    return {
        'plot': 'summary',
        'counts': {
            'features_total': total_features,
            'features_passing': surviving,
            'injections': int(header['Injection'].nunique()),
            'samples': int(header['Sample'].nunique()),
            'biological_groups': int(header['Biolgroup'].nunique()),
        },
        'filters': filters,
        'scores': scores,
        'groups': results.biolgroups(),
        'blank_group': results.blank_group(),
        'stats_groups': params.get('statstgrps') or [],
        'feature_sets': [
            {'name': entry['name'], 'colour': entry['colour'],
             'count': int(len(rows))}
            for entry, rows in results.feature_sets()
        ],
        'analysisinfo': results.analysisinfo(),
    }


def group_averages(results, options=None):
    """Long-format group averages, for the Summary tab's per-group table."""
    frame = results.groupaverages()
    numeric = frame.select_dtypes(include=[np.number])
    return {
        'plot': 'groupaverages',
        'columns': [str(c) for c in frame.columns],
        'rows': int(len(frame)),
        'describe': {
            str(column): {
                stat: (None if not np.isfinite(value) else float(value))
                for stat, value in pd.Series(numeric[column]).describe().items()
            }
            for column in numeric.columns
        },
    }
