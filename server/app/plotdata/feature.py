"""Feature Info: the searchable feature table, per-feature abundance plot,
MS2 spectrum, and NPAtlas database matches.

Web halves of ``main.fillfttree`` + ``searchtree`` (the table), of
``plotting.plot_abund`` (abundance), of ``plotting.show_spectrum`` +
``getfragdb`` (spectra), and of ``dbsearch.search_npatlas`` (matches).

Table filtering reuses ``treefilters`` verbatim -- the substring / numeric
range / category-token rules the desktop's filter row applies are exactly
the ones applied here, so a filter behaves the same in both apps.
"""

import json

import numpy as np
import pandas as pd

from .. import config
from ..core import treefilters
from ..results import Memo, clean, file_key

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
    {'key': 'hits', 'label': 'MS1 hits', 'type': 'number', 'digits': 0},
    {'key': 'msms_hits', 'label': 'MS2 hits', 'type': 'number', 'digits': 0},
)


# Hard ceiling on rows in one response. A 5,000-feature study is ~700KB of
# JSON, which is fine; this only stops a pathological table from being sent
# in one piece.
MAX_ROWS = 100_000
# Ids in one by-id request: a screenful plus what is fetched ahead of it.
MAX_ROWS_FOR = 2_000


def _reverse_text(text):
    """Sort key that reverses lexicographic order for strings.

    Python can't negate a string the way it negates a number, so descending
    text order is expressed by inverting each code point.
    """
    return [-ord(ch) for ch in text]


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
        elif column['type'] == 'category':
            required = frozenset(entry.get('required') or ())
            excluded = frozenset(entry.get('excluded') or ())
            if required or excluded:
                filters[index] = SetFilter(required, excluded)
            elif entry.get('allowed'):
                filters[index] = treefilters.CategoryFilter(frozenset(entry['allowed']))
    return filters


_NOT_FINITE = (float('inf'), float('-inf'))


def _cells(series):
    """One table column as JSON-safe values, blanks as ``None``.

    Done once per column, before filtering and sorting, rather than once per
    cell on the way out. It also settles what a blank *is* for the filters:
    pandas reads an empty numeric cell as NaN, which is a float, and every
    comparison with NaN is false -- so a NaN satisfied ``RangeFilter`` and a
    filter like "MS2 hits >= 1" kept every feature that had never been
    searched. As ``None`` it is what that filter's rule says it should be: not
    a number, and so never in range.
    """
    values = series.tolist()
    kind = series.dtype.kind
    if kind == 'f':
        return [None if (value != value or value in _NOT_FINITE) else value
                for value in values]
    if kind in 'iub':
        return values
    return [value if isinstance(value, str) else clean(value) for value in values]


class SetFilter:
    """Every ``required`` token present and no ``excluded`` token."""

    def __init__(self, required, excluded):
        self.required, self.excluded = required, excluded

    def matches(self, value):
        tokens = set(str(value or '').split())
        return self.required <= tokens and not (self.excluded & tokens)


# The filtered, sorted table for a run and a set of options. Building it is
# the expensive part of a table request; the browser then asks for it a
# screenful at a time as the list is scrolled, and each of those requests is
# a slice of the same view.
_VIEWS = Memo(limit=12)

# Options that decide which rows are in the view and in what order. Paging
# (offset, limit, with_ids) only chooses the slice returned.
VIEW_OPTIONS = ('filters', 'sort', 'descending', 'include_filtered', 'hit_filter')


def _stamp(path):
    try:
        return file_key(path)
    except OSError:
        return None


def _view(results, options, ms2_ids=None, ms2_stamp=None):
    """The whole view: ``rows`` (every row, in order), ``ids`` and the flags
    the table header needs. Shared between requests: read, never modified.

    ``ms2_stamp`` identifies the fragment database ``ms2_ids`` came from; a
    restriction without one cannot be told apart from another, so it is built
    fresh rather than cached.
    """
    from . import hits, msms
    if not options.get('hit_filter') and atlas_available():
        hits.prepare(results)
    if ms2_ids is not None and ms2_stamp is None:
        return _build_view(results, options, ms2_ids)
    key = ('feature-view', file_key(results.iondict_path), _stamp(results.filtered_path),
           _stamp(results.outputdir / msms.RESULTS_FILE), _stamp(config.MSMS_LIBRARY_PATH),
           _atlas_key(),
           json.dumps({name: options.get(name) for name in VIEW_OPTIONS},
                      sort_keys=True, default=str),
           ms2_stamp if ms2_ids is not None else None)
    return _VIEWS.get(key, lambda: _build_view(results, options, ms2_ids))


def _build_view(results, options, ms2_ids):
    """Filter and sort the whole feature table.

    Sorting and filtering happen server-side over the whole set, so the list
    the client holds (every id, in order) is complete and correctly ordered
    whichever rows it has fetched.

    ``include_filtered`` (default false) is the "show filtered-out features"
    switch. The desktop's tree lists only features that survived filtering --
    it populates from ``hits >= 0``, and only surviving features are ever
    searched -- so that is the default here too. The authority for "survived"
    is the filtered peak table's own index rather than re-deriving it from the
    ``pass_*`` columns, which would have to re-implement which filters were
    enabled for this run.
    """
    # Read-only here: rows are selected with a mask, never written back.
    iondict = results.iondict(copy=False)
    compounds = iondict['Compound'].astype(str)
    keep = np.ones(len(iondict), dtype=bool)

    if not options.get('include_filtered', False):
        try:
            keep &= compounds.isin(results.surviving()).to_numpy()
        except Exception:  # noqa: BLE001 - an unreadable filtered table shouldn't
            pass          # empty the whole view; fall back to showing everything

    if ms2_ids is not None:
        keep &= compounds.isin(ms2_ids).to_numpy()

    from . import msms
    msms_counts = msms.counts(results) if msms.available() else None

    from . import hits
    hit_spec = None
    if options.get('hit_filter'):
        hit_spec = hits.normalise(options['hit_filter'])
        if hit_spec:
            passing = hits.passing_features(results, hit_spec, set(compounds[keep]))
            keep &= compounds.isin(passing).to_numpy()

    iondict = iondict[keep]
    compounds = compounds[keep]

    # Column by column rather than ``iterrows``: that builds a Series per
    # feature, which is most of the time this table takes on a large run.
    cells = []
    for column in TABLE_COLUMNS:
        key = column['key']
        if key == 'msms_hits':
            values = _cells(compounds.map(msms_counts or {}))
        elif key in iondict.columns:
            values = _cells(iondict[key])
        else:
            values = [None] * len(iondict)
        cells.append(values)
    rows = list(zip(*cells))
    filters = _build_filters(options.get('filters'))
    if filters:
        rows = [row for row in rows if treefilters.row_passes(row, filters)]

    sort_key = options.get('sort')
    descending = bool(options.get('descending'))
    if sort_key:
        keys = [column['key'] for column in TABLE_COLUMNS]
        if sort_key not in keys:
            raise ValueError(f'cannot sort by {sort_key!r}; expected one of {", ".join(keys)}')
        index = keys.index(sort_key)
        numeric = TABLE_COLUMNS[index]['type'] == 'number'

        # Blanks always sort last, in both directions. Mapping them to +inf
        # (the obvious shortcut) parks every unsearched feature at the *top*
        # of a descending sort -- which is exactly how "sort by Hits" came to
        # show nothing but "--" and looked like a broken database search.
        def sort_key_for(row):
            value = row[index]
            if numeric:
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    return (1, 0.0)
                if np.isnan(number):
                    return (1, 0.0)
                return (0, -number if descending else number)
            text = '' if value is None else str(value)
            if not text:
                return (1, '')
            return (0, _reverse_text(text.lower()) if descending else text.lower())

        # Sorting ascending on an already-direction-aware key keeps the
        # blanks-last group in place regardless of direction.
        rows.sort(key=sort_key_for)

    return {
        'rows': rows,
        'ids': [row[0] for row in rows],
        'hit_spec': hit_spec,
        'msms_searched': msms_counts is not None,
        'searched': bool('hits' in iondict.columns and iondict['hits'].notna().any()),
        'categories': treefilters.distinct_category_tokens(
            iondict['groups'].fillna('').tolist()) if 'groups' in iondict else [],
    }


def feature_table(results, options=None, ms2_ids=None, ms2_stamp=None):
    """One page of the feature table, filtered and sorted.

    ``offset``/``limit`` choose the rows returned; ``limit`` 0 (the default)
    means every row. ``rows_for`` (a list of ids) returns those features'
    rows instead, in the order asked. ``with_ids`` adds ``ids``, every compound id in the view
    in order -- what the browser needs to size the list, walk it with the
    keyboard and select ranges in it without holding every row. It asks for
    that once, with the first page, and for further pages as they scroll
    into view.
    """
    options = options or {}
    view = _view(results, options, ms2_ids, ms2_stamp)
    rows = view['rows']

    total = len(rows)
    wanted = options.get('rows_for')
    if wanted is not None:
        # Rows by id rather than by position: the browser can put the list in
        # an order of its own (selected or flagged features first), and then
        # the rows on screen are not a contiguous slice of this one.
        if not isinstance(wanted, list) or len(wanted) > MAX_ROWS_FOR:
            raise ValueError(f'rows_for must be a list of at most {MAX_ROWS_FOR} feature ids')
        by_id = view.get('by_id')
        if by_id is None:
            by_id = view['by_id'] = {row[0]: row for row in rows}
        page = [by_id[compound] for compound in wanted if compound in by_id]
        offset, limit = 0, len(page)
    else:
        offset = max(0, int(options.get('offset', 0) or 0))
        # The ceiling is a transport guard, not a page size.
        requested = int(options.get('limit', 0) or 0)
        limit = total if requested <= 0 else requested
        limit = min(limit, MAX_ROWS)
        page = rows[offset:offset + limit]

    table = {
        'plot': 'features',
        'columns': [dict(column) for column in TABLE_COLUMNS],
        'rows': page,
        'total': total,
        'offset': offset,
        'limit': limit,
        'include_filtered': bool(options.get('include_filtered', False)),
        'hit_filter': bool(view['hit_spec']),
        'msms_searched': view['msms_searched'],
        'searched': view['searched'],
        'categories': view['categories'],
    }
    if options.get('with_ids'):
        table['ids'] = view['ids']
    return table


# Columns added to an export after the run's own per-feature columns.
EXPORT_MS1 = ('ms1_top_match', 'ms1_top_formula', 'ms1_top_ppm', 'ms1_top_organism')
EXPORT_MS2 = ('ms2_hits', 'ms2_top_match', 'ms2_top_score')


def export_table(results, options=None, ms2_ids=None, ms2_stamp=None, ids=None):
    """``(header, rows)`` for a CSV of the feature table.

    The rows are the view the options describe -- the same filters and the
    same order as the table on screen -- or, with ``ids``, just those
    features. Every per-feature column the run produced is included
    (``iondict.csv``'s), then the feature's flag colour when the run has
    flags, followed by the best database match for each
    feature: the closest NPAtlas mass match and the highest-scoring MS/MS
    library match, among those meeting the hit filter when one is active.
    """
    from . import hits, msms
    options = options or {}
    view = _view(results, options, ms2_ids, ms2_stamp)
    order = [str(compound) for compound in view['ids']]
    if ids is not None:
        wanted = {str(compound) for compound in ids}
        order = [compound for compound in order if compound in wanted]

    positions = results.feature_positions()
    order = [compound for compound in order if compound in positions]
    frame = results.iondict(copy=False).iloc[[positions[compound] for compound in order]]

    header = [str(name) for name in frame.columns]
    columns = [_cells(frame[name]) for name in frame.columns]

    flags = results.flags()
    if flags:
        # The flag's colour, so a spreadsheet can be sorted or filtered by it.
        header.append('flag')
        columns.append([flags.get(compound) for compound in order])

    spec = view['hit_spec']
    if atlas_available():
        best = hits.top_matches(results, order, spec)
        header += EXPORT_MS1
        for slot in range(len(EXPORT_MS1)):
            columns.append([best[compound][slot] if compound in best else None
                            for compound in order])

    if msms.available() and msms.stored(results) is not None:
        counts = msms.counts(results) or {}
        best = hits.top_library_matches(results, order, spec)
        header += EXPORT_MS2
        columns.append([counts.get(compound) for compound in order])
        for slot in range(2):
            columns.append([best[compound][slot] if compound in best else None
                            for compound in order])

    return header, list(zip(*columns)) if columns else []


class UnknownFeature(ValueError):
    """The requested compound id is not in this run's ``iondict``."""


def feature_detail(results, compound):
    """One feature's identity fields, straight out of ``iondict``."""
    iondict = results.iondict(copy=False)
    position = results.feature_positions().get(str(compound))
    if position is None:
        raise UnknownFeature(f'feature {compound!r} is not in this run')
    row = iondict.iloc[position]
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
    if str(compound) not in results.surviving():
        # Not an error: only filtered features have per-injection abundances,
        # and most features in a run don't survive filtering.
        return {'plot': 'abundance', 'feature': str(compound), 'found': False,
                'reason': 'This feature did not pass filtering, so it has no '
                          'per-injection abundances.',
                'groups': [], 'points': []}

    filtered = results.filtered(header=(0, 1, 2), index_col=0, copy=False)
    row = filtered.iloc[:, 2:].loc[str(compound)].reset_index()
    row.columns = ['biolgroup', 'sample', 'injection', 'abundance']

    summary = results.summary(copy=False).iloc[:, 2:].loc[str(compound)]
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
        'found': True,
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
    ('source_database', 'Database'),
)


def atlas_available():
    return config.NPATLAS_PATH.exists() or config.NPATLAS_SUPPLEMENTAL_PATH.exists()


def combined_atlas():
    """NPAtlas plus the supplemental table, as one frame indexed by
    compound_id. The supplemental table holds only compounds NPAtlas lacks
    and uses ids from 9,000,001, so the two never collide."""
    frames = []
    for path, source in ((config.NPATLAS_PATH, 'NPAtlas'),
                         (config.NPATLAS_SUPPLEMENTAL_PATH, None)):
        if not path.exists():
            continue
        frame = pd.read_csv(path, sep='\t', header=0, index_col=1)
        if source:
            frame['source_database'] = source
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(
            'npatlas.tsv is not installed on this server; run '
            'scripts/fetch_reference_data.py to enable database search')
    return pd.concat(frames) if len(frames) > 1 else frames[0]


# The columns the web layer and ``dbsearch.search_npatlas`` read. The other
# twenty-odd (reference lists, DOIs, InChI strings) are most of the table's
# memory and are never looked at here.
ATLAS_COLUMNS = (
    'compound_name', 'compound_molecular_formula', 'compound_accurate_mass',
    'compound_m_plus_h', 'compound_m_plus_na', 'compound_inchikey',
    'compound_smiles', 'origin_type', 'genus', 'origin_species',
    'source_database', 'synonyms',
)

_ATLAS = Memo(limit=1)


def _atlas_key():
    def stamp(path):
        try:
            stat = path.stat()
            return (str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            return (str(path), None, None)
    return (stamp(config.NPATLAS_PATH), stamp(config.NPATLAS_SUPPLEMENTAL_PATH))


def shared_atlas():
    """``combined_atlas`` cut down to ``ATLAS_COLUMNS`` and kept in memory.

    Parsing the 33MB table took longer than everything else a match lookup
    does put together, and it was parsed again for every feature clicked.
    The frame is shared between requests: treat it as read-only (every caller
    here selects rows into a new frame). A re-downloaded atlas has a new
    mtime and is picked up on the next call.
    """
    def build():
        atlas = combined_atlas()
        return atlas[[name for name in ATLAS_COLUMNS if name in atlas.columns]].copy()
    return _ATLAS.get(_atlas_key(), build)


def load_atlas(kingdom='', genus='', taxon_level='', taxon=''):
    """Load NPAtlas, applying the same origin/genus narrowing the desktop
    applies in ``enumerate_inputs``, or the web's any-rank ``taxon``.

    The >3-character test is the desktop's own guard: it treats a short value
    as "nothing selected" rather than a real filter.
    """
    atlas = shared_atlas()
    taxon = str(taxon or '').strip()
    if taxon:
        return _narrow_by_taxon(atlas, str(taxon_level or '').lower(), taxon)
    if len(str(kingdom or '')) > 3:
        atlas = atlas[atlas['origin_type'] == kingdom]
    if len(str(genus or '')) > 3:
        atlas = atlas[atlas['genus'] == genus]
    return atlas


def atlas_for(params):
    """The atlas narrowed by a run's parameters."""
    return load_atlas(params.get('kingdom', ''), params.get('genus', ''),
                      params.get('taxon_level', ''), params.get('taxon', ''))


def _narrow_by_taxon(atlas, level, taxon):
    value = taxon.lower()
    genus = atlas['genus'].fillna('').astype(str)
    if level == 'kingdom':
        keep = atlas['origin_type'].fillna('').astype(str).str.lower() == value
    elif level == 'genus':
        keep = genus.str.lower() == value
    elif level == 'species':
        species = (genus + ' ' + atlas['origin_species'].fillna('').astype(str)).str.strip().str.lower()
        keep = (species == value) | species.str.startswith(value + ' ')
    elif level in ('phylum', 'class', 'order', 'family'):
        from .hits import _lineage
        lineage = _lineage()
        if lineage is None:
            raise FileNotFoundError('npatlas_taxonomy.tsv is not installed on this server')
        rows = lineage[lineage[level].str.lower() == value]
        pairs = set(zip(rows['origin_type'], rows['genus']))
        keep = pd.Series([(o, g) in pairs for o, g in
                          zip(atlas['origin_type'].fillna(''), genus)], index=atlas.index)
    else:
        raise ValueError(f'unknown taxonomic level {level!r}')
    return atlas[keep.to_numpy()]


def matches_for_mass(atlas, mass, ppm_threshold):
    """Protonated/sodiated adduct matches for one mass.

    Same two-adduct ppm test and ppm sort as ``main.runsearch`` and
    ``dbsearch.search_npatlas``. ``adduct`` says which of the two a match is.
    """
    hits = []
    for column, adduct in (('compound_m_plus_h', '[M+H]+'), ('compound_m_plus_na', '[M+Na]+')):
        values = atlas[column].to_numpy(dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            ppm = np.abs(1e6 * (values - float(mass)) / values)
        selected = atlas[ppm < float(ppm_threshold)].copy()
        selected['ppm'] = ppm[ppm < float(ppm_threshold)]
        selected['adduct'] = adduct
        hits.append(selected)
    combined = pd.concat(hits).sort_values(by=['ppm'])
    return [
        {'compound_id': clean(index), 'adduct': row['adduct'],
         **{key: clean(row.get(key)) for key, _ in ATLAS_FIELDS}}
        for index, row in combined.iterrows()
    ]


def render_structure(smiles):
    """A 2D structure SVG for a SMILES string.

    RDKit with CoordGen coordinates when available, epam.indigo otherwise.
    Optional: the server reports structures as unavailable rather than
    failing if neither (both are native dependencies) is installed.
    """
    from ..core import indigo_available, rdkit_available
    if rdkit_available:
        return _render_rdkit(smiles)
    if not indigo_available:
        raise RuntimeError('structure rendering is unavailable on this server '
                           '(neither rdkit nor epam.indigo is installed)')
    return _render_indigo(smiles)


def _render_rdkit(smiles, size=(420, 320)):
    """CoordGen layout rather than RDKit's default depictor.

    Natural products are often macrocycles, and the default depiction (like
    Indigo's) lays a large ring out as a near-circle of almost straight bonds.
    CoordGen draws the same ring as a polygon at ~120 degree angles, the way a
    chemist would draw it.
    """
    import numpy as np
    from rdkit import Chem, RDLogger
    from rdkit.Chem import rdCoordGen
    from rdkit.Chem.Draw import rdMolDraw2D
    from rdkit.Geometry import Point3D

    from .hits import standardize
    RDLogger.DisableLog('rdApp.*')
    molecule = standardize(Chem.MolFromSmiles(str(smiles)))
    if molecule is None:
        raise ValueError('unparseable SMILES')
    rdCoordGen.AddCoords(molecule)

    # Longest axis horizontal, so a structure fills the wide detail panel
    # instead of standing on end.
    conformer = molecule.GetConformer()
    count = molecule.GetNumAtoms()
    if count > 2:
        points = np.array([[conformer.GetAtomPosition(i).x, conformer.GetAtomPosition(i).y]
                           for i in range(count)])
        points -= points.mean(axis=0)
        _, _, vt = np.linalg.svd(points, full_matrices=False)
        angle = np.arctan2(vt[0, 1], vt[0, 0])
        cos, sin = np.cos(-angle), np.sin(-angle)
        points = points @ np.array([[cos, sin], [-sin, cos]])
        for i, (x, y) in enumerate(points):
            conformer.SetAtomPosition(i, Point3D(float(x), float(y), 0.0))

    drawer = rdMolDraw2D.MolDraw2DSVG(*size)
    options = drawer.drawOptions()
    options.setBackgroundColour((1, 1, 1, 1))
    options.bondLineWidth = 1.5
    options.padding = 0.06
    options.additionalAtomLabelPadding = 0.05
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, molecule)
    drawer.FinishDrawing()
    return drawer.GetDrawingText().encode('utf-8')


def _render_indigo(smiles):
    from ..core import Indigo, IndigoRenderer
    indigo = Indigo()
    renderer = IndigoRenderer(indigo)
    indigo.setOption('render-output-format', 'svg')
    indigo.setOption('render-background-color', '1,1,1')
    indigo.setOption('render-coloring', True)
    molecule = indigo.loadMolecule(str(smiles))
    molecule.layout()
    return bytes(renderer.renderToBuffer(molecule))
