"""Hit-level filters for the feature table: hit name, taxonomy, substructure
and activity class of the NPAtlas compounds within a feature's ppm window.

A feature passes when at least one of its hits meets every active criterion.
Atlas annotations are built once per atlas; the feature -> hit index once per
run; RDKit work only ever touches compounds that are hits, and is memoised.
"""

import threading

import numpy as np
import pandas as pd

from .. import config
from . import feature as featuredata

TAXON_LEVELS = ('kingdom', 'phylum', 'class', 'order', 'family', 'genus', 'species')
ACTIVITIES = ('electrophile', 'siderophore', 'chromophore')
DEFAULT_CHROMOPHORE_MIN = 14

# (label, SMARTS)
WARHEADS = (
    ('Epoxide', 'C1OC1'),
    ('Aziridine', 'C1NC1'),
    ('Thiirane', 'C1SC1'),
    ('β-Lactone', 'O=C1OCC1'),
    ('β-Lactam', 'O=C1NCC1'),
    ('Michael acceptor',
     '[CX3;!$(C-[N,O,S])]=[CX3]-[CX3;!$(C(=O)[O,N,S])]=[OX1]'),
    ('Michael acceptor', '[CH2]=[CX3]-[CX3]=[OX1]'),
    ('Michael acceptor', '[C;R;!$(C-[N,O,S])]=[C;R]-[C;R](=[OX1])-[O;R]'),
    ('Michael acceptor', 'O=C1C=CC(=O)N1'),
    ('Michael acceptor', 'C=C-S(=O)(=O)'),
    ('Michael acceptor', 'C=C-[N+](=O)[O-]'),
    ('Michael acceptor', 'C#C-C=O'),
    ('Quinone', 'O=[#6]1[#6]=,:[#6][#6](=O)[#6]=,:[#6]1'),
    ('Quinone', 'O=[#6]1[#6](=O)[#6]=,:[#6][#6]=,:[#6]1'),
    ('α-Halocarbonyl', '[Cl,Br,I][CX4][CX3]=O'),
    ('Aldehyde', '[CX3H1](=O)[#6]'),
    ('Isothiocyanate', 'N=C=S'),
    ('Isocyanate', 'N=C=O'),
    ('Diazocarbonyl', 'C(=O)C=[N+]=[N-]'),
)

_LOCK = threading.Lock()
_REFERENCES = {}
_INDEXES = {}
_PATTERNS = None


def _rdkit():
    try:
        from rdkit import Chem, RDLogger
    except ImportError as exc:
        raise ValueError('substructure and activity filters need RDKit on the server') from exc
    RDLogger.DisableLog('rdApp.*')
    return Chem


def _patterns():
    global _PATTERNS
    if _PATTERNS is None:
        Chem = _rdkit()
        _PATTERNS = [(label, Chem.MolFromSmarts(smarts)) for label, smarts in WARHEADS]
    return _PATTERNS


def _mtime(path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


class Reference:
    """The run-narrowed atlas as flat arrays, plus memoised chemistry."""

    def __init__(self, params):
        atlas = featuredata.atlas_for(params).reset_index()
        self.size = len(atlas)
        self.compound_id = atlas['compound_id'].to_numpy()
        self.mph = atlas['compound_m_plus_h'].to_numpy(dtype=float)
        self.mna = atlas['compound_m_plus_na'].to_numpy(dtype=float)
        self.order_h = np.argsort(self.mph, kind='stable')
        self.sorted_h = self.mph[self.order_h]
        self.order_na = np.argsort(self.mna, kind='stable')
        self.sorted_na = self.mna[self.order_na]
        self.smiles = atlas['compound_smiles'].fillna('').astype(str).to_numpy()

        names = atlas['compound_name'].fillna('').astype(str)
        if 'synonyms' in atlas:
            names = names + '\n' + atlas['synonyms'].fillna('').astype(str)
        self.names = names.str.lower()

        genus_col = atlas['genus'].fillna('').astype(str)
        origin = atlas['origin_type'].fillna('').astype(str)
        taxa = {'kingdom': origin, 'genus': genus_col,
                'species': (genus_col + ' ' + atlas['origin_species'].fillna('').astype(str)).str.strip()}
        lineage = _lineage()
        keyed = pd.DataFrame({'origin_type': origin, 'genus': genus_col})
        if lineage is not None:
            keyed = keyed.merge(lineage, on=['origin_type', 'genus'], how='left')
        for level in ('phylum', 'class', 'order', 'family'):
            values = keyed[level] if level in keyed else pd.Series([''] * self.size)
            taxa[level] = values.fillna('').astype(str).reset_index(drop=True)
        self.taxa = {level: taxa[level].reset_index(drop=True) for level in TAXON_LEVELS}
        self.taxa_lower = {level: series.str.lower() for level, series in self.taxa.items()}

        sources = atlas['source_database'].fillna('').astype(str) \
            if 'source_database' in atlas else pd.Series([''] * self.size)
        blocks = atlas['compound_inchikey'].fillna('').astype(str).str.split('-').str[0]
        self.siderophore = (blocks.isin(_siderite_keys()) | (sources == 'SIDERITE')).to_numpy()

        self._positions = None
        self._binary = {}
        self._chem = {}
        self._substructure = {}

    def position_of(self, compound_id):
        if self._positions is None:
            self._positions = {}
            for position, value in enumerate(self.compound_id):
                self._positions.setdefault(int(value), position)
        try:
            return self._positions.get(int(compound_id))
        except (TypeError, ValueError):
            return None

    def mol(self, position):
        Chem = _rdkit()
        binary = self._binary.get(position)
        if binary is None:
            molecule = Chem.MolFromSmiles(self.smiles[position]) if self.smiles[position] else None
            binary = molecule.ToBinary() if molecule is not None else b''
            self._binary[position] = binary
            return molecule
        return Chem.Mol(binary) if binary else None

    def chemistry(self, position):
        """(warhead labels, largest conjugated pi system in atoms)."""
        cached = self._chem.get(position)
        if cached is None:
            molecule = self.mol(position)
            if molecule is None:
                cached = ((), 0)
            else:
                labels = []
                for label, pattern in _patterns():
                    if label not in labels and molecule.HasSubstructMatch(pattern):
                        labels.append(label)
                cached = (tuple(labels), conjugated_size(molecule))
            self._chem[position] = cached
        return cached

    def substructure(self, query_key, query, positions):
        memo = self._substructure.get(query_key)
        if memo is None:
            if len(self._substructure) >= 8:
                self._substructure.pop(next(iter(self._substructure)))
            memo = self._substructure[query_key] = {}
        out = np.zeros(len(positions), dtype=bool)
        for i, position in enumerate(positions):
            hit = memo.get(position)
            if hit is None:
                molecule = self.mol(position)
                hit = memo[position] = bool(molecule is not None
                                            and molecule.HasSubstructMatch(query))
            out[i] = hit
        return out


def conjugated_size(molecule):
    """Atoms in the largest conjugated pi system (aromatic or multiply bonded
    atoms joined by multiple bonds or conjugated single bonds)."""
    BondType = _rdkit().BondType
    count = molecule.GetNumAtoms()
    pi = [atom.GetIsAromatic() or any(bond.GetBondType() in (BondType.DOUBLE, BondType.TRIPLE)
                                      for bond in atom.GetBonds())
          for atom in molecule.GetAtoms()]
    parent = list(range(count))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for bond in molecule.GetBonds():
        a, b = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        if not (pi[a] and pi[b]):
            continue
        if bond.GetBondType() == BondType.SINGLE and not bond.GetIsConjugated():
            continue
        parent[find(a)] = find(b)
    sizes = {}
    for i in range(count):
        if pi[i]:
            root = find(i)
            sizes[root] = sizes.get(root, 0) + 1
    return max(sizes.values(), default=0)


_LINEAGE = {}
_SIDERITE = {}


def _lineage():
    path = config.TAXONOMY_PATH
    key = _mtime(path)
    if key not in _LINEAGE:
        _LINEAGE.clear()
        _LINEAGE[key] = (pd.read_csv(path, sep='\t', dtype=str).fillna('')
                         if key is not None else None)
    return _LINEAGE[key]


def _siderite_keys():
    path = config.SIDERITE_KEYS_PATH
    key = _mtime(path)
    if key not in _SIDERITE:
        _SIDERITE.clear()
        _SIDERITE[key] = (frozenset(path.read_text(encoding='utf-8').split())
                          if key is not None else frozenset())
    return _SIDERITE[key]


def reference(params):
    narrowing = {key: params.get(key, '') for key in ('kingdom', 'genus', 'taxon_level', 'taxon')}
    key = (tuple(narrowing.values()), _mtime(config.NPATLAS_PATH),
           _mtime(config.NPATLAS_SUPPLEMENTAL_PATH),
           _mtime(config.TAXONOMY_PATH), _mtime(config.SIDERITE_KEYS_PATH))
    with _LOCK:
        cached = _REFERENCES.get(key)
        if cached is None:
            cached = Reference(narrowing)
            if len(_REFERENCES) >= 2:
                _REFERENCES.pop(next(iter(_REFERENCES)))
            _REFERENCES[key] = cached
    return cached


def hit_index(results, ref):
    """{compound: atlas positions within the run's ppm window}."""
    params = results.params()
    ppm = float(params.get('ppmthresh', 10))
    key = (str(results.iondict_path), _mtime(results.iondict_path), ppm, id(ref))
    with _LOCK:
        cached = _INDEXES.get(key)
    if cached is not None:
        return cached

    iondict = results.iondict()
    masses = pd.to_numeric(iondict['m/z'], errors='coerce').to_numpy(dtype=float)
    compounds = iondict['Compound'].astype(str).to_numpy()
    t = ppm / 1e6

    def window(sorted_vals, order, values):
        lo = np.searchsorted(sorted_vals, masses * (1 - 2 * t), side='left')
        hi = np.searchsorted(sorted_vals, masses * (1 + 2 * t), side='right')
        out = []
        for i, mass in enumerate(masses):
            cand = order[lo[i]:hi[i]]
            if cand.size:
                cv = values[cand]
                with np.errstate(divide='ignore', invalid='ignore'):
                    cand = cand[np.abs(1e6 * (cv - mass) / cv) < ppm]
            out.append(cand)
        return out

    by_h = window(ref.sorted_h, ref.order_h, ref.mph)
    by_na = window(ref.sorted_na, ref.order_na, ref.mna)
    index = {}
    for i, compound in enumerate(compounds):
        if np.isnan(masses[i]):
            continue
        positions = np.union1d(by_h[i], by_na[i])
        if positions.size:
            index[compound] = positions
    with _LOCK:
        if len(_INDEXES) >= 8:
            _INDEXES.pop(next(iter(_INDEXES)))
        _INDEXES[key] = index
    if index:
        candidates = np.unique(np.concatenate(list(index.values())))
        threading.Thread(target=_warm, args=(ref, candidates), daemon=True).start()
    return index


def _warm(ref, positions):
    try:
        _rdkit()
        for position in positions:
            ref.chemistry(position)
    except Exception:  # noqa: BLE001 - warming is best effort
        pass


def parse_query(text):
    Chem = _rdkit()
    text = str(text).strip()
    query = Chem.MolFromSmiles(text) or Chem.MolFromSmarts(text)
    if query is None or query.GetNumAtoms() == 0:
        raise ValueError(f'could not read substructure {text!r}')
    return query


def normalise(spec):
    """The active parts of a hit-filter spec, or None if nothing is active."""
    spec = spec or {}
    out = {}
    name = str(spec.get('name') or '').strip().lower()
    if name:
        out['name'] = name
    taxon = spec.get('taxon') or {}
    level = str(taxon.get('level') or '').lower()
    value = str(taxon.get('value') or '').strip().lower()
    if value:
        if level not in TAXON_LEVELS:
            raise ValueError(f'unknown taxonomic level {level!r}')
        out['taxon'] = (level, value)
    substructure = str(spec.get('substructure') or '').strip()
    if substructure:
        out['substructure'] = substructure
    activity = [a for a in (spec.get('activity') or []) if a in ACTIVITIES]
    if activity:
        out['activity'] = activity
        out['chromophore_min'] = int(spec.get('chromophore_min') or DEFAULT_CHROMOPHORE_MIN)
    return out or None


def passing_positions(ref, positions, spec):
    """The subset of atlas ``positions`` meeting every criterion in ``spec``."""
    positions = np.asarray(positions)
    mask = np.ones(len(positions), dtype=bool)
    if 'name' in spec:
        mask &= ref.names.iloc[positions].str.contains(spec['name'], regex=False).to_numpy()
    if 'taxon' in spec:
        level, value = spec['taxon']
        values = ref.taxa_lower[level].iloc[positions]
        if level == 'species':
            hit = (values == value) | values.str.startswith(value + ' ')
        else:
            hit = values == value
        mask &= hit.to_numpy()
    if 'activity' in spec and mask.any():
        wanted = set(spec['activity'])
        keep = mask.copy()
        for i in np.flatnonzero(mask):
            position = positions[i]
            ok = 'siderophore' in wanted and ref.siderophore[position]
            if not ok and wanted & {'electrophile', 'chromophore'}:
                labels, conjugated = ref.chemistry(position)
                ok = (('electrophile' in wanted and bool(labels))
                      or ('chromophore' in wanted and conjugated >= spec['chromophore_min']))
            keep[i] = ok
        mask = keep
    if 'substructure' in spec and mask.any():
        query = parse_query(spec['substructure'])
        selected = np.flatnonzero(mask)
        mask[selected] = ref.substructure(spec['substructure'], query, positions[selected])
    return positions[mask]


def passing_features(results, spec):
    """Compound ids with at least one hit meeting ``spec``."""
    ref = reference(results.params())
    index = hit_index(results, ref)
    if not index:
        return set()
    candidates = np.unique(np.concatenate(list(index.values())))
    ok = np.zeros(ref.size, dtype=bool)
    ok[passing_positions(ref, candidates, spec)] = True
    return {compound for compound, positions in index.items() if ok[positions].any()}


def annotate_matches(params, matches, spec=None):
    """Add warhead labels, siderophore flag and conjugated size to matches,
    and ``passes`` when a hit-filter ``spec`` is given."""
    try:
        ref = reference(params)
    except FileNotFoundError:
        return matches
    try:
        _rdkit()
        chemistry = True
    except ValueError:
        chemistry = False
    positions = [ref.position_of(match.get('compound_id')) for match in matches]
    for match, position in zip(matches, positions):
        if position is None:
            continue
        match['siderophore'] = bool(ref.siderophore[position])
        if chemistry:
            labels, conjugated = ref.chemistry(position)
            match['warheads'] = list(labels)
            match['conjugated'] = int(conjugated)
    if spec:
        known = np.array([p for p in positions if p is not None], dtype=int)
        passing = set(passing_positions(ref, known, spec).tolist()) if known.size else set()
        for match, position in zip(matches, positions):
            match['passes'] = position in passing
    return matches


def suggest_taxa(params, level, text, limit=30):
    """Names at ``level`` containing ``text``, prefix matches first, with
    compound counts."""
    if level not in TAXON_LEVELS:
        raise ValueError(f'unknown taxonomic level {level!r}')
    ref = reference(params)
    counts = ref.taxa[level][ref.taxa[level] != ''].value_counts()
    text = str(text or '').strip().lower()
    names = counts.index.to_series()
    lower = names.str.lower()
    if text:
        found = lower.str.contains(text, regex=False)
        counts, lower = counts[found.to_numpy()], lower[found]
        prefix = lower.str.startswith(text).to_numpy()
        frame = pd.DataFrame({'name': counts.index, 'count': counts.to_numpy(), 'prefix': prefix})
        frame = frame.sort_values(['prefix', 'count', 'name'], ascending=[False, False, True])
    else:
        frame = pd.DataFrame({'name': counts.index, 'count': counts.to_numpy()})
    return [{'name': str(row['name']), 'count': int(row['count'])}
            for row in frame.head(limit).to_dict('records')]
