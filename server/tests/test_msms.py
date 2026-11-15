"""MS/MS search: peak cleaning, cosine scoring, and a search over a tiny
synthetic library."""

import gzip
import json
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.plotdata import msms  # noqa: E402


def test_clean_peaks_normalises_and_drops_noise():
    mz, weight = msms.clean_peaks([[100.0, 1000.0], [150.0, 5.0], [200.0, 250.0], [90, 0]])
    assert list(mz) == [100.0, 200.0]           # 5 is under 1% of the base peak
    assert np.isclose(np.linalg.norm(weight), 1.0)
    assert weight[0] == pytest.approx(2 * weight[1])   # sqrt(1000) vs sqrt(250)


def test_identical_spectra_score_one():
    mz, weight = msms.clean_peaks([[100.0, 10.0], [150.0, 20.0], [200.0, 5.0]])
    value, matched = msms.score(mz, weight, mz + 0.005, weight)
    assert value == pytest.approx(1.0) and matched == 3


def test_peaks_outside_the_tolerance_do_not_match():
    mz, weight = msms.clean_peaks([[100.0, 10.0], [150.0, 20.0]])
    assert msms.score(mz, weight, mz + 0.05, weight) == (0.0, 0)


def test_modified_cosine_matches_shifted_fragments():
    query = msms.clean_peaks([[100.0, 10.0], [214.0, 20.0], [300.0, 10.0]])
    library = msms.clean_peaks([[100.0, 10.0], [200.0, 20.0], [286.0, 10.0]])
    plain, _ = msms.score(*query, *library)
    shifted, matched = msms.score(*query, *library, shift=14.0)
    assert shifted == pytest.approx(1.0) and matched == 3
    assert plain < 0.5


def test_each_peak_is_used_once():
    query = (np.array([100.0, 100.01]), np.array([0.6, 0.8]))
    library = (np.array([100.005]), np.array([1.0]))
    value, matched = msms.score(*query, *library)
    assert matched == 1 and value == pytest.approx(0.8)


@pytest.fixture
def tiny_library(tmp_path, monkeypatch):
    spectra = [  # precursor, peaks, name, inchikey
        (300.1, [[100, 50], [150, 100], [200, 30], [250, 10]], 'Alpha', 'AAAAAAAAAAAAAA-X-N',
         'CC1OC1CCO'),
        (300.1, [[100, 50], [150, 100], [200, 31], [250, 10]], 'Alpha CE20', 'AAAAAAAAAAAAAA-X-N',
         'CC1OC1CCO'),
        (314.1, [[100, 50], [150, 100], [214, 30], [264, 10], [120, 40], [130, 20]],
         'Beta', 'BBBBBBBBBBBBBB-X-N', 'Oc1ccccc1CCN'),
        (500.0, [[60, 10], [70, 10], [80, 10]], 'Gamma', 'CCCCCCCCCCCCCC-X-N', 'CCCC'),
    ]
    precursor, ptr, mzs, weights = [], [0], [], []
    for prec, peaks, *_ in spectra:
        mz, weight = msms.clean_peaks(peaks)
        precursor.append(prec)
        mzs.append(mz.astype(np.float32))
        weights.append(weight.astype(np.float32))
        ptr.append(ptr[-1] + mz.size)
    np.savez(tmp_path / 'lib.npz', precursor=np.array(precursor), ptr=np.array(ptr),
             mz=np.concatenate(mzs), intensity=np.concatenate(weights))
    with gzip.open(tmp_path / 'meta.tsv.gz', 'wt', encoding='utf-8') as handle:
        handle.write('name\tprecursortype\tformula\tontology\tinchikey\tsmiles\t'
                     'collisionenergy\tinstrumenttype\n')
        for _, _, name, key, smiles in spectra:
            handle.write(f'{name}\t[M+H]+\t\t\t{key}\t{smiles}\t\t\n')
    monkeypatch.setattr(config, 'MSMS_LIBRARY_PATH', tmp_path / 'lib.npz')
    monkeypatch.setattr(config, 'MSMS_META_PATH', tmp_path / 'meta.tsv.gz')
    msms._LIBRARY.clear()
    yield
    msms._LIBRARY.clear()


class FakeResults:
    def __init__(self, root, features):
        self.outputdir = root
        self._iondict = pd.DataFrame({'Compound': list(features),
                                      'm/z': [mz for mz, _ in features.values()]})

    def params(self):
        return {'ppmthresh': 10}

    def iondict(self):
        return self._iondict.copy()

    def filtered(self, **_):
        return pd.DataFrame(index=self._iondict['Compound'])


def fragdb(features):
    ions = {name: types.SimpleNamespace(pattern=np.array(peaks, dtype=float))
            for name, (_, peaks) in features.items()}
    return types.SimpleNamespace(ions=ions)


FEATURES = {
    'f_alpha': (300.1012, [[100, 48], [150, 100], [200, 30], [250, 11]]),
    'f_beta_analogue': (328.1, [[100, 50], [150, 100], [228, 30], [278, 10], [120, 40], [130, 20]]),
    'f_noise': (420.0, [[61, 10], [75, 10], [93, 10]]),
}


def test_precursor_search_finds_the_exact_match_once(tiny_library, tmp_path):
    results = FakeResults(tmp_path, FEATURES)
    record = msms.search(results, fragdb(FEATURES), 0.7, match_precursor=True)
    names = {c: [msms.library().meta.iloc[h[0]]['name'] for h in found]
             for c, found in record['hits'].items()}
    assert names['f_alpha'] in (['Alpha'], ['Alpha CE20'])     # de-duplicated by structure
    assert names['f_beta_analogue'] == [] and names['f_noise'] == []
    assert json.loads((tmp_path / msms.RESULTS_FILE).read_text())['searched'] == 3
    assert msms.counts(results) == {'f_alpha': 1, 'f_beta_analogue': 0, 'f_noise': 0}


def test_analogue_search_finds_a_shifted_relative(tiny_library, tmp_path):
    results = FakeResults(tmp_path, FEATURES)
    record = msms.search(results, fragdb(FEATURES), 0.7, match_precursor=False)
    lib = msms.library()
    found = [lib.meta.iloc[h[0]]['name'] for h in record['hits']['f_beta_analogue']]
    assert 'Beta' in found
    payload = msms.matches(results, 'f_beta_analogue', 328.1)
    beta = next(m for m in payload['matches'] if m['name'] == 'Beta')
    assert beta['delta_mz'] == pytest.approx(14.0, abs=1e-6)
    assert len(beta['peak_mz']) == len(beta['peak_intensity']) == 6
    assert max(beta['peak_intensity']) == pytest.approx(100.0)
    assert payload['searched'] and payload['match_precursor'] is False


def test_a_search_for_another_library_is_ignored(tiny_library, tmp_path):
    results = FakeResults(tmp_path, FEATURES)
    msms.search(results, fragdb(FEATURES), 0.7)
    path = tmp_path / msms.RESULTS_FILE
    record = json.loads(path.read_text())
    record['library'] = 'something else'
    path.write_text(json.dumps(record))
    assert msms.stored(results) is None and msms.counts(results) is None


def test_matches_are_ranked_by_cosine(tiny_library, tmp_path):
    results = FakeResults(tmp_path, FEATURES)
    msms.search(results, fragdb(FEATURES), 0.3, match_precursor=False)
    for compound in FEATURES:
        scores = [m['score'] for m in msms.matches(results, compound, None)['matches']]
        assert scores == sorted(scores, reverse=True)


def test_hit_filters_reach_msms_hits(tiny_library, tmp_path, monkeypatch):
    pytest.importorskip('rdkit')
    from app.plotdata import feature, hits
    monkeypatch.setattr(feature, 'atlas_available', lambda: False)
    hits._LIBRARY_ANNOTATIONS.clear()
    results = FakeResults(tmp_path, FEATURES)
    passing = lambda spec: hits.passing_features(results, hits.normalise(spec))
    msms.search(results, fragdb(FEATURES), 0.7, match_precursor=False)
    # f_alpha's 200/250 fragments line up with Beta's 214/264 after the 14 Da
    # shift, so it is Beta's analogue too.
    assert passing({'substructure': 'c1ccccc1O'}) == {'f_beta_analogue', 'f_alpha'}
    assert passing({'name': 'beta'}) == {'f_beta_analogue', 'f_alpha'}
    assert 'f_noise' not in passing({'name': 'a'})

    msms.search(results, fragdb(FEATURES), 0.7, match_precursor=True)
    assert passing({'name': 'alpha'}) == {'f_alpha'}
    assert passing({'substructure': 'C1OC1'}) == {'f_alpha'}
    assert passing({'activity': ['electrophile']}) == {'f_alpha'}
    assert passing({'name': 'alpha', 'substructure': 'c1ccccc1O'}) == set()
    assert passing({'taxon': {'level': 'genus', 'value': 'Streptomyces'}}) == set()

    payload = msms.matches(results, 'f_alpha', 300.1)
    hits.annotate_library_matches(payload['matches'], hits.normalise({'substructure': 'C1OC1'}))
    assert all(match['passes'] for match in payload['matches'])
    assert 'Epoxide' in payload['matches'][0]['warheads']
    hits._LIBRARY_ANNOTATIONS.clear()


def test_precursor_match_accepts_the_feature_as_m_plus_na(tiny_library, tmp_path):
    # Alpha is an [M+H]+ entry at 300.1 (neutral 299.0927); its sodiated ion
    # is at 322.0819.
    sodiated = {'f_na': (299.092724 + msms.SODIUM, FEATURES['f_alpha'][1])}
    results = FakeResults(tmp_path, sodiated)
    record = msms.search(results, fragdb(sodiated), 0.5, match_precursor=True)
    assert record['hits']['f_na'], 'the sodiated feature found no match'
    match = msms.matches(results, 'f_na', sodiated['f_na'][0])['matches'][0]
    assert match['name'].startswith('Alpha') and match['feature_ion'] == '[M+Na]+'
    assert abs(match['delta_mz']) < 1e-3
