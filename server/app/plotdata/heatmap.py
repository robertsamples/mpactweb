"""Clustered heatmap data (web half of ``plotting.plot_heatmap``).

The desktop calls ``sns.clustermap(msdata, standard_scale=0,
metric='euclidean', method='ward')``, which does three things: row-scales the
matrix to 0-1, clusters rows and columns with Ward linkage on Euclidean
distance, and draws the reordered matrix with a dendrogram on each axis. Only
the drawing is seaborn-specific -- the rest is scipy, so it is reproduced
here exactly and shipped to the browser as a reordered matrix plus the two
dendrograms.

``standard_scale=0`` is seaborn's "scale each row to [0, 1]"
(``(x - min) / (max - min)`` along axis 0 of the *rows*); replicated below,
including its behaviour on a constant row.
"""

import numpy as np
import scipy.cluster.hierarchy as sch
from scipy.spatial.distance import pdist

from .. import config
from . import labels as labelutil


def _standard_scale_rows(matrix):
    """seaborn's ``standard_scale=0``: min-max each row into [0, 1].

    A constant row has zero range; seaborn's division yields NaN there, which
    matplotlib renders as the colormap's "bad" colour. Zero is used instead so
    the cell still draws -- a flat feature is genuinely at the bottom of its
    own range, and a NaN hole in the matrix would break the client's colour
    scale.
    """
    lo = np.nanmin(matrix, axis=1, keepdims=True)
    hi = np.nanmax(matrix, axis=1, keepdims=True)
    span = hi - lo
    scaled = np.where(span > 0, (matrix - lo) / np.where(span > 0, span, 1.0), 0.0)
    return np.nan_to_num(scaled, nan=0.0)


def _dendrogram_payload(linkage_matrix):
    """scipy dendrogram coordinates in a form the client can draw directly."""
    dendro = sch.dendrogram(linkage_matrix, no_plot=True)
    return {
        'icoord': [[float(v) for v in seg] for seg in dendro['icoord']],
        'dcoord': [[float(v) for v in seg] for seg in dendro['dcoord']],
        'order': [int(i) for i in dendro['leaves']],
    }


def _linkage(data):
    """Ward linkage on Euclidean distance, matching the desktop's clustermap.

    ``pdist`` + ``linkage`` on the condensed distances (rather than passing
    the raw observation matrix) so a single-row/column matrix degrades to an
    empty linkage instead of raising.
    """
    if data.shape[0] < 2:
        return np.empty((0, 4))
    return sch.linkage(pdist(data, metric='euclidean'), method='ward')


def heatmap(results, options=None):
    options = options or {}
    view = options.get('view', labelutil.TECHNICAL)
    use_names = bool(options.get('use_sample_names', False))

    # Feature rows stay in peak-table file order; only columns change with the
    # view -- the same invariant the desktop relies on for its feature<->row
    # navigation.
    frame = results.filtered(header=(2,), index_col=0).iloc[:, 2:]
    raw_header = results.raw_header()

    if view == labelutil.BIOLOGICAL:
        sample_of_injection = raw_header.set_index('Injection')['Sample']
        frame = frame.transpose().groupby(sample_of_injection).mean().transpose()
    elif view == labelutil.GROUPS:
        group_of_injection = raw_header.set_index('Injection')['Biolgroup']
        frame = frame.transpose().groupby(group_of_injection).mean().transpose()

    if frame.size > config.MAX_HEATMAP_CELLS:
        raise ValueError(
            f'{frame.shape[0]} features x {frame.shape[1]} columns exceeds the '
            f'{config.MAX_HEATMAP_CELLS}-cell response limit; tighten the filters')

    raw_columns = [str(c) for c in frame.columns]
    matrix = _standard_scale_rows(frame.to_numpy(dtype=float))

    row_linkage = _linkage(matrix)
    col_linkage = _linkage(matrix.transpose())
    row_dendro = _dendrogram_payload(row_linkage) if row_linkage.size else {
        'icoord': [], 'dcoord': [], 'order': list(range(matrix.shape[0]))}
    col_dendro = _dendrogram_payload(col_linkage) if col_linkage.size else {
        'icoord': [], 'dcoord': [], 'order': list(range(matrix.shape[1]))}

    row_order, col_order = row_dendro['order'], col_dendro['order']
    reordered = matrix[np.ix_(row_order, col_order)]

    feature_ids = [str(v) for v in frame.index]
    display = (labelutil.display_labels(raw_header, raw_columns, view)
               if use_names else raw_columns)

    return {
        'plot': 'heatmap',
        'view': view,
        'views': [labelutil.TECHNICAL, labelutil.BIOLOGICAL, labelutil.GROUPS],
        'colorscheme': options.get('colorscheme', 'rocket'),
        # z is row-major: z[i][j] is the (i-th reordered feature, j-th
        # reordered column) cell, matching the ordered label arrays below.
        'z': [[float(v) for v in row] for row in reordered],
        'features': [feature_ids[i] for i in row_order],
        'columns': [display[j] for j in col_order],
        'column_ids': [raw_columns[j] for j in col_order],
        'groups': labelutil.group_of(raw_header, raw_columns, view),
        'row_dendrogram': row_dendro,
        'col_dendrogram': col_dendro,
        'shape': [int(matrix.shape[0]), int(matrix.shape[1])],
    }
