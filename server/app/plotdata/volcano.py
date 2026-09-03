"""Volcano plot data (web half of ``plotting.plot_volcano``).

The desktop classifies each point red / blue / black against live p-q and
fold-change threshold boxes and redraws. Here the raw ``logfc`` and
``-logp``/``-logq`` values go to the browser once and the client
re-classifies as the thresholds move, so dragging a threshold is instant
instead of a round trip -- the classification rule below is still the
desktop's, and is applied server-side too so the returned counts match what
the client will draw.
"""

import numpy as np

from ..results import column


def volcano(results, options=None):
    options = options or {}
    params = results.params()
    if not (params.get('Ttest') and params.get('FC')):
        raise ValueError('the volcano plot needs both Fold Change and T test enabled '
                         'in Analysis Settings')

    # The desktop plots -logq when FDR correction is on and -logp when it is
    # off, and shows a warning label in the latter case.
    use_fdr = bool(params.get('FDR'))
    pqvar = '-logq' if use_fdr else '-logp'

    pqthresh = float(options.get('pqthresh', 0.05))
    fcthresh = float(options.get('fcthresh', 1.5))
    pq_cut = -np.log10(pqthresh) if pqthresh > 0 else np.inf
    fc_cut = np.log2(fcthresh) if fcthresh > 0 else np.inf

    series, max_pq = [], 0.0
    for entry, rows in results.feature_sets():
        if pqvar not in rows.columns:
            raise ValueError(f'{pqvar} was not calculated for this run')
        logfc = column(rows, 'logfc')
        if logfc is None:
            with np.errstate(divide='ignore', invalid='ignore'):
                values = np.log2(rows['fc'].to_numpy(dtype=float))
            logfc = [None if not np.isfinite(v) else float(v) for v in values]

        pq = column(rows, pqvar)
        finite = [v for v in pq if v is not None]
        if finite:
            max_pq = max(max_pq, max(finite))

        # Desktop rule: significant == above the p/q line AND beyond the
        # fold-change line; sign of logfc then splits red (up) from blue
        # (down). Everything else is black.
        fc_array = np.asarray([np.nan if v is None else v for v in logfc], dtype=float)
        pq_array = np.asarray([np.nan if v is None else v for v in pq], dtype=float)
        significant = (pq_array >= pq_cut) & (np.abs(fc_array) >= fc_cut)
        classes = np.where(~significant, 'ns',
                           np.where(fc_array > 0, 'up', 'down'))

        series.append({
            'key': entry['key'],
            'name': entry['name'],
            'colour': entry['colour'],
            'x': logfc,
            'y': pq,
            'ids': column(rows, 'Compound', dtype=str) or [],
            'mz': column(rows, 'm/z'),
            'rt': column(rows, 'Retention time (min)'),
            'classes': [str(c) for c in classes],
            'count': int(len(rows)),
        })

    counts = {'up': 0, 'down': 0, 'ns': 0}
    for item in series:
        for cls in item['classes']:
            counts[cls] = counts.get(cls, 0) + 1

    return {
        'plot': 'volcano',
        'x_label': 'log2 fold change',
        'y_label': f'-log10 {pqvar[-1]}-value',
        'pq_variable': pqvar,
        'fdr': use_fdr,
        'warning': '' if use_fdr else 'False discovery rate correction off',
        'thresholds': {'pq': pqthresh, 'fc': fcthresh,
                       'pq_line': float(pq_cut) if np.isfinite(pq_cut) else None,
                       'fc_line': float(fc_cut) if np.isfinite(fc_cut) else None},
        'class_colours': {'up': '#d62728', 'down': '#1f77b4', 'ns': '#333333'},
        'counts': counts,
        'max_pq': float(max_pq),
        'stats_groups': params.get('statstgrps') or [],
        'series': series,
    }
