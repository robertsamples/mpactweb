"""Ion forms, by ionisation mode.

One table for everything that has to know which ion a feature's m/z might be:
the adduct filter (the mass differences between forms of one molecule), the
database search (a known compound's m/z as each form) and fragment formulae
(the atoms each form adds). The atoms are ``plotdata.formula.ADDUCTS``; this
module says which forms each mode offers, in what order, which are on by
default, and works out their masses from those atoms.

Every form here is singly charged.
"""

from .plotdata import formula

MODES = ('pos', 'neg')
MODE_NAMES = {'pos': 'Positive', 'neg': 'Negative'}

# The ion the others are measured from.
BASE = {'pos': '[M+H]+', 'neg': '[M-H]-'}

# (label, kind) in the order they are offered. An 'adduct' adds something to
# the molecule, a 'loss' is the base ion less a neutral fragment.
FORMS = {
    'pos': (
        ('[M+H]+', 'base'),
        ('[M+Na]+', 'adduct'), ('[M+K]+', 'adduct'), ('[M+NH4]+', 'adduct'),
        ('[M-H2O+H]+', 'loss'), ('[M-2H2O+H]+', 'loss'),
    ),
    'neg': (
        ('[M-H]-', 'base'),
        ('[M+Cl]-', 'adduct'), ('[M+FA-H]-', 'adduct'), ('[M+Hac-H]-', 'adduct'),
        ('[M+Na-2H]-', 'adduct'), ('[M+K-2H]-', 'adduct'),
        ('[M-H2O-H]-', 'loss'), ('[M-2H2O-H]-', 'loss'), ('[M-CO2-H]-', 'loss'),
        ('[M-CO-H]-', 'loss'), ('[M-SO3-H]-', 'loss'), ('[M-H2SO4-H]-', 'loss'),
        ('[M-Hex-H]-', 'loss'), ('[M-GlcA-H]-', 'loss'), ('[M-Taurine-H]-', 'loss'),
    ),
}

# Forms the adduct filter looks for unless told otherwise.
GROUPING_DEFAULT = {
    'pos': ('[M+Na]+', '[M+K]+', '[M+NH4]+', '[M-H2O+H]+', '[M-2H2O+H]+'),
    'neg': ('[M+Cl]-', '[M+FA-H]-', '[M+Hac-H]-', '[M-H2O-H]-', '[M-CO2-H]-'),
}

# Forms a database search tries. The positive pair is what the search has
# always used (NPAtlas publishes exactly those two columns).
SEARCH_DEFAULT = {
    'pos': ('[M+H]+', '[M+Na]+'),
    'neg': ('[M-H]-', '[M+Cl]-'),
}
LEGACY_SEARCH = SEARCH_DEFAULT['pos']

_KNOWN = {label: mode for mode in MODES for label, _ in FORMS[mode]}


def known(label):
    return label in _KNOWN


def mode_of(label):
    return _KNOWN[label]


def labels(mode):
    return [label for label, _ in FORMS[mode]]


def added(label):
    """The ion's m/z less the neutral molecule's monoisotopic mass: the atoms
    it gains or loses, and the electron its charge takes or leaves."""
    return formula.mass(formula.ADDUCTS[label]) - formula.charge_of(label) * formula.ELECTRON


def default_selection(table):
    """Both modes' defaults in one list. A selection is kept as one list of
    labels, and a label belongs to one mode, so switching mode and back
    leaves each mode's ticks as they were."""
    return [label for mode in MODES for label in table[mode]]


def selected(mode, chosen):
    """``chosen`` narrowed to ``mode``'s forms, in table order."""
    wanted = set(chosen or ())
    return [label for label in labels(mode) if label in wanted]


def core_name(label):
    """The form as the pipeline's adduct filter writes it: ``M+Na`` for
    ``[M+Na]+``. Its own base ion is ``M+H`` / ``M-H``, so the two read alike
    in ``adduct_form`` and the attribution table."""
    return label[1:label.rindex(']')]


def grouping_list(mode, chosen):
    """``[(name, delta), ...]`` for ``filter.adductgroup``: each selected form
    of ``mode`` with its m/z difference from the base ion."""
    base = added(BASE[mode])
    return [(core_name(label), added(label) - base)
            for label in selected(mode, chosen) if label != BASE[mode]]


def search_ions(mode, chosen):
    """The forms a database search tries in ``mode``; the mode's defaults if
    none of its forms is selected."""
    return selected(mode, chosen) or list(SEARCH_DEFAULT[mode])


def run_mode(params):
    """A run's ion mode, from its parameter snapshot. Runs made before there
    was a choice are positive."""
    return 'neg' if str(params.get('ionmode', 'pos')).lower().startswith('neg') else 'pos'


def run_ions(params):
    """The forms a run's database search tries, from its parameter snapshot.
    A run from before the forms could be chosen has no list and gets the pair
    that was built in then."""
    chosen = params.get('search_adducts')
    if chosen is None:
        return list(LEGACY_SEARCH)
    return search_ions(run_mode(params), [label for label in chosen if known(label)])


def catalogue():
    """The table as the settings form needs it."""
    out = {}
    for mode in MODES:
        base = added(BASE[mode])
        out[mode] = {
            'name': MODE_NAMES[mode],
            'base': BASE[mode],
            'forms': [{'label': label, 'kind': kind, 'added': round(added(label), 6),
                       'delta': round(added(label) - base, 6)}
                      for label, kind in FORMS[mode]],
            'grouping_default': list(GROUPING_DEFAULT[mode]),
            'search_default': list(SEARCH_DEFAULT[mode]),
        }
    return out
