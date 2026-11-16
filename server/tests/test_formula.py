"""Fragment formulae: sub-formulae of a candidate parent, and the score a
candidate gets from how much of a spectrum it accounts for."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.plotdata import formula  # noqa: E402

# Caffeine, [M+H]+ 195.0877, with three of its real fragments.
CAFFEINE = [[195.0877, 300.0], [138.0662, 100.0], [110.0713, 40.0], [69.0447, 20.0]]


def test_parse_reads_counts_and_charge():
    assert formula.parse('C8H10N4O2') == ({'C': 8, 'H': 10, 'N': 4, 'O': 2}, 0)
    assert formula.parse('C12H17N4OS+') == ({'C': 12, 'H': 17, 'N': 4, 'O': 1, 'S': 1}, 1)
    assert formula.parse('C6H5Cl') == ({'C': 6, 'H': 5, 'Cl': 1}, 0)
    for bad in ('', None, 'C8H10(NO)2', 'Xx2', '[13C]H4'):
        assert formula.parse(bad) is None


def test_ion_adds_the_adduct_and_weighs_right():
    ion = formula.ion('C8H10N4O2', '[M+H]+')
    assert formula.text(ion) == 'C8H11N4O2'
    assert formula.ion_mz(ion) == pytest.approx(195.08765, abs=1e-4)
    assert formula.ion_mz(formula.ion('C8H10N4O2', '[M+Na]+')) == pytest.approx(217.0696, abs=1e-4)
    # A formula written as a cation is already the ion.
    assert formula.text(formula.ion('C5H14NO+', '[M+H]+')) == 'C5H14NO'
    # Water cannot be lost from a molecule with no oxygen; unknown adducts.
    assert formula.ion('C6H6', '[M-H2O+H]+') is None
    assert formula.ion('C8H10N4O2', '[2M+H]+') is None


def test_fragments_are_sub_formulae_of_the_parent():
    ion = formula.ion('C8H10N4O2', '[M+H]+')
    found = formula.annotate(ion, [138.0662, 110.0713, 69.0447], formula.fragment_tolerance(10))
    assert [peak['formula'] for peak in found] == ['C6H8N3O', 'C5H8N3', 'C3H5N2']
    assert all(not peak['radical'] and abs(peak['error']) < 0.001 for peak in found)


def test_a_parent_without_the_atoms_cannot_explain_the_fragment():
    tolerance = formula.fragment_tolerance(10)
    # m/z 111.0002 is chlorophenyl, C6H4Cl+.
    assert formula.annotate(formula.ion('C7H7ClO', '[M+H]+'), [111.0002],
                            tolerance)[0]['formula'] == 'C6H4Cl'
    # A parent with no chlorine has nothing within tolerance, however many
    # carbons, hydrogens and oxygens it has...
    assert formula.annotate(formula.ion('C12H18O6', '[M+H]+'), [111.0002], tolerance) == [None]
    # ...and neither has one with the chlorine but too few carbons.
    assert formula.annotate(formula.ion('C4H9ClO', '[M+H]+'), [111.0002], tolerance) == [None]


def test_no_fragment_has_more_of_an_atom_than_the_parent():
    ion = formula.ion('C10H12N2O3', '[M+H]+')
    mzs = [60.0444, 72.0444, 91.0542, 105.0335, 119.0491, 133.0396, 150.0550, 163.0502, 191.0815]
    for peak in formula.annotate(ion, mzs, formula.fragment_tolerance(10)):
        if peak is None:
            continue
        for element, count in peak['counts'].items():
            assert count <= ion[element]


def test_report_scores_fragments_below_the_precursor():
    out = formula.report(CAFFEINE, 195.0877, 10,
                         [('C8H10N4O2', '[M+H]+'), ('C7H14O6', '[M+H]+'), ('nonsense', '[M+H]+'),
                          ('C12H18O2', '[M+H]+')])
    # Unreadable formulae are left out, and so is one whose ion is not this
    # feature's (C12H18O2 is 50 mDa away).
    assert set(out) == {'C8H10N4O2|[M+H]+', 'C7H14O6|[M+H]+'}
    right = out['C8H10N4O2|[M+H]+']
    # The precursor is labelled but not scored: three fragments, all explained.
    assert (right['matched'], right['total'], right['explained']) == (3, 3, 1.0)
    assert right['ion'] == 'C8H11N4O2'
    labels = {peak['index']: peak for peak in right['peaks']}
    assert labels[0]['precursor'] and labels[0]['loss'] == ''
    assert labels[1]['formula'] == 'C6H8N3O' and labels[1]['loss'] == 'C2H3NO'
    # An isobar with no nitrogen (7 ppm away) cannot account for all of them.
    assert out['C7H14O6|[M+H]+']['explained'] < right['explained']


def test_small_peaks_do_not_count_against_a_candidate():
    # A 2% peak no formula fits is under the scoring floor.
    noisy = CAFFEINE + [[123.4567, 2.0]]
    out = formula.report(noisy, 195.0877, 10, [('C8H10N4O2', '[M+H]+')])['C8H10N4O2|[M+H]+']
    assert out['explained'] == 1.0 and out['total'] == 3
    # A 50% one is not.
    loud = CAFFEINE + [[123.4567, 50.0]]
    out = formula.report(loud, 195.0877, 10, [('C8H10N4O2', '[M+H]+')])['C8H10N4O2|[M+H]+']
    assert out['total'] == 4 and out['matched'] == 3 and 0.7 < out['explained'] < 0.8


def test_composite_needs_both_mass_and_fragments():
    both = formula.composite(1.0, 10, 1.0)
    assert both > formula.composite(1.0, 10, 0.2) > formula.composite(8.0, 10, 0.2)
    assert both > formula.composite(8.0, 10, 1.0)
    # Unexplained fragments halve a score at most; no spectrum, no score.
    assert formula.composite(0.0, 10, 0.0) == pytest.approx(0.5)
    assert formula.composite(1.0, 10, None) is None


def test_score_matches_annotates_in_place():
    matches = [{'f': 'C8H10N4O2', 'a': '[M+H]+', 'ppm': 0.5},
               {'f': 'C7H14O6', 'a': '[M+H]+', 'ppm': 6.0},
               {'f': '', 'a': '[M+H]+', 'ppm': 1.0}]
    found = formula.score_matches(matches, CAFFEINE, 195.0877, 10,
                                  key_of=lambda m: (m['f'], m['a']) if m['f'] else None,
                                  ppm_of=lambda m: m['ppm'])
    assert matches[0]['fragment_key'] in found
    assert matches[0]['fragments'] == {'explained': 1.0, 'matched': 3, 'total': 3}
    assert matches[0]['score'] > matches[1]['score']
    assert 'score' not in matches[2]
