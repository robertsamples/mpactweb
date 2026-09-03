"""Hierarchical clustering dendrogram (web half of ``plotting.plot_dendrogram``).

The desktop's two views, purity colouring and optional bootstrap support are
all preserved, and every number comes from the same vendored modules the
desktop calls: ``ordination.load_ordination_matrix`` for the collapsed
matrix, ``clusterpurity`` for branch colours and the purity headline, and
``pvclust.PvClust`` for AU/BP bootstrap values.

Two things differ from the desktop, both deliberate:

* ``PvClust(..., parallel=False)``. The desktop passes ``parallel=True``; the
  server already isolates each request's heavy work, and a worker pool
  fanning out from inside a request handler competes with other sessions for
  the same cores. The bootstrap result is identical either way.
* Branch colours are computed as an explicit per-link colour array instead of
  scipy's ``link_color_func`` callback, since the client draws the segments.
  The classification itself is ``clusterpurity.purity_link_color_func``,
  called unmodified.
"""

import numpy as np
import pandas as pd
import scipy.cluster.hierarchy as sch
from sklearn.preprocessing import normalize

from .. import config
from ..core import clusterpurity, ordination, pvclust
from ..results import ORDINATION_LOCK
from . import labels as labelutil

COLOR_MODES = ('Purity', 'None')


def _matrix_for_view(results, view):
    """Return ``(data_for_linkage, data_for_pvclust, leaf_names, leaf_labels,
    purity_noun, raw_header)`` for the requested view.

    Mirrors ``plot_dendrogram.plot``'s two branches exactly, including which
    axis gets normalised and which orientation each clustering path needs:
    ``scipy.linkage`` wants objects x variables, ``PvClust`` wants variables x
    objects (it bootstraps rows and transposes internally).
    """
    filtered_path = results.require(results.filtered_path)
    raw_header = results.raw_header()

    if view == labelutil.BIOLOGICAL:
        # Leaves are Samples (technical replicates averaged); purity is judged
        # against Biolgroup.
        with ORDINATION_LOCK:
            x, biolgroup = ordination.load_ordination_matrix(
                filtered_path, raw_header.copy(), collapse_replicates=True)
        scaled = pd.DataFrame(normalize(x.values, axis=1), columns=x.columns, index=x.index)
        leaf_names = scaled.index.tolist()
        leaf_labels = [biolgroup[name] for name in leaf_names]
        return (scaled, scaled.transpose(), leaf_names, leaf_labels,
                'biological groups separable', raw_header)

    # Leaves are Injections; purity is judged against Sample -- i.e. "did each
    # sample's technical replicates cluster together".
    frame = (results.filtered(header=(2,), index_col=0)
             .drop(['m/z', 'Retention time (min)'], axis=1))
    scaled = pd.DataFrame(normalize(frame, axis=0), columns=frame.columns, index=frame.index)
    leaf_names = scaled.columns.tolist()
    sample_of_injection = raw_header.set_index('Injection')['Sample'].to_dict()
    leaf_labels = [sample_of_injection[name] for name in leaf_names]
    return (scaled.transpose(), scaled, leaf_names, leaf_labels,
            "samples' replicates clustered together", raw_header)


def _link_colours(linkage_matrix, leaf_labels, color_mode):
    """Per-link colour, in the same order as ``dendrogram()``'s segments."""
    n_leaves = len(leaf_labels)
    if color_mode != 'Purity' or not linkage_matrix.size:
        return None
    colour_func = clusterpurity.purity_link_color_func(
        linkage_matrix, leaf_labels, true_color='green', false_color='magenta')
    # scipy's dendrogram emits one segment per merge, in the order the merges
    # appear in its own traversal; ``icoord``/``dcoord`` are indexed the same
    # way, so ask the colour function for each internal node in that order.
    dendro = sch.dendrogram(linkage_matrix, no_plot=True)
    # The traversal order of the links matches the order of icoord entries;
    # reconstruct which node each entry corresponds to by matching merge
    # heights, which is what scipy itself does when it colours links.
    order = _link_order(linkage_matrix, dendro)
    return [colour_func(n_leaves + idx) for idx in order]


def _link_order(linkage_matrix, dendro):
    """Map each drawn segment back to its row in the linkage matrix.

    ``dendrogram()`` draws segments in increasing merge height, but ties make
    height alone ambiguous, so the segment's horizontal midpoint is used as a
    tiebreaker: it is determined by the leaf positions under that merge, which
    are unique per node.
    """
    heights = linkage_matrix[:, 2]
    used = set()
    order = []
    for seg_x, seg_y in zip(dendro['icoord'], dendro['dcoord']):
        height = seg_y[1]
        candidates = [i for i in np.argsort(np.abs(heights - height))
                      if i not in used and np.isclose(heights[i], height)]
        order.append(candidates[0] if candidates else 0)
        used.add(order[-1])
    return order


def dendrogram(results, options=None):
    options = options or {}
    view = options.get('view', labelutil.TECHNICAL)
    color_mode = options.get('color_mode', 'Purity')
    bootstrap = bool(options.get('bootstrap', False))
    use_names = bool(options.get('use_sample_names', False))

    (for_linkage, for_pvclust, leaf_names, leaf_labels,
     purity_noun, raw_header) = _matrix_for_view(results, view)

    if len(leaf_names) < 2:
        raise ValueError('a dendrogram needs at least two leaves; '
                         f'this view has {len(leaf_names)}')

    bootstrap_values = None
    if bootstrap:
        pv = pvclust.PvClust(for_pvclust, method='ward', metric='euclidean',
                             nboot=config.PVCLUST_NBOOT, parallel=False)
        linkage_matrix = np.asarray(pv.linkage_matrix, dtype=float)
        # AU (approximately unbiased) and BP (bootstrap probability) per
        # internal node, in linkage-matrix row order -- the desktop annotates
        # the same pair onto each node.
        bootstrap_values = [
            {'au': float(au), 'bp': float(bp)}
            for au, bp in np.asarray(pv.result[['AU', 'BP']], dtype=float)
        ]
    else:
        linkage_matrix = sch.linkage(for_linkage, method='ward')

    dendro = sch.dendrogram(linkage_matrix, no_plot=True)
    ordered = [leaf_names[i] for i in dendro['leaves']]
    display = (labelutil.display_labels(raw_header, ordered, view)
               if use_names else [str(name) for name in ordered])

    payload = {
        'plot': 'dendrogram',
        'view': view,
        'views': [labelutil.TECHNICAL, labelutil.BIOLOGICAL],
        'color_mode': color_mode,
        'color_modes': list(COLOR_MODES),
        'bootstrap': bootstrap,
        'icoord': [[float(v) for v in seg] for seg in dendro['icoord']],
        'dcoord': [[float(v) for v in seg] for seg in dendro['dcoord']],
        'leaves': [str(name) for name in ordered],
        'labels': display,
        'leaf_groups': labelutil.group_of(raw_header, ordered, view),
        'link_colours': _link_colours(linkage_matrix, leaf_labels, color_mode),
        'bootstrap_values': bootstrap_values,
        'link_order': [int(i) for i in _link_order(linkage_matrix, dendro)],
    }

    if color_mode == 'Purity':
        n_pure, n_total = clusterpurity.purity_summary(linkage_matrix, leaf_labels)
        payload['title'] = f'{n_pure}/{n_total} {purity_noun}'
        payload['purity'] = {'pure': int(n_pure), 'total': int(n_total)}
    else:
        payload['title'] = ''
    return payload
