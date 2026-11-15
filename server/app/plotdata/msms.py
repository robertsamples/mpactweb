"""MS/MS spectral search of a run's fragment spectra against a compiled
MS-DIAL library (scripts/build_msms_library.py).

With ``match_precursor`` a library spectrum is a candidate when its precursor
is within the run's ppm window, and is scored by cosine. Without it every
library spectrum is a candidate: a binned sparse dot product screens them and
the best are rescored by modified cosine (fragments may also match shifted by
the precursor difference), which is what finds analogues.

Results are written to ``msms_hits.json`` in the run's output directory.
"""

import gzip
import json
import threading

import numpy as np
import pandas as pd

from .. import config, storage
from ..results import Memo, file_key

TOLERANCE = 0.02        # Da, fragment matching
MIN_MATCHED = 2         # shared peaks for a match to count
MIN_MATCHED_ANALOG = 4  # without a precursor match
MAX_SHIFT = 200.0       # Da, analogue precursor difference
PRECURSOR_WINDOW = 1.5  # Da around the precursor ignored in analogue scoring
TOP_PEAKS = 50
RELATIVE_FLOOR = 0.01   # of the base peak
MAX_RESULTS = 10        # per feature, after de-duplicating by structure
SCREEN = 60             # analogue candidates rescored per feature
DEFAULT_MIN_SCORE = 0.5

PROTON = 1.007276
SODIUM = 22.989218
# Library precursor type -> (molecules, charge, mass added), for the neutral
# mass behind each entry.
ADDUCTS = {
    '[M+H]+': (1, 1, PROTON), '[M+Na]+': (1, 1, SODIUM), '[M+NH4]+': (1, 1, 18.033823),
    '[M+K]+': (1, 1, 38.963158), '[M]+': (1, 1, -0.000549),
    '[M-H2O+H]+': (1, 1, PROTON - 18.010565), '[M+H-H2O]+': (1, 1, PROTON - 18.010565),
    '[M-2H2O+H]+': (1, 1, PROTON - 36.02113), '[M+2H]2+': (1, 2, 2 * PROTON),
    '[M+3H]3+': (1, 3, 3 * PROTON), '[2M+H]+': (2, 1, PROTON), '[2M+Na]+': (2, 1, SODIUM),
    '[2M+NH4]+': (2, 1, 18.033823),
}
# What a feature's m/z is taken to be, besides the library entry's own ion.
FEATURE_IONS = (('[M+H]+', PROTON), ('[M+Na]+', SODIUM))
RESULTS_FILE = 'msms_hits.json'

_LOCK = threading.Lock()
_LIBRARY = {}


def clean_peaks(array):
    """(mz, weight) sorted by m/z: floor, top peaks, sqrt, unit norm."""
    array = np.asarray(array, dtype=float)
    if array.ndim != 2 or array.shape[0] == 0:
        return np.empty(0), np.empty(0)
    mz, intensity = array[:, 0], array[:, 1]
    keep = np.isfinite(mz) & np.isfinite(intensity) & (intensity > 0)
    mz, intensity = mz[keep], intensity[keep]
    if mz.size == 0:
        return np.empty(0), np.empty(0)
    keep = intensity >= intensity.max() * RELATIVE_FLOOR
    mz, intensity = mz[keep], intensity[keep]
    top = np.argsort(-intensity, kind='stable')[:TOP_PEAKS]
    mz, intensity = mz[top], intensity[top]
    order = np.argsort(mz, kind='stable')
    weight = np.sqrt(intensity[order])
    return mz[order], weight / np.linalg.norm(weight)


def without_precursor(mz, weight, precursor):
    keep = np.abs(mz - precursor) > PRECURSOR_WINDOW
    mz, weight = mz[keep], weight[keep]
    norm = np.linalg.norm(weight)
    return (mz, weight / norm) if norm > 0 else (mz, weight)


def score(qmz, qw, lmz, lw, shift=None):
    """(cosine, matched peaks), greedy one-to-one; ``shift`` (query precursor
    minus library precursor) makes it the modified cosine."""
    close = np.abs(qmz[:, None] - lmz[None, :]) <= TOLERANCE
    if shift is not None and abs(shift) > TOLERANCE:
        close |= np.abs(qmz[:, None] - (lmz[None, :] + shift)) <= TOLERANCE
    qi, li = np.nonzero(close)
    if qi.size == 0:
        return 0.0, 0
    products = qw[qi] * lw[li]
    order = np.argsort(-products, kind='stable')
    used_q, used_l = set(), set()
    total, matched = 0.0, 0
    for k in order:
        a, b = qi[k], li[k]
        if a in used_q or b in used_l:
            continue
        used_q.add(a)
        used_l.add(b)
        total += products[k]
        matched += 1
    return float(total), matched


class Library:
    def __init__(self, path, meta_path):
        data = np.load(path)
        self.precursor = data['precursor']
        self.ptr = data['ptr']
        self.mz = data['mz']
        self.weight = data['intensity']
        self.size = len(self.precursor)
        self.order = np.argsort(self.precursor, kind='stable')
        self.sorted_precursor = self.precursor[self.order]
        self.meta_path = meta_path
        self._meta = None
        self._matrix = None
        self._neutral = None
        stat = path.stat()
        self.signature = f'{stat.st_size}-{stat.st_mtime_ns}'

    @property
    def neutral(self):
        """Neutral mass per entry from its precursor type (NaN if unknown),
        with a sorted order for windowed lookup."""
        if self._neutral is None:
            mass = np.full(self.size, np.nan)
            types = self.meta['precursortype'].to_numpy()
            for name, (molecules, charge, added) in ADDUCTS.items():
                rows = types == name
                mass[rows] = (self.precursor[rows] * charge - added) / molecules
            known = np.flatnonzero(np.isfinite(mass))
            order = known[np.argsort(mass[known], kind='stable')]
            self._neutral = (mass, order, mass[order])
        return self._neutral

    def precursor_candidates(self, mz, ppm):
        """Entries whose own ion, or whose neutral mass as [M+H]+ or
        [M+Na]+, is within ``ppm`` of ``mz``."""
        window = mz * ppm / 1e6
        lo = np.searchsorted(self.sorted_precursor, mz - window, side='left')
        hi = np.searchsorted(self.sorted_precursor, mz + window, side='right')
        found = [self.order[lo:hi]]
        _, order, sorted_mass = self.neutral
        for _, added in FEATURE_IONS:
            target = mz - added
            lo = np.searchsorted(sorted_mass, target - window, side='left')
            hi = np.searchsorted(sorted_mass, target + window, side='right')
            found.append(order[lo:hi])
        return np.unique(np.concatenate(found))

    def relation(self, index, mz):
        """(label, Δm/z) for how ``mz`` matches entry ``index``: its own ion
        (label None) or the closest of [M+H]+ / [M+Na]+."""
        options = [(None, mz - float(self.precursor[index]))]
        neutral = self.neutral[0][index]
        if np.isfinite(neutral):
            options += [(label, mz - added - float(neutral)) for label, added in FEATURE_IONS]
        return min(options, key=lambda option: abs(option[1]))

    def peaks(self, index):
        start, stop = self.ptr[index], self.ptr[index + 1]
        return self.mz[start:stop], self.weight[start:stop]

    @property
    def meta(self):
        if self._meta is None:
            with gzip.open(self.meta_path, 'rt', encoding='utf-8') as handle:
                self._meta = pd.read_csv(handle, sep='\t', dtype=str, quoting=3).fillna('')
            self._meta['block'] = self._meta['inchikey'].str.split('-').str[0]
        return self._meta

    @property
    def matrix(self):
        """spectra x m/z bins, for the analogue screen."""
        if self._matrix is None:
            from scipy import sparse
            bins = np.floor(self.mz / TOLERANCE).astype(np.int64)
            rows = np.repeat(np.arange(self.size), np.diff(self.ptr))
            self._matrix = sparse.csr_matrix(
                (self.weight.astype(np.float32), (rows, bins)),
                shape=(self.size, int(bins.max()) + 2)).T.tocsr()
        return self._matrix


def available():
    return config.MSMS_LIBRARY_PATH.exists() and config.MSMS_META_PATH.exists()


def library():
    path = config.MSMS_LIBRARY_PATH
    if not available():
        raise FileNotFoundError('the MS/MS library is not installed on this server; '
                                'run scripts/build_msms_library.py')
    key = (str(path), path.stat().st_mtime_ns)
    with _LOCK:
        cached = _LIBRARY.get(key)
        if cached is None:
            _LIBRARY.clear()
            cached = _LIBRARY[key] = Library(path, config.MSMS_META_PATH)
    return cached


def _screen(lib, queries, min_score):
    """{query position: candidate library indices} by binned dot product."""
    from scipy import sparse
    matrix = lib.matrix                  # bins x spectra
    width = matrix.shape[0]
    out = {}
    chunk = 24
    for start in range(0, len(queries), chunk):
        block = queries[start:start + chunk]
        rows, cols, vals = [], [], []
        for row, (_, mz, weight) in enumerate(block):
            bins = np.floor(mz / TOLERANCE).astype(np.int64)
            for offset in (-1, 0, 1):
                spread = bins + offset
                keep = (spread >= 0) & (spread < width)
                rows.append(np.full(int(keep.sum()), row))
                cols.append(spread[keep])
                vals.append(weight[keep])
        q = sparse.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                              shape=(len(block), width))
        product = (q @ matrix).tocsr()
        for row in range(len(block)):
            lo, hi = product.indptr[row], product.indptr[row + 1]
            values, indices = product.data[lo:hi], product.indices[lo:hi]
            keep = values >= min_score * 0.5
            values, indices = values[keep], indices[keep]
            if values.size > SCREEN:
                top = np.argpartition(-values, SCREEN)[:SCREEN]
                indices = indices[top]
            out[start + row] = indices
    return out


def search(results, fragdb, min_score=DEFAULT_MIN_SCORE, match_precursor=True):
    """Search every filtered feature that has a spectrum; write and return
    the results."""
    lib = library()
    params = results.params()
    ppm = float(params.get('ppmthresh', 10))
    min_score = float(min_score)

    iondict = results.iondict()
    iondict = iondict.set_index(iondict['Compound'].astype(str))
    try:
        compounds = list(results.filtered_ids())
    except Exception:  # noqa: BLE001
        compounds = list(iondict.index)
    ions = getattr(fragdb, 'ions', {}) if fragdb else {}

    queries = []
    for compound in compounds:
        entry = ions.get(compound)
        if entry is None or compound not in iondict.index:
            continue
        mz, weight = clean_peaks(entry.pattern)
        if mz.size < MIN_MATCHED:
            continue
        try:
            precursor = float(iondict.at[compound, 'm/z'])
        except (TypeError, ValueError):
            continue
        queries.append((compound, mz, weight, precursor))

    blocks = lib.meta['block'].to_numpy()
    if match_precursor:
        candidates = {i: lib.precursor_candidates(precursor, ppm)
                      for i, (_, _, _, precursor) in enumerate(queries)}
    else:
        candidates = _screen(lib, [(c, m, w) for c, m, w, _ in queries], min_score)

    hits = {}
    needed = MIN_MATCHED if match_precursor else MIN_MATCHED_ANALOG
    for i, (compound, mz, weight, precursor) in enumerate(queries):
        found = {}
        if not match_precursor:
            mz, weight = without_precursor(mz, weight, precursor)
            if mz.size < needed:
                hits[compound] = []
                continue
        for index in candidates.get(i, ()):
            lmz, lw = lib.peaks(index)
            if match_precursor:
                shift = None
            else:
                shift = precursor - lib.precursor[index]
                if abs(shift) > MAX_SHIFT:
                    continue
                lmz, lw = without_precursor(lmz, lw, lib.precursor[index])
            value, matched = score(mz, weight, lmz, lw, shift)
            if value < min_score or matched < needed:
                continue
            key = blocks[index] or f'#{index}'
            if key not in found or value > found[key][1]:
                found[key] = (int(index), round(value, 4), matched)
        hits[compound] = sorted(found.values(), key=lambda hit: -hit[1])[:MAX_RESULTS]

    record = {'library': lib.signature, 'min_score': min_score,
              'match_precursor': bool(match_precursor), 'ppm': ppm,
              'searched': len(queries), 'hits': hits}
    # Written beside the target and renamed over it, so a table refresh that
    # lands mid-search reads the previous results rather than half a file.
    storage.write_text_atomic(results.outputdir / RESULTS_FILE, json.dumps(record))
    return record


_STORED = Memo(limit=16)


def stored(results):
    """The run's saved search, or None if there is none for this library."""
    path = results.outputdir / RESULTS_FILE
    if not path.exists() or not available():
        return None

    def read():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
    try:
        # Read on every table refresh and every feature click; parsed once
        # per search instead.
        record = _STORED.get(file_key(path), read)
    except OSError:
        return None
    if record is None:
        return None
    return record if record.get('library') == library().signature else None


def counts(results):
    """{compound: number of MS/MS hits} for searched features, or None."""
    record = stored(results)
    if record is None:
        return None
    return {compound: len(found) for compound, found in record['hits'].items()}


def matches(results, compound, precursor):
    record = stored(results)
    if record is None:
        return {'searched': False, 'matches': []}
    lib = library()
    meta = lib.meta
    out = []
    for index, value, matched in record['hits'].get(str(compound), []):
        row = meta.iloc[index]
        peak_mz, weight = lib.peaks(index)
        relative = weight.astype(float) ** 2
        if precursor is None:
            ion, delta = None, None
        elif record['match_precursor']:
            ion, delta = lib.relation(index, float(precursor))
        else:
            ion, delta = None, float(precursor) - float(lib.precursor[index])
        relative = 100 * relative / relative.max() if relative.size else relative
        out.append({
            'index': int(index),
            'peak_mz': [round(float(v), 5) for v in peak_mz],
            'peak_intensity': [round(float(v), 2) for v in relative],
            'name': row['name'], 'score': value, 'matched': matched,
            'precursor_mz': float(lib.precursor[index]),
            'delta_mz': delta, 'feature_ion': ion,
            'precursortype': row['precursortype'], 'formula': row['formula'],
            'ontology': row['ontology'], 'inchikey': row['inchikey'],
            'smiles': row['smiles'], 'collisionenergy': row['collisionenergy'],
        })
    out.sort(key=lambda match: -match['score'])
    return {'searched': True, 'min_score': record['min_score'],
            'match_precursor': record['match_precursor'],
            'has_spectrum': str(compound) in record['hits'], 'matches': out}
