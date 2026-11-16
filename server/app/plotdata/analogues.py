"""Analogues of a feature: other features whose m/z differs from it by a
small formula step.

On the mass defect plot a formula difference is a fixed vector (so much
nominal mass, so much mass defect), and a family of related compounds lines
up along a few of them: a homologous series along CH2, a hydroxylation
along O, a reduction along H2. This finds the features at the end of those
vectors from one feature, and of pairs of them (an alcohol oxidised to an
acid is +O -H2), whether or not the compound in between was detected.

The test is on the *difference* between two m/z values measured in the same
run. That is far tighter than either mass is on its own: in the CHM350
alignments the CH2 difference between features holds to about 2 mDa while
their absolute masses are off by up to 20 ppm.
"""

import itertools

import numpy as np
import pandas as pd

from .feature import UnknownFeature

# Step -> mass, in the order they are offered.
UNITS = {
    'CH2': 14.0156500642,
    'H2': 2.0156500642,
    'O': 15.99491461956,
    'NH': 15.0108990373,
}
DEFAULT_TOLERANCE = 0.002   # Da, on the m/z difference
MAX_TOLERANCE = 0.02
MAX_RESULTS = 200


def compositions(units, combine=True):
    """Every net change of one step, and with ``combine`` of two: a list of
    ``(steps, delta)`` where ``steps`` is ``((unit, sign), ...)``. A step and
    its own reverse cancel and are left out."""
    signed = [(unit, sign) for unit in units for sign in (1, -1)]
    out = [((step,), step[1] * UNITS[step[0]]) for step in signed]
    if combine:
        for first, second in itertools.combinations_with_replacement(signed, 2):
            if first[0] == second[0] and first[1] != second[1]:
                continue
            out.append(((first, second),
                        first[1] * UNITS[first[0]] + second[1] * UNITS[second[0]]))
    return out


def find(results, compound, units=None, combine=True, tolerance=None):
    """Analogues of ``compound`` among the run's filtered features.

    Returns ``{'seed', 'units', 'combine', 'tolerance', 'analogues'}``. Each
    analogue has its ``id``, ``mz``, ``rt``, the ``steps`` from the seed
    (``[{'unit', 'sign'}]``, one or two), the m/z ``delta`` those make, and
    the ``error`` of the observed difference against it (Da). A two-step
    analogue whose halfway compound was itself found names it as ``via``.
    """
    units = list(UNITS) if units is None else [unit for unit in UNITS if unit in set(units)]
    if not units:
        raise ValueError(f'units must be among {", ".join(UNITS)}')
    tolerance = DEFAULT_TOLERANCE if tolerance is None else float(tolerance)
    if not 0 < tolerance <= MAX_TOLERANCE:
        raise ValueError(f'tolerance must be between 0 and {MAX_TOLERANCE} Da')

    iondict = results.iondict(copy=False)
    position = results.feature_positions().get(str(compound))
    if position is None:
        raise UnknownFeature(f'feature {compound!r} is not in this run')
    ids = iondict['Compound'].astype(str).to_numpy()
    masses = pd.to_numeric(iondict['m/z'], errors='coerce').to_numpy(dtype=float)
    times = (pd.to_numeric(iondict['Retention time (min)'], errors='coerce').to_numpy(dtype=float)
             if 'Retention time (min)' in iondict else np.full(len(ids), np.nan))
    seed_mz = masses[position]
    if not np.isfinite(seed_mz):
        raise ValueError(f'{compound} has no m/z')

    # Filtered features only: those are what the plots show.
    try:
        surviving = results.surviving()
        keep = np.array([name in surviving for name in ids]) & np.isfinite(masses)
    except Exception:  # noqa: BLE001 - an unreadable filtered table: search them all
        keep = np.isfinite(masses)
    keep[position] = False
    rows = np.flatnonzero(keep)
    rows = rows[np.argsort(masses[rows], kind='stable')]
    sorted_mz = masses[rows]

    # The simplest explanation of each feature found: fewest steps, then
    # the smallest error.
    found = {}
    for steps, delta in compositions(units, combine):
        target = seed_mz + delta
        low = np.searchsorted(sorted_mz, target - tolerance, side='left')
        high = np.searchsorted(sorted_mz, target + tolerance, side='right')
        for row in rows[low:high]:
            error = float(masses[row] - target)
            rank = (len(steps), abs(error))
            if row not in found or rank < found[row][0]:
                found[row] = (rank, steps, delta, error)

    direct = {steps[0]: row for row, (_, steps, _, _) in found.items() if len(steps) == 1}
    analogues = []
    for row, (_, steps, delta, error) in found.items():
        via = None
        if len(steps) == 2:
            # Through a detected one-step analogue where there is one, so the
            # second vector starts from a real point.
            for first, second in (steps, steps[::-1]):
                halfway = direct.get(first)
                if halfway is not None and abs(
                        masses[row] - masses[halfway] - second[1] * UNITS[second[0]]) <= tolerance:
                    via, steps = str(ids[halfway]), (first, second)
                    break
        time = times[row]
        analogues.append({
            'id': str(ids[row]), 'mz': float(masses[row]),
            'rt': float(time) if np.isfinite(time) else None,
            'steps': [{'unit': unit, 'sign': sign} for unit, sign in steps],
            'delta': round(delta, 6), 'error': round(error, 6), 'via': via,
        })
    analogues.sort(key=lambda entry: (len(entry['steps']), abs(entry['error'])))
    seed_time = times[position]
    return {
        'seed': {'id': str(compound), 'mz': float(seed_mz),
                 'rt': float(seed_time) if np.isfinite(seed_time) else None},
        'units': units, 'combine': bool(combine), 'tolerance': tolerance,
        'truncated': len(analogues) > MAX_RESULTS,
        'analogues': analogues[:MAX_RESULTS],
    }
