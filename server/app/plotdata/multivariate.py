"""Multivariate ordination: PCA / NMDS / PLS-DA (web half of
``plotting.plot_ordination``).

All the maths is ``ordination.py``, vendored unmodified -- ``run_pca``,
``run_nmds`` + ``nmds_loading_proxy``, ``run_plsda``, ``top_loadings`` and
``autoscale`` are called exactly as the desktop calls them, including the
same axis-label formatting (percent variance for PCA/PLS-DA, stress for
NMDS) and the same 2-component loadings ranking.

The scores view's confidence ellipse is computed here rather than drawn:
the desktop's ``plot_point_cov`` draws a 2-sigma covariance ellipse per
group, so the same centre/width/height/angle is returned for the client to
render as an SVG ellipse.
"""

import numpy as np

from ..core import ordination
from ..results import ORDINATION_LOCK, derived, file_key
from . import labels as labelutil

METHODS = ('PCA', 'NMDS', 'PLS-DA')
VIEWS = ('Scores', 'Loadings')

# How replicates are grouped. Named the way the sample-correlation tab names
# them rather than offered as a "collapse" checkbox: a checkbox only says
# what it does if you already know what it collapses, whereas these say what
# you get.
#   Individual Injections -- every injection is its own point, no averaging
#   Biological Replicates -- technical replicates averaged per Sample,
#                            biological replicates kept separate
REPLICATE_VIEWS = (labelutil.INJECTIONS, labelutil.BIOLOGICAL)

# What the Multivariate tab first shows: NMDS of the biological replicates
# (technical replicates averaged). Both stay changeable in the tab.
DEFAULT_METHOD = 'NMDS'
DEFAULT_REPLICATES = labelutil.BIOLOGICAL

# Group colour cycle from ``plot_ordination`` -- kept in the same order so a
# given group gets the same colour it does on the desktop.
GROUP_COLORS = (
    'red', 'blue', 'black', 'grey', 'purple', 'orange', 'green', 'yellow',
    'lime', 'plum', 'teal', 'olivedrab', 'sienna', 'maroon', 'navy',
    'lightcoral', 'darkgoldenrod', 'seagreen', 'lightseagreen', 'aqua',
    'lightsteelblue', 'slateblue', 'blueviolet', 'plum', 'burlywood',
    'salmon', 'aquamarine', 'magenta', 'tan',
)


def _confidence_ellipse(points, nstd=2):
    """2-sigma covariance ellipse for a group's scores.

    Same construction as ``plot_ordination.plot_point_cov``: eigen-decompose
    the 2x2 covariance, sort eigenvalues descending, and size the axes as
    ``2 * nstd * sqrt(eigenvalue)``. Returns None for fewer than 3 points,
    matching the desktop's ``if np.shape(points)[0] > 2`` guard.
    """
    if len(points) <= 2:
        return None
    covariance = np.cov(points, rowvar=False)
    if not np.all(np.isfinite(covariance)):
        return None
    values, vectors = np.linalg.eigh(covariance)
    order = values.argsort()[::-1]
    values, vectors = values[order], vectors[:, order]
    if np.any(values < 0):
        values = np.clip(values, 0, None)
    width, height = 2 * nstd * np.sqrt(values)
    angle = float(np.degrees(np.arctan2(*vectors[:, 0][::-1])))
    centre = points.mean(axis=0)
    return {
        'cx': float(centre[0]), 'cy': float(centre[1]),
        'width': float(width), 'height': float(height), 'angle': angle,
    }


def ordination_plot(results, options=None):
    options = options or {}
    method = options.get('method', DEFAULT_METHOD)
    view = options.get('view', 'Scores')
    # The old boolean is still accepted so a stored option set keeps working.
    replicates = options.get('replicates')
    if replicates is None:
        collapse = options.get('collapse_replicates')
        replicates = (DEFAULT_REPLICATES if collapse is None
                      else labelutil.BIOLOGICAL if collapse else labelutil.INJECTIONS)
    if replicates not in REPLICATE_VIEWS:
        raise ValueError(f'replicates must be one of {", ".join(REPLICATE_VIEWS)}')
    collapse = replicates == labelutil.BIOLOGICAL

    if method not in METHODS:
        raise ValueError(f'unknown ordination method {method!r}')

    filtered_path = results.require(results.filtered_path)
    raw_header = results.raw_header()

    # The fit depends only on the method and how replicates are grouped. The
    # view, the selected feature and the number of loadings shown are all
    # read off the same solution, so it is solved once and kept: without
    # this, switching Scores <-> Loadings or selecting a different feature
    # re-ran the whole NMDS (the slowest thing any plot tab does).
    def solve():
        with ORDINATION_LOCK:
            x, biolgroup = ordination.load_ordination_matrix(
                filtered_path, raw_header.copy(), collapse_replicates=collapse)
        if len(x) < 3:
            raise ValueError(
                f'ordination needs at least three points; "{replicates}" gives {len(x)}. '
                f'Try "{labelutil.INJECTIONS}".')
        # Same component count the desktop uses: as many as the data
        # supports, to a maximum of 10.
        n_components = max(2, min(len(x) - 1, 10))
        title = ''
        if method == 'PCA':
            scores, loadings, expvar = ordination.run_pca(x, n_components)
            axis_labels = [f'PC{i + 1} ({100 * expvar[i]:.1f}%)' for i in range(2)]
        elif method == 'PLS-DA':
            scores, loadings, expvar = ordination.run_plsda(x, biolgroup, n_components)
            axis_labels = [f'PLS{i + 1} ({100 * expvar[i]:.1f}%)' for i in range(2)]
        else:
            scores, expvar, stress = ordination.run_nmds(x, n_components)
            loadings = ordination.nmds_loading_proxy(x, scores)
            axis_labels = ['NMDS1', 'NMDS2']
            title = f'Stress: {stress:.4f}'
        return (len(x), biolgroup, n_components, scores, loadings,
                np.asarray(expvar, dtype=float), axis_labels, title)

    (n_points, biolgroup, n_components, scores, loadings, expvar,
     axis_labels, title) = derived(
        ('ordination', method, collapse) + file_key(filtered_path), solve)

    blank_group = results.blank_group()
    # Colour assignment skips the blank group, as the desktop does -- blanks
    # are plotted only if blank filtering is off.
    group_colours, position = {}, 0
    for value in biolgroup:
        if value not in group_colours and value != blank_group:
            group_colours[value] = GROUP_COLORS[position % len(GROUP_COLORS)]
            position += 1

    payload = {
        'plot': 'ordination',
        'method': method,
        'methods': list(METHODS),
        'view': view,
        'views': list(VIEWS),
        'replicates': replicates,
        'replicate_views': list(REPLICATE_VIEWS),
        'n_points': int(n_points),
        'point_noun': 'samples' if collapse else 'injections',
        'n_components': int(n_components),
        'x_label': axis_labels[0],
        'y_label': axis_labels[1],
        'title': title,
        'explained_variance': [float(v) for v in expvar],
    }

    if view == 'Loadings':
        always = [options['selected_feature']] if options.get('selected_feature') else []
        subset = ordination.top_loadings(loadings.iloc[:, :2],
                                         n=int(options.get('top_n', 25)),
                                         always_include=always)
        limit = (float(subset.iloc[:, :2].abs().values.max()) * 1.2
                 if len(subset) else 1.0)
        payload['loadings'] = [
            {'id': str(feature),
             'x': float(row.iloc[0]),
             'y': float(row.iloc[1])}
            for feature, row in subset.iterrows()
        ]
        payload['range'] = [-limit, limit]
        return payload

    # Scores view: one series per biological group, plus its ellipse.
    scores = scores.copy()
    scores['Biolgroup'] = biolgroup
    labelled = labelutil.display_labels(
        raw_header, list(scores.index),
        labelutil.BIOLOGICAL if collapse else labelutil.TECHNICAL)

    series = []
    for group, colour in group_colours.items():
        subset = scores[scores['Biolgroup'] == group]
        if subset.empty:
            continue
        points = subset.iloc[:, [0, 1]].to_numpy(dtype=float)
        indices = [list(scores.index).index(name) for name in subset.index]
        series.append({
            'name': str(group),
            'colour': colour,
            'x': [float(v) for v in points[:, 0]],
            'y': [float(v) for v in points[:, 1]],
            'ids': [str(name) for name in subset.index],
            'labels': [labelled[i] for i in indices],
            'ellipse': _confidence_ellipse(points, nstd=2),
        })
    payload['series'] = series
    return payload


def samplecorr(results, options=None):
    """Sample-correlation heatmap (web half of ``plotting.plot_samplecorr``).

    ``ordination.similarity_matrix`` does the work for all three methods.
    The 0-1 colour range is the desktop's deliberate choice: real
    sample-to-sample correlations cluster tightly positive, and a -1..1 scale
    would compress all the informative variation into a sliver.
    """
    options = options or {}
    view = options.get('view', labelutil.BIOLOGICAL)
    method = options.get('method', 'Spearman')
    use_names = bool(options.get('use_sample_names', False))

    filtered_path = results.require(results.filtered_path)
    raw_header = results.raw_header()
    with ORDINATION_LOCK:
        x, biolgroup = ordination.load_ordination_matrix(
            filtered_path, raw_header.copy(),
            collapse_replicates=(view != labelutil.INJECTIONS))
    if view == labelutil.GROUPS:
        x = x.groupby(biolgroup).mean()

    matrix = ordination.similarity_matrix(x, method)
    names = [str(c) for c in matrix.columns]
    display = labelutil.display_labels(raw_header, names, view) if use_names else names

    values = matrix.to_numpy(dtype=float)
    return {
        'plot': 'samplecorr',
        'method': method,
        'methods': ['Spearman', 'Jaccard', 'Bray-Curtis'],
        'view': view,
        'views': [labelutil.BIOLOGICAL, labelutil.INJECTIONS, labelutil.GROUPS],
        'colorscheme': options.get('colorscheme', 'rocket'),
        'labels': display,
        'ids': names,
        'groups': labelutil.group_of(raw_header, names, view),
        'z': [[None if not np.isfinite(v) else float(v) for v in row] for row in values],
        'range': [0, 1],
    }
