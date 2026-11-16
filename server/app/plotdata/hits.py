"""Hit-level filters for the feature table: hit name, taxonomy, substructure
and activity class of the NPAtlas compounds within a feature's ppm window.

A feature passes when at least one of its hits meets every active criterion.
Atlas annotations are built once per atlas; the feature -> hit index once per
run; RDKit work only ever touches compounds that are hits, and is memoised.
"""

import functools
import hashlib
import itertools
import threading

import numpy as np
import pandas as pd

from .. import config, ions
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


_IMIDIC_ACID = None


def standardize(molecule):
    """Imidic acids drawn as such (-C(OH)=N-, common for peptides in the
    libraries) rewritten as the amides they are, including non-aromatic
    lactims. Stereocentres are kept; the InChIKey does not change."""
    global _IMIDIC_ACID
    if molecule is None:
        return None
    Chem = _rdkit()
    if _IMIDIC_ACID is None:
        _IMIDIC_ACID = Chem.MolFromSmarts('[NX2;!a]=[CX3;!a]-[OX2H1]')
    if not molecule.HasSubstructMatch(_IMIDIC_ACID):
        return molecule
    editable = Chem.RWMol(molecule)
    for _ in range(500):
        found = editable.GetSubstructMatches(_IMIDIC_ACID, maxMatches=1)
        if not found:
            break
        n, c, o = found[0]
        bond = editable.GetBondBetweenAtoms(n, c)
        bond.SetBondType(Chem.BondType.SINGLE)
        bond.SetStereo(Chem.BondStereo.STEREONONE)
        editable.GetBondBetweenAtoms(c, o).SetBondType(Chem.BondType.DOUBLE)
        for index in (n, c):
            for neighbour in editable.GetAtomWithIdx(index).GetBonds():
                neighbour.SetBondDir(Chem.BondDir.NONE)
        for index in (n, o):
            atom = editable.GetAtomWithIdx(index)
            atom.SetNumExplicitHs(0)
            atom.SetNoImplicit(False)
        Chem.SanitizeMol(editable)
    out = editable.GetMol()
    Chem.AssignStereochemistry(out, cleanIt=True, force=True)
    return out


@functools.lru_cache(maxsize=20000)
def canonical_smiles(smiles):
    """Canonical SMILES of the standardized structure; the input if RDKit
    is unavailable or cannot read it."""
    if not smiles:
        return smiles
    try:
        Chem = _rdkit()
    except ValueError:
        return smiles
    molecule = Chem.MolFromSmiles(str(smiles))
    if molecule is None:
        return smiles
    return Chem.MolToSmiles(standardize(molecule))


def chemistry_signature():
    """Changes whenever the warhead patterns, the conjugation rule or the
    structure standardization do, so a precomputed table built from older
    rules is ignored."""
    return hashlib.sha1((repr(WARHEADS) + 'conjugated-pi-v1-amide').encode()).hexdigest()[:12]


def smiles_hash(smiles):
    return hashlib.sha1(str(smiles).encode()).hexdigest()[:10]


_PRECOMPUTED = {}


def _precomputed():
    """{compound_id: (smiles hash, labels, conjugated)} from
    npatlas_chemistry.tsv, or {} if it is missing or stale."""
    path = config.CHEMISTRY_PATH
    key = _mtime(path)
    if key not in _PRECOMPUTED:
        _PRECOMPUTED.clear()
        table = {}
        if key is not None:
            with open(path, encoding='utf-8') as handle:
                if handle.readline().strip() == f'# {chemistry_signature()}':
                    handle.readline()
                    for line in handle:
                        compound_id, digest, labels, conjugated = line.rstrip('\n').split('\t')
                        table[int(compound_id)] = (digest, tuple(filter(None, labels.split('|'))),
                                                   int(conjugated))
        _PRECOMPUTED[key] = table
    return _PRECOMPUTED[key]


def _mtime(path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


class _Chemistry:
    """Memoised RDKit work over ``self.smiles``, by position."""

    taxa_lower = None

    def _init_chemistry(self):
        self._binary = {}
        self._chem = {}
        self._substructure = {}

    def mol(self, position):
        Chem = _rdkit()
        binary = self._binary.get(position)
        if binary is None:
            molecule = (standardize(Chem.MolFromSmiles(self.smiles[position]))
                        if self.smiles[position] else None)
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


class LibraryAnnotations(_Chemistry):
    """The MS/MS library's entries, for the same hit criteria. It records no
    source organism, so no entry meets a taxon criterion."""

    def __init__(self, meta):
        self.size = len(meta)
        self.smiles = meta['smiles'].to_numpy()
        self.names = meta['name'].str.lower()
        self.siderophore = meta['block'].isin(_siderite_keys()).to_numpy()
        self._init_chemistry()


_LIBRARY_ANNOTATIONS = {}


def library_annotations():
    """Annotations for the installed MS/MS library, or None."""
    from . import msms
    if not msms.available():
        return None
    lib = msms.library()
    with _LOCK:
        cached = _LIBRARY_ANNOTATIONS.get(lib.signature)
        if cached is None:
            _LIBRARY_ANNOTATIONS.clear()
            cached = _LIBRARY_ANNOTATIONS[lib.signature] = LibraryAnnotations(lib.meta)
    return cached


_SERIALS = itertools.count(1)


class Reference(_Chemistry):
    """The run-narrowed atlas as flat arrays, plus memoised chemistry."""

    def __init__(self, params):
        # Identifies this object in cache keys. ``id()`` did that job, but an
        # id is only unique among objects alive at the same time: once a
        # Reference was evicted, the next one built could be handed the same
        # address, and a hit index computed against the old narrowing would
        # then be served for the new one.
        self.serial = next(_SERIALS)
        atlas = featuredata.atlas_for(params).reset_index()
        self.size = len(atlas)
        self.compound_id = atlas['compound_id'].to_numpy()
        # Each compound's m/z as every ion form this run searches, with a
        # sorted order for windowed lookup.
        self.ions = tuple(params.get('ions') or ions.LEGACY_SEARCH)
        self.ion_mz = {label: featuredata.ion_masses(atlas, label) for label in self.ions}
        self.ion_order = {label: np.argsort(values, kind='stable')
                          for label, values in self.ion_mz.items()}
        self.ion_sorted = {label: self.ion_mz[label][self.ion_order[label]]
                           for label in self.ions}
        self.smiles = atlas['compound_smiles'].fillna('').astype(str).to_numpy()

        names = atlas['compound_name'].fillna('').astype(str)
        # As written, for the export's "top match" columns.
        self.display_names = names.to_numpy()
        self.formulas = (atlas['compound_molecular_formula'].fillna('').astype(str).to_numpy()
                         if 'compound_molecular_formula' in atlas else np.array([''] * self.size))
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
        self._init_chemistry()
        precomputed = _precomputed()
        if precomputed:
            for position, (compound_id, smiles) in enumerate(zip(self.compound_id, self.smiles)):
                entry = precomputed.get(int(compound_id))
                if entry is not None and entry[0] == smiles_hash(smiles):
                    self._chem[position] = (entry[1], entry[2])

    def position_of(self, compound_id):
        if self._positions is None:
            self._positions = {}
            for position, value in enumerate(self.compound_id):
                self._positions.setdefault(int(value), position)
        try:
            return self._positions.get(int(compound_id))
        except (TypeError, ValueError):
            return None


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
    narrowing['ions'] = tuple(ions.run_ions(params))
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
    key = (str(results.iondict_path), _mtime(results.iondict_path), ppm, ref.serial)
    with _LOCK:
        cached = _INDEXES.get(key)
    if cached is not None:
        return cached

    iondict = results.iondict(copy=False)
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

    by_ion = [window(ref.ion_sorted[label], ref.ion_order[label], ref.ion_mz[label])
              for label in ref.ions]
    index = {}
    for i, compound in enumerate(compounds):
        if np.isnan(masses[i]):
            continue
        positions = np.unique(np.concatenate([found[i] for found in by_ion]))
        if positions.size:
            index[compound] = positions
    with _LOCK:
        if len(_INDEXES) >= 8:
            _INDEXES.pop(next(iter(_INDEXES)))
        _INDEXES[key] = index
    if index:
        try:
            surviving = results.surviving()
        except Exception:  # noqa: BLE001 - only an ordering hint
            surviving = frozenset()
        first = [p for c, p in index.items() if c in surviving]
        rest = [p for c, p in index.items() if c not in surviving]
        first = np.unique(np.concatenate(first)) if first else np.array([], dtype=int)
        rest = np.setdiff1d(np.unique(np.concatenate(rest)), first) if rest else np.array([], dtype=int)
        threading.Thread(target=_warm, args=(ref, np.concatenate([first, rest])),
                         daemon=True).start()
    return index


def prewarm():
    """Parse the whole unnarrowed atlas in the background, once per process."""
    def work():
        try:
            ref = reference({})
            _warm(ref, range(ref.size))
        except Exception:  # noqa: BLE001 - best effort
            pass
    threading.Thread(target=work, daemon=True, name='hits-prewarm').start()


_PREPARED = set()


def prepare(results):
    """Build a run's hit index in the background, ahead of the first filter."""
    key = (str(results.iondict_path), _mtime(results.iondict_path))
    with _LOCK:
        if key in _PREPARED:
            return
        if len(_PREPARED) > 512:
            # Only a "seen it" marker, but one per run ever opened otherwise.
            _PREPARED.clear()
        _PREPARED.add(key)

    def work():
        try:
            hit_index(results, reference(results.params()))
        except Exception:  # noqa: BLE001 - best effort
            with _LOCK:
                _PREPARED.discard(key)
    threading.Thread(target=work, daemon=True).start()


def _warm(ref, positions):
    try:
        _rdkit()
        for position in positions:
            ref.chemistry(position)
            ref.mol(position)
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
    if 'taxon' in spec and ref.taxa_lower is None:
        mask[:] = False
    elif 'taxon' in spec:
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


def _passing(ref, index, spec):
    index = {c: np.asarray(p, dtype=int) for c, p in index.items() if len(p)}
    if not index:
        return set()
    candidates = np.unique(np.concatenate(list(index.values())))
    ok = np.zeros(ref.size, dtype=bool)
    ok[passing_positions(ref, candidates, spec)] = True
    return {compound for compound, positions in index.items() if ok[positions].any()}


def passing_features(results, spec, compounds=None):
    """Compound ids with at least one hit meeting ``spec``, among
    ``compounds`` when given: an NPAtlas mass match or, once an MS/MS search
    has run, an MS/MS library match."""
    from . import feature, msms
    keep = (lambda c: True) if compounds is None else (lambda c: c in compounds)
    passing = set()
    searched = False
    if feature.atlas_available():
        searched = True
        ref = reference(results.params())
        passing |= _passing(ref, {c: p for c, p in hit_index(results, ref).items() if keep(c)}, spec)
    record = msms.stored(results) if msms.available() else None
    if record is not None:
        searched = True
        index = {c: [hit[0] for hit in found] for c, found in record['hits'].items() if keep(c)}
        passing |= _passing(library_annotations(), index, spec)
    if not searched:
        raise FileNotFoundError('no database is installed on this server')
    return passing


def _allowed(ref, groups, spec):
    """A mask over ``ref`` of the positions meeting ``spec`` (all of them
    when there is no spec), given the candidate position arrays."""
    if not spec:
        return None
    groups = [np.asarray(g, dtype=int) for g in groups if len(g)]
    ok = np.zeros(ref.size, dtype=bool)
    if groups:
        ok[passing_positions(ref, np.unique(np.concatenate(groups)), spec)] = True
    return ok


def top_matches(results, compounds, spec=None):
    """``{compound: (name, formula, ppm, organism)}`` -- each feature's
    closest NPAtlas mass match, among those meeting ``spec`` when given.
    Features with no match are left out."""
    ref = reference(results.params())
    index = hit_index(results, ref)
    found = {compound: index[compound] for compound in compounds if compound in index}
    ok = _allowed(ref, found.values(), spec)

    iondict = results.iondict(copy=False)
    where = results.feature_positions()
    masses = pd.to_numeric(iondict['m/z'], errors='coerce').to_numpy(dtype=float)
    species = ref.taxa['species'].to_numpy()

    best = {}
    for compound, positions in found.items():
        if ok is not None:
            positions = positions[ok[positions]]
        if not len(positions):
            continue
        mass = masses[where[compound]]
        with np.errstate(divide='ignore', invalid='ignore'):
            ppm = functools.reduce(np.fmin, [
                np.abs(1e6 * (ref.ion_mz[label][positions] - mass) / ref.ion_mz[label][positions])
                for label in ref.ions])
        if not np.isfinite(ppm).any():
            continue
        at = int(np.nanargmin(ppm))
        position = positions[at]
        best[compound] = (str(ref.display_names[position]), str(ref.formulas[position]),
                          round(float(ppm[at]), 3), str(species[position]))
    return best


def top_library_matches(results, compounds, spec=None):
    """``{compound: (name, score)}`` -- each feature's highest-scoring MS/MS
    library match from the run's last search, among those meeting ``spec``."""
    from . import msms
    record = msms.stored(results)
    if record is None:
        return {}
    found = {compound: record['hits'][compound] for compound in compounds
             if record['hits'].get(compound)}
    ann = library_annotations() if spec else None
    ok = _allowed(ann, [[hit[0] for hit in hits] for hits in found.values()], spec) \
        if ann is not None else None
    names = msms.library().meta['name'].to_numpy()
    best = {}
    for compound, hits in found.items():
        # Stored best-first.
        for index, score, _matched in hits:
            if ok is None or ok[index]:
                best[compound] = (str(names[index]), score)
                break
    return best


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


def annotate_library_matches(matches, spec=None):
    """Tags and, with a ``spec``, ``passes`` for MS/MS library matches."""
    ann = library_annotations()
    if ann is None:
        return matches
    try:
        _rdkit()
        chemistry = True
    except ValueError:
        chemistry = False
    for match in matches:
        index = match['index']
        match['siderophore'] = bool(ann.siderophore[index])
        if chemistry:
            labels, conjugated = ann.chemistry(index)
            match['warheads'] = list(labels)
            match['conjugated'] = int(conjugated)
    if spec:
        indices = np.array([match['index'] for match in matches], dtype=int)
        passing = set(passing_positions(ann, indices, spec).tolist()) if indices.size else set()
        for match in matches:
            match['passes'] = match['index'] in passing
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
