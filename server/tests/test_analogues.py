"""Analogue search: features one or two formula steps from another."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.plotdata import analogues  # noqa: E402
from app.plotdata.feature import UnknownFeature  # noqa: E402

CH2, H2, O, NH = (analogues.UNITS[unit] for unit in ('CH2', 'H2', 'O', 'NH'))
SEED = 445.2000

FEATURES = {
    'seed': SEED,
    'methyl': SEED + CH2 + 0.0004,          # a homologue
    'hydroxy': SEED + O - 0.0006,           # +O
    'reduced': SEED + H2,                   # +H2
    'acid': SEED + O - H2 + 0.0003,         # +O -H2, nothing detected in between
    'hydroxy_methyl': SEED + O + CH2,       # +O +CH2, reachable through 'hydroxy'
    'near_miss': SEED + CH2 + 0.006,        # 6 mDa off a CH2 step
    'unrelated': SEED + 7.7777,
    'filtered_out': SEED + NH,              # an NH step away, but did not pass filtering
}


class FakeResults:
    def __init__(self, features, surviving=None):
        self._iondict = pd.DataFrame({'Compound': list(features), 'm/z': list(features.values()),
                                      'Retention time (min)': [1.0 + i for i in range(len(features))]})
        self._surviving = frozenset(surviving if surviving is not None else features)

    def iondict(self, copy=True):
        return self._iondict

    def feature_positions(self):
        return {name: i for i, name in enumerate(self._iondict['Compound'])}

    def surviving(self):
        return self._surviving


@pytest.fixture
def results():
    return FakeResults(FEATURES, surviving=set(FEATURES) - {'filtered_out'})


def steps(entry):
    return [(step['unit'], step['sign']) for step in entry['steps']]


def test_single_steps_are_found_within_tolerance(results):
    found = {entry['id']: entry for entry in analogues.find(results, 'seed')['analogues']}
    assert steps(found['methyl']) == [('CH2', 1)]
    assert steps(found['hydroxy']) == [('O', 1)]
    assert steps(found['reduced']) == [('H2', 1)]
    assert found['hydroxy']['error'] == pytest.approx(-0.0006, abs=1e-6)
    assert 'near_miss' not in found and 'unrelated' not in found
    assert 'seed' not in found


def test_two_steps_are_found_without_the_compound_in_between():
    # Neither the alcohol (+O) nor the dehydro compound (-H2) was detected.
    alone = FakeResults({name: FEATURES[name] for name in ('seed', 'methyl', 'acid')})
    found = {entry['id']: entry for entry in analogues.find(alone, 'seed')['analogues']}
    acid = found['acid']
    assert sorted(steps(acid)) == [('H2', -1), ('O', 1)]
    assert acid['via'] is None
    assert acid['delta'] == pytest.approx(O - H2, abs=1e-6)


def test_a_two_step_analogue_goes_through_a_detected_one(results):
    found = {entry['id']: entry for entry in analogues.find(results, 'seed')['analogues']}
    # The acid is -H2 on from the detected alcohol.
    assert found['acid']['via'] == 'hydroxy'
    assert steps(found['acid']) == [('O', 1), ('H2', -1)]
    entry = found['hydroxy_methyl']
    # Both halves were detected; whichever is named, its step comes first.
    assert entry['via'] in ('hydroxy', 'methyl')
    first = {'hydroxy': ('O', 1), 'methyl': ('CH2', 1)}[entry['via']]
    assert steps(entry)[0] == first and len(entry['steps']) == 2


def test_steps_work_downwards_too(results):
    found = {entry['id']: entry for entry in analogues.find(results, 'methyl')['analogues']}
    assert steps(found['seed']) == [('CH2', -1)]


def test_options_narrow_the_search(results):
    only = analogues.find(results, 'seed', units=['CH2'], combine=False)
    assert [entry['id'] for entry in only['analogues']] == ['methyl']
    assert only['units'] == ['CH2'] and only['combine'] is False
    singles = {entry['id'] for entry in analogues.find(results, 'seed', combine=False)['analogues']}
    assert singles == {'methyl', 'hydroxy', 'reduced'}
    loose = {entry['id'] for entry in analogues.find(results, 'seed', tolerance=0.007)['analogues']}
    assert 'near_miss' in loose


def test_only_filtered_features_are_analogues(results):
    assert 'filtered_out' not in {e['id'] for e in analogues.find(results, 'seed')['analogues']}
    everything = FakeResults(FEATURES)
    assert 'filtered_out' in {e['id'] for e in analogues.find(everything, 'seed')['analogues']}


def test_bad_requests_are_refused(results):
    with pytest.raises(UnknownFeature):
        analogues.find(results, 'nope')
    with pytest.raises(ValueError):
        analogues.find(results, 'seed', tolerance=5)
    with pytest.raises(ValueError):
        analogues.find(results, 'seed', units=['C60'])


def test_a_step_and_its_reverse_are_not_a_pair():
    for steps_, delta in analogues.compositions(list(analogues.UNITS)):
        assert abs(delta) > 0.5
    assert len(analogues.compositions(['CH2'], combine=False)) == 2
    # Four steps each way, and the 32 distinct pairs of them.
    assert len(analogues.compositions(list(analogues.UNITS))) == 8 + 32
