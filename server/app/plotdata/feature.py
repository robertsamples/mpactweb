"""Feature Info: the searchable feature table, per-feature abundance plot,
MS2 spectrum, and NPAtlas database matches.

Web halves of ``main.fillfttree`` + ``searchtree`` (the table), of
``plotting.plot_abund`` (abundance), of ``plotting.show_spectrum`` +
``getfragdb`` (spectra), and of ``dbsearch.search_npatlas`` (matches).

Table filtering reuses ``treefilters`` verbatim -- the substring / numeric
range / category-token rules the desktop's filter row applies are exactly
the ones applied here, so a filter behaves the same in both apps.
"""

import numpy as np
import pandas as pd

from .. import config
from ..core import treefilters
from ..results import clean

# The desktop's feature-table columns, in ``searchtree.COLUMNS`` order and
# with the same source field for each. 'Sets' and 'Groups' both come from the
# ``groups`` column upstream (the desktop passes it twice); kept as one
# 'groups' field here and rendered in both roles by the client.
TABLE_COLUMNS = (
    {'key': 'Compound', 'label': 'Compound', 'type': 'text'},
    {'key': 'm/z', 'label': 'm/z', 'type': 'number', 'digits': 4},
    {'key': 'Retention time (min)', 'label': 'TR', 'type': 'number', 'digits': 3},
    {'key': 'max', 'label': 'Max', 'type': 'number', 'digits': 0},
    {'key': 'groups', 'label': 'Sets', 'type': 'category'},
    {'key': 'fc', 'label': 'FC', 'type': 'number', 'digits': 3},
    {'key': 'hits', 'label': 'Hits', 'type': 'number', 'digits': 0},
)


def _build_filters(spec):
    """Translate the request's filter spec into ``treefilters`` criteria.

    ``spec`` is ``{column key: {...}}`` where a text filter carries ``text``,
    a numeric filter ``min``/``max``, and a category filter ``allowed``.
    """
    filters = {}
    for index, column in enumerate(TABLE_COLUMNS):
        entry = (spec or {}).get(column['key'])
        if not entry:
            continue
        if column['type'] == 'text' and entry.get('text'):
            filters[index] = treefilters.TextFilter(str(entry['text']))
        elif column['type'] == 'number':
            minimum, maximum = entry.get('min'), entry.get('max')
            if minimum is not None or maximum is not None:
                filters[index] = treefilters.RangeFilter(
                    None if minimum is None else float(minimum),
                    None if maximum is None else float(maximum))
        elif column['type'] == 'category' and entry.get('allowed'):
            filters[index] = treefilters.CategoryFilter(frozenset(entry['allowed']))
    return filters


def feature_table(results, options=None):
    """Paged, filtered, sorted feature table.

    Unlike the desktop -- which loads every row into a Qt model and filters
    in memory -- this pages, because the table has one row per feature and a
    large study can have tens of thousands. Sorting and filtering happen
    server-side over the full set so a page is a true window on the sorted
    result, not just the visible slice re-sorted.
    """
    options = options or {}
    iondict = results.iondict()
    # The desktop shows only searched features (``hits >= 0``). Here the
    # database search is a separate request, so an unsearched run still shows
    # its features -- with 'hits' blank until a search has run.
    if options.get('only_searched') and 'hits' in iondict:
        iondict = iondict[iondict['hits'].notna()]

    rows = [
        tuple(row.get(column['key']) for column in TABLE_COLUMNS)
        for _, row in iondict.iterrows()
    ]
    filters = _build_filters(options.get('filters'))
    if filters:
        rows = [row for row in rows if treefilters.row_passes(row, filters)]

    sort_key = options.get('sort')
    if sort_key:
        keys = [column['key'] for column in TABLE_COLUMNS]
        if sort_key in keys:
            index = keys.index(sort_key)
            numeric = TABLE_COLUMNS[index]['type'] == 'number'

            def sort_value(row):
                value = row[index]
                if numeric:
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        return np.inf
                    return np.inf if np.isnan(number) else number
                return str(value or '').lower()

            rows.sort(key=sort_value, reverse=bool(options.get('descending')))

    total = len(rows)
    offset = max(0, int(options.get('offset', 0)))
    limit = max(1, min(int(options.get('limit', 200)), 2000))
    page = rows[offset:offset + limit]

    return {
        'plot': 'features',
        'columns': [dict(column) for column in TABLE_COLUMNS],
        'rows': [[clean(value) for value in row] for row in page],
        'total': total,
        'offset': offset,
        'limit': limit,
        'categories': treefilters.distinct_category_tokens(
            iondict['groups'].fillna('').tolist()) if 'groups' in iondict else [],
    }


def feature_detail(results, compound):
    """One feature's identity fields, straight out of ``iondict``."""
    iondict = results.iondict()
    match = iondict[iondict['Compound'].astype(str) == str(compound)]
    if match.empty:
        raise ValueError(f'feature {compound!r} is not in this run')
    row = match.iloc[0]
    return {
        'id': str(row['Compound']),
        'fields': {str(key): clean(value) for key, value in row.items()},
    }


def abundance(results, compound):
    """Per-feature abundance: group means with propagated error, plus every
    individual injection (web half of ``plotting.plot_abund``).

    The desktop draws a bar chart of group averages with ``combASD`` error
    bars beside a per-sample strip/point plot; both halves are returned here
    so the client can lay them out the same way.
    """
    filtered = results.filtered(header=(0, 1, 2), index_col=0)
    if str(compound) not in filtered.index.astype(str):
        raise ValueError(f'feature {compound!r} did not pass filtering in this run')

    row = filtered.iloc[:, 2:].loc[str(compound)].reset_index()
    row.columns = ['biolgroup', 'sample', 'injection', 'abundance']

    summary = results.summary().iloc[:, 2:].loc[str(compound)]
    blank_group = results.blank_group()

    def level(name):
        frame = summary.loc[[name]].to_frame()
        frame.index = frame.index.droplevel(level=0)
        return frame.iloc[:, 0]

    averages, comb_asd, neff = level('average'), level('combASD'), level('neff')

    groups = [group for group in averages.index if group != blank_group]
    return {
        'plot': 'abundance',
        'feature': str(compound),
        'groups': [
            {
                'name': str(group),
                'average': clean(averages.get(group)),
                'error': clean(comb_asd.get(group)),
                'neff': clean(neff.get(group)),
            }
            for group in groups
        ],
        'points': [
            {
                'biolgroup': str(record['biolgroup']),
                'sample': str(record['sample']),
                'injection': str(record['injection']),
                'abundance': clean(record['abundance']),
            }
            for record in row.to_dict('records')
            if str(record['biolgroup']) != str(blank_group)
        ],
    }


def spectrum(results, fragdb, compound):
    """MS2 spectrum for one feature from the loaded fragment database.

    ``fragdb`` is ``getfragdb.importfrag``'s ``fragmentation_db``: an ``ions``
    dict of compound id -> ``ion``, whose ``pattern`` is the (m/z, intensity)
    array. Returns empty peak arrays (not an error) when the feature has no
    entry -- most features in a typical run don't.
    """
    entry = getattr(fragdb, 'ions', {}).get(str(compound)) if fragdb else None
    if entry is None:
        return {'plot': 'spectrum', 'feature': str(compound), 'found': False,
                'mz': [], 'intensity': []}
    array = np.asarray(entry.pattern, dtype=float)
    if array.ndim != 2 or array.shape[0] == 0:
        return {'plot': 'spectrum', 'feature': str(compound), 'found': False,
                'mz': [], 'intensity': []}
    return {
        'plot': 'spectrum',
        'feature': str(compound),
        'found': True,
        'mz': [float(v) for v in array[:, 0]],
        'intensity': [float(v) for v in array[:, 1]],
    }


# NPAtlas columns surfaced per match, in the desktop's tree-column order.
ATLAS_FIELDS = (
    ('compound_name', 'Name'),
    ('compound_molecular_formula', 'Formula'),
    ('compound_accurate_mass', 'Accurate mass'),
    ('ppm', 'ppm'),
    ('genus', 'Genus'),
    ('origin_species', 'Species'),
    ('compound_smiles', 'SMILES'),
)


def load_atlas(kingdom='', genus=''):
    """Load NPAtlas, applying the same origin/genus narrowing the desktop
    applies in ``enumerate_inputs``.

    The >3-character test is the desktop's own guard: it treats a short value
    as "nothing selected" rather than a real filter.
    """
    if not config.NPATLAS_PATH.exists():
        raise FileNotFoundError(
            'npatlas.tsv is not installed on this server; run '
            'scripts/fetch_reference_data.py to enable database search')
    atlas = pd.read_csv(config.NPATLAS_PATH, sep='\t', header=0, index_col=1)
    if len(str(kingdom or '')) > 3:
        atlas = atlas[atlas['origin_type'] == kingdom]
    if len(str(genus or '')) > 3:
        atlas = atlas[atlas['genus'] == genus]
    return atlas


def matches_for_mass(atlas, mass, ppm_threshold):
    """Protonated/sodiated adduct matches for one mass.

    Same two-adduct ppm test and ppm sort as ``main.runsearch`` and
    ``dbsearch.search_npatlas``.
    """
    hits = []
    for column in ('compound_m_plus_h', 'compound_m_plus_na'):
        values = atlas[column].to_numpy(dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            ppm = np.abs(1e6 * (values - float(mass)) / values)
        selected = atlas[ppm < float(ppm_threshold)].copy()
        selected['ppm'] = ppm[ppm < float(ppm_threshold)]
        hits.append(selected)
    combined = pd.concat(hits).sort_values(by=['ppm'])
    return [
        {key: clean(row.get(key)) for key, _ in ATLAS_FIELDS}
        for _, row in combined.iterrows()
    ]


def render_structure(smiles):
    """A 2D structure SVG for a SMILES string, via epam.indigo.

    Optional: the server reports structures as unavailable rather than
    failing if indigo (a native dependency) isn't installed.
    """
    from ..core import Indigo, IndigoRenderer, indigo_available
    if not indigo_available:
        raise RuntimeError('structure rendering is unavailable on this server '
                           '(epam.indigo is not installed)')
    indigo = Indigo()
    renderer = IndigoRenderer(indigo)
    indigo.setOption('render-output-format', 'svg')
    indigo.setOption('render-background-color', '1,1,1')
    indigo.setOption('render-coloring', True)
    molecule = indigo.loadMolecule(str(smiles))
    molecule.layout()
    return bytes(renderer.renderToBuffer(molecule))
