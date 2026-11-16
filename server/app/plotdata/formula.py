"""Molecular formulae for MS/MS fragments.

A fragment's formula is only ever a sub-formula of its precursor ion's: no
atom the parent does not have. So fragments are annotated against one
candidate parent formula at a time (a database match's), and how much of the
spectrum a candidate can account for becomes evidence for or against it.
"""

import re

import numpy as np

ELECTRON = 0.000548579909
HYDROGEN = 1.00782503207

# Monoisotopic masses, and the valence used for the ring-and-double-bond
# count (the lowest common one; only its lower bound is tested).
ELEMENTS = {
    'H': (HYDROGEN, 1), 'C': (12.0, 4), 'N': (14.0030740048, 3),
    'O': (15.99491461956, 2), 'S': (31.972071, 2), 'P': (30.97376163, 3),
    'F': (18.99840322, 1), 'Cl': (34.96885268, 1), 'Br': (78.9183371, 1),
    'I': (126.904473, 1), 'Na': (22.9897692809, 1), 'K': (38.96370668, 1),
    'Si': (27.9769265325, 4), 'B': (11.0093054, 3), 'Se': (79.9165213, 2),
    'As': (74.9215965, 3), 'Fe': (55.9349375, 2), 'Co': (58.933195, 2),
    'Cu': (62.9295975, 2), 'Zn': (63.9291422, 2), 'Mg': (23.9850417, 2),
    'Al': (26.98153863, 3),
}

# Ion type -> atoms added to the neutral molecule (the charge is +1).
ADDUCTS = {
    '[M+H]+': {'H': 1}, '[M+Na]+': {'Na': 1}, '[M+K]+': {'K': 1},
    '[M+NH4]+': {'N': 1, 'H': 4}, '[M]+': {},
    '[M-H2O+H]+': {'H': -1, 'O': -1}, '[M+H-H2O]+': {'H': -1, 'O': -1},
    '[M-2H2O+H]+': {'H': -3, 'O': -2},
}

_TOKEN = re.compile(r'([A-Z][a-z]?)(\d*)')
_FORMULA = re.compile(r'^(?:[A-Z][a-z]?\d*)+[+-]?$')

# Element combinations tried per fragment. A parent large enough to pass it
# is not annotated rather than held up.
MAX_COMBINATIONS = 3_000_000
# Fragments are scored below the precursor; the precursor itself (and its
# isotopes) is every candidate's own formula and tells them apart no better.
PRECURSOR_MARGIN = 0.5
# Share of the tallest fragment a peak needs to count towards the score.
SCORE_FLOOR = 0.05
# Share of the base peak a peak needs to be given a formula at all, and how
# many peaks and candidate parents one report covers.
LABEL_FLOOR = 0.01
MAX_PEAKS = 60
# Da between a candidate's ion and the feature beyond which it is not a
# candidate parent of this spectrum.
MAX_PARENT_ERROR = 0.02
# Hydrogens (or halogens) per carbon below which a formula is not a
# plausible organic ion.
MIN_H_PER_C = 0.2
MAX_CANDIDATES = 40


def parse(text):
    """``(counts, charge)`` for a formula such as ``C20H30O5`` or
    ``C12H17N4OS+``, or None if it is not one this module can weigh."""
    text = str(text or '').strip()
    if not text or not _FORMULA.match(text):
        return None
    charge = 0
    if text[-1] in '+-':
        charge = 1 if text[-1] == '+' else -1
        text = text[:-1]
    counts = {}
    for element, number in _TOKEN.findall(text):
        if element not in ELEMENTS:
            return None
        counts[element] = counts.get(element, 0) + (int(number) if number else 1)
    return (counts, charge) if counts else None


def mass(counts):
    return sum(ELEMENTS[element][0] * number for element, number in counts.items())


def text(counts):
    """Hill notation: C, H, then the rest alphabetically."""
    order = [e for e in ('C', 'H') if counts.get(e)] if counts.get('C') else []
    order += sorted(e for e in counts if counts[e] and e not in order)
    return ''.join(f'{e}{counts[e] if counts[e] > 1 else ""}' for e in order)


def ion(formula, adduct):
    """Atom counts of the singly charged ion ``adduct`` makes of ``formula``.
    A formula written as a cation is its own ion whatever the adduct. None
    if either is unknown or the adduct takes atoms the molecule lacks."""
    parsed = parse(formula)
    if parsed is None:
        return None
    counts, charge = parsed
    if charge < 0:
        return None
    counts = dict(counts)
    if charge == 0:
        delta = ADDUCTS.get(adduct)
        if delta is None:
            return None
        for element, number in delta.items():
            counts[element] = counts.get(element, 0) + number
            if counts[element] < 0:
                return None
    return {element: number for element, number in counts.items() if number}


def ion_mz(counts):
    return mass(counts) - ELECTRON


class _Grid:
    """Every combination of a parent ion's non-hydrogen atoms."""

    def __init__(self, counts):
        self.hydrogens = counts.get('H', 0)
        self.elements = [e for e in counts if e != 'H' and counts[e] > 0]
        sizes = [counts[e] + 1 for e in self.elements]
        self.size = int(np.prod(sizes, dtype=np.float64)) if sizes else 0
        self.usable = 0 < self.size <= MAX_COMBINATIONS
        if not self.usable:
            return
        grids = np.meshgrid(*[np.arange(n, dtype=np.int16) for n in sizes], indexing='ij')
        self.counts = np.stack([g.ravel() for g in grids], axis=1)
        weights = np.array([ELEMENTS[e][0] for e in self.elements])
        valences = np.array([ELEMENTS[e][1] - 2 for e in self.elements], dtype=float)
        self.mass = self.counts @ weights
        # Rings plus double bonds before hydrogens are counted.
        self.unsaturation = 1 + (self.counts @ valences) / 2
        keep = self.counts.sum(axis=1) > 0
        if 'C' in self.elements:
            # An organic molecule's fragments keep at least one carbon.
            keep &= self.counts[:, self.elements.index('C')] > 0
        self.allowed = keep
        self.carbons = (self.counts[:, self.elements.index('C')].astype(float)
                        if 'C' in self.elements else np.zeros(self.size))
        halogens = [i for i, e in enumerate(self.elements) if e in ('F', 'Cl', 'Br', 'I')]
        self.halogens = (self.counts[:, halogens].sum(axis=1).astype(float)
                         if halogens else np.zeros(self.size))
        order = np.argsort(self.mass, kind='stable')
        self.order, self.sorted_mass = order, self.mass[order]


def annotate(ion_counts, mzs, tolerance):
    """The best sub-formula of ``ion_counts`` for each m/z, or None for a
    peak none fits: ``{'formula', 'error', 'radical', 'counts'}``.

    ``tolerance(mz)`` is the allowed error in Da. A candidate must not have
    more of any atom than the parent and must be a possible ion: rings plus
    double bonds no lower than -0.5, and at least one hydrogen or halogen to
    five carbons (the usual lower bound for organic molecules). Even-electron
    ions are preferred over radical cations unless the radical is markedly
    closer in mass.
    """
    grid = _Grid(ion_counts)
    out = [None] * len(mzs)
    if not grid.usable:
        return out
    for position, mz in enumerate(mzs):
        allowed = float(tolerance(mz))
        target = float(mz) + ELECTRON
        # Combinations light enough to leave room for 0..H hydrogens.
        low = np.searchsorted(grid.sorted_mass,
                              target - grid.hydrogens * HYDROGEN - allowed, side='left')
        high = np.searchsorted(grid.sorted_mass, target + allowed, side='right')
        if high <= low:
            continue
        rows = grid.order[low:high]
        rows = rows[grid.allowed[rows]]
        if not rows.size:
            continue
        rest = target - grid.mass[rows]
        hydrogens = np.rint(rest / HYDROGEN)
        error = rest - hydrogens * HYDROGEN
        unsaturation = grid.unsaturation[rows] - hydrogens / 2
        valid = ((hydrogens >= 0) & (hydrogens <= grid.hydrogens)
                 & (np.abs(error) <= allowed) & (unsaturation >= -0.5)
                 & (hydrogens + grid.halogens[rows] >= MIN_H_PER_C * grid.carbons[rows]))
        if not valid.any():
            continue
        rows, hydrogens = rows[valid], hydrogens[valid]
        error, unsaturation = error[valid], unsaturation[valid]
        # A whole number of rings and double bonds is an odd-electron ion.
        radical = np.isclose(unsaturation, np.rint(unsaturation))
        best = int(np.argmin(np.abs(error) + radical * allowed * 0.5))
        counts = {element: int(n) for element, n in zip(grid.elements, grid.counts[rows[best]])
                  if n}
        if hydrogens[best]:
            counts['H'] = int(hydrogens[best])
        out[position] = {'formula': text(counts), 'counts': counts,
                         'error': float(error[best]), 'radical': bool(radical[best])}
    return out


def loss(ion_counts, fragment_counts):
    """What the parent lost to leave the fragment, as a formula ('' if
    nothing)."""
    gone = {element: number - fragment_counts.get(element, 0)
            for element, number in ion_counts.items()}
    return text({element: number for element, number in gone.items() if number > 0})


def fragment_tolerance(ppm):
    """Allowed fragment error in Da at an m/z: the run's ppm window, and
    never tighter than 5 mDa, which is what QTOF fragment masses need."""
    ppm = float(ppm)
    return lambda mz: max(0.005, mz * ppm / 1e6)


def explain(ion_counts, mzs, intensities, precursor_mz, tolerance):
    """How much of a spectrum a parent ion formula accounts for.

    Returns ``{'explained', 'matched', 'total', 'peaks'}``: the share of the
    fragment intensity that has a sub-formula, the number of fragment peaks
    that do out of those scored, and per-peak annotations aligned with
    ``mzs`` (None where there is none). Every peak is annotated; only those
    below the precursor and at least ``SCORE_FLOOR`` of the tallest of them
    are scored, since the many peaks under that in a DDA spectrum are mostly
    not fragments of this ion at all. ``explained`` is None when there is no
    fragment to score.
    """
    mzs = np.asarray(mzs, dtype=float)
    intensities = np.asarray(intensities, dtype=float)
    peaks = annotate(ion_counts, mzs, tolerance)
    parent = ion_mz(ion_counts)
    for position, mz in enumerate(mzs):
        if peaks[position] is not None:
            peaks[position]['loss'] = loss(ion_counts, peaks[position].pop('counts'))
            peaks[position]['precursor'] = bool(abs(mz - parent) <= tolerance(mz)
                                                and not peaks[position]['loss'])
    scored = (mzs < float(precursor_mz) - PRECURSOR_MARGIN) & (intensities > 0)
    if scored.any():
        scored &= intensities >= intensities[scored].max() * SCORE_FLOOR
    weight = np.where(scored, intensities, 0.0)
    total = float(weight.sum())
    found = np.array([peak is not None for peak in peaks], dtype=bool)
    return {
        'explained': float(weight[found].sum() / total) if total > 0 else None,
        'matched': int((scored & found).sum()),
        'total': int(scored.sum()),
        'peaks': peaks,
    }


def mass_score(ppm, window):
    """1 at no error, falling to ~0.14 at the edge of the ppm window."""
    return float(np.exp(-0.5 * (float(ppm) / (float(window) / 2)) ** 2))


def composite(ppm, window, explained):
    """One score from the precursor's mass error and the fragments
    explained. The two multiply, so a candidate needs both to score highly;
    the fragment term runs from 0.5 to 1 rather than 0 to 1 because real
    spectra carry peaks no formula should explain, and a correct candidate
    must not be zeroed by them."""
    if explained is None:
        return None
    return mass_score(ppm, window) * (0.5 + 0.5 * float(explained))


def report(pattern, precursor_mz, ppm, candidates):
    """Fragment annotations for each candidate parent of one spectrum.

    ``pattern`` is the spectrum as stored ((m/z, intensity) rows) and
    ``candidates`` an iterable of ``(formula, adduct)``. Returns
    ``{'formula|adduct': {'formula', 'adduct', 'ion', 'explained',
    'matched', 'total', 'peaks'}}`` where ``peaks`` lists the annotated
    peaks by their row ``index`` in ``pattern``. Candidates that cannot be
    weighed are left out; at most ``MAX_CANDIDATES`` are worked.
    """
    array = np.asarray(pattern, dtype=float)
    if array.ndim != 2 or array.shape[0] == 0:
        return {}
    mzs, intensities = array[:, 0], array[:, 1]
    usable = np.isfinite(mzs) & np.isfinite(intensities) & (intensities > 0)
    if not usable.any():
        return {}
    # The peaks worth a label: over 1% of the base peak, the tallest first.
    rows = np.flatnonzero(usable & (intensities >= intensities[usable].max() * LABEL_FLOOR))
    rows = rows[np.argsort(-intensities[rows], kind='stable')][:MAX_PEAKS]
    tolerance = fragment_tolerance(ppm)

    out = {}
    for formula, adduct in candidates:
        key = f'{formula}|{adduct}'
        if key in out or len(out) >= MAX_CANDIDATES:
            continue
        counts = ion(formula, adduct)
        if counts is None:
            continue
        # Not this feature's ion at all: a formula written as a cation that
        # the mass search matched as though a proton or sodium were added.
        if abs(ion_mz(counts) - float(precursor_mz)) > max(MAX_PARENT_ERROR,
                                                           3 * tolerance(precursor_mz)):
            continue
        result = explain(counts, mzs[rows], intensities[rows], precursor_mz, tolerance)
        peaks = []
        for row, peak in zip(rows, result['peaks']):
            if peak is None:
                continue
            peaks.append({'index': int(row), 'formula': peak['formula'], 'loss': peak['loss'],
                          'error': round(peak['error'], 5), 'radical': peak['radical'],
                          'precursor': peak['precursor']})
        out[key] = {'formula': str(formula), 'adduct': adduct, 'ion': text(counts),
                    'explained': None if result['explained'] is None
                    else round(result['explained'], 4),
                    'matched': result['matched'], 'total': result['total'], 'peaks': peaks}
    return out


def score_matches(matches, pattern, precursor_mz, ppm, key_of, ppm_of):
    """Add fragment consistency to database matches, in place.

    ``key_of(match)`` gives the match's ``(formula, adduct)`` (or None) and
    ``ppm_of(match)`` its precursor error. Each match that can be weighed
    gains ``fragment_key`` and ``fragments`` (``explained``, ``matched``,
    ``total``) and ``score``, the composite. Returns the annotations by
    key, for drawing.
    """
    keys = [key_of(match) for match in matches]
    found = report(pattern, precursor_mz, ppm, [key for key in keys if key is not None])
    for match, key in zip(matches, keys):
        entry = found.get(f'{key[0]}|{key[1]}') if key is not None else None
        if entry is None:
            continue
        match['fragment_key'] = f'{key[0]}|{key[1]}'
        match['fragments'] = {name: entry[name] for name in ('explained', 'matched', 'total')}
        value = composite(ppm_of(match), ppm, entry['explained'])
        match['score'] = None if value is None else round(value, 4)
    return found
