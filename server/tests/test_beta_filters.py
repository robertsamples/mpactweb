"""The beta filters (mass defect, isotope, adduct) and the ion mode.

Three things are pinned here: that the form's switches become the pipeline
parameters they claim to (each of the isotope and adduct filters takes over a
step of the published pipeline while it is on, and gives it back when it is
off); that one table of ion forms feeds the adduct filter, the database
search and the fragment formulae consistently in both modes; and that a real
run with the filters on reaches every results view.
"""

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import inspect as inspection                 # noqa: E402
from app import ions, mpctsession                     # noqa: E402
from app import params as params_builder              # noqa: E402
from app.core import dbsearch                         # noqa: E402
from app.plotdata import feature as featuredata       # noqa: E402
from app.plotdata import formula                      # noqa: E402
from app.schemas import AnalysisSettings              # noqa: E402

GROUPS = ['Blanks', 'Control', 'Treated']


class FakeRun:
    def __init__(self, tmp_path):
        self.output_root = tmp_path / 'output'
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.root = tmp_path


def build(tmp_path, **overrides):
    raw = tmp_path / 'rawdata'
    raw.mkdir(exist_ok=True)
    inputs = {}
    for kind, name in (('peaktable', 'peaks.csv'), ('samplelist', 'samples.csv'),
                       ('metadata', 'metadata.csv')):
        (raw / name).write_text('placeholder')
        inputs[kind] = raw / name
    settings = AnalysisSettings(blnkgrp='Blanks', expgrp='Treated', ctrgrp='Control', **overrides)
    params, _ = params_builder.build_params(FakeRun(tmp_path), settings, GROUPS, inputs)
    return params


# --- the form's defaults ---------------------------------------------------

def test_only_the_mass_defect_filter_starts_on():
    settings = AnalysisSettings()
    assert (settings.mdfil, settings.relfil_beta, settings.adductgrp) == (True, False, False)
    assert settings.ionmode == 'pos'


def test_default_parameters_leave_the_published_steps_alone(tmp_path):
    params = build(tmp_path)
    assert params.mdfil is True
    assert params.relfil is True and params.relfil_mode == 'legacy'
    assert params.decon is True and params.adductgrp is False
    assert params.graphfilters == ['cv', 'rel', 'insource']
    # Nothing of a filter that is off is handed to the pipeline.
    assert not hasattr(params, 'adduct_list') and not hasattr(params, 'iso_ppm')


# --- each beta filter takes over one step ----------------------------------

def test_the_isotope_filter_runs_in_place_of_peak_correction(tmp_path):
    # Peak correction itself is unticked: the beta switch alone turns the step on.
    params = build(tmp_path, relfil=False, relfil_beta=True, iso_ppm=8, ring_ppm=500)
    assert params.relfil is True and params.relfil_mode == 'beta'
    assert 'rel' in params.graphfilters
    assert (params.iso_ppm, params.ring_ppm, params.iso_max_n) == (8.0, 500.0, 3)
    assert params.corr_cut == 0.75


def test_peak_correction_settings_survive_the_isotope_filter(tmp_path):
    params = build(tmp_path, relfil_beta=True, ringingwin=0.4, isopeakwin=0.02)
    assert (params.ringingwin, params.isopeakwin) == (0.4, 0.02)


def test_the_adduct_filter_runs_in_place_of_deconvolution(tmp_path):
    params = build(tmp_path, decon=True, adductgrp=True, adduct_ppm=5, adduct_rt_win=0.03)
    assert params.adductgrp is True and params.decon is False
    assert 'insource' not in params.graphfilters
    assert (params.adduct_mode, params.adduct_ppm, params.adduct_rt_win) == ('pos', 5.0, 0.03)
    names = [name for name, _ in params.adduct_list]
    assert names == ['M+Na', 'M+K', 'M+NH4', 'M-H2O+H', 'M-2H2O+H']


def test_the_adduct_list_follows_the_ion_mode(tmp_path):
    params = build(tmp_path, adductgrp=True, ionmode='neg')
    assert params.adduct_mode == 'neg'
    deltas = dict(params.adduct_list)
    assert set(deltas) == {'M+Cl', 'M+FA-H', 'M+Hac-H', 'M-H2O-H', 'M-CO2-H'}
    assert deltas['M+Cl'] == pytest.approx(35.97668, abs=1e-4)
    assert deltas['M-CO2-H'] == pytest.approx(-43.98983, abs=1e-4)


def test_the_mass_defect_filter_can_be_turned_off(tmp_path):
    assert build(tmp_path, mdfil=False).mdfil is False


def test_unknown_ion_forms_are_refused():
    with pytest.raises(ValueError, match='unknown ion form'):
        AnalysisSettings(adducts=['[M+Na]+', '[M+Unobtainium]+'])


# --- the ion table ---------------------------------------------------------

def test_ion_masses_are_those_of_the_ions():
    # Differences between forms of one molecule, as measured between peaks.
    assert ions.added('[M+Na]+') - ions.added('[M+H]+') == pytest.approx(21.98194, abs=1e-5)
    assert ions.added('[M+K]+') - ions.added('[M+H]+') == pytest.approx(37.95588, abs=1e-5)
    assert ions.added('[M+NH4]+') - ions.added('[M+H]+') == pytest.approx(17.02655, abs=1e-5)
    assert ions.added('[M-H]-') == pytest.approx(-1.007276, abs=1e-6)
    assert ions.added('[M+Cl]-') == pytest.approx(34.969402, abs=1e-5)
    assert ions.added('[M+FA-H]-') == pytest.approx(44.998203, abs=1e-5)


def test_one_selection_holds_both_modes():
    chosen = AnalysisSettings().search_adducts
    assert ions.search_ions('pos', chosen) == ['[M+H]+', '[M+Na]+']
    assert ions.search_ions('neg', chosen) == ['[M-H]-', '[M+Cl]-']
    # Nothing of the mode ticked: its defaults, not an empty search.
    assert ions.search_ions('neg', ['[M+H]+']) == ['[M-H]-', '[M+Cl]-']


def test_a_run_from_before_ion_forms_searches_the_built_in_pair():
    assert ions.run_ions({}) == ['[M+H]+', '[M+Na]+']
    assert ions.run_ions({'ionmode': 'neg', 'search_adducts': ['[M-H]-', '[M+H]+']}) == ['[M-H]-']


# --- fragment formulae for anions ------------------------------------------

def test_anion_masses():
    glucose = 'C6H12O6'
    assert formula.ion_mz(formula.ion(glucose, '[M-H]-'), -1) == pytest.approx(179.05611, abs=1e-4)
    assert formula.ion_mz(formula.ion(glucose, '[M+Cl]-'), -1) == pytest.approx(215.03279, abs=1e-4)
    assert formula.ion_mz(formula.ion(glucose, '[M+H]+'), 1) == pytest.approx(181.07066, abs=1e-4)


def test_a_formula_is_only_its_own_ion_in_its_own_polarity():
    assert formula.ion('C5H4NO2-', '[M-H]-') == {'C': 5, 'H': 4, 'N': 1, 'O': 2}
    assert formula.ion('C5H4NO2-', '[M+H]+') is None
    assert formula.ion('C12H17N4OS+', '[M-H]-') is None


def test_fragments_of_an_anion_are_annotated():
    # Deprotonated benzoic acid losing CO2: C7H5O2- (121.0295) -> C6H5- (77.0397).
    parent = formula.ion('C7H6O2', '[M-H]-')
    pattern = [(77.0397, 100.0), (121.0295, 40.0)]
    report = formula.report(pattern, 121.0295, 10, [('C7H6O2', '[M-H]-')])
    entry = report['C7H6O2|[M-H]-']
    assert entry['ion'] == formula.text(parent) == 'C7H5O2'
    fragment = next(peak for peak in entry['peaks'] if peak['index'] == 0)
    assert (fragment['formula'], fragment['loss']) == ('C6H5', 'CO2')
    # The same spectrum read as a cation's does not fit at all.
    assert formula.report(pattern, 121.0295, 10, [('C7H6O2', '[M+H]+')]) == {}


# --- database search by ion form -------------------------------------------

def _atlas():
    neutral = np.array([180.06339, 300.10000, 420.20000, 264.07500])
    return pd.DataFrame({
        'compound_name': ['a', 'b', 'c', 'd'],
        'compound_molecular_formula': ['C6H12O6', '', '', ''],
        'compound_accurate_mass': neutral,
        'compound_m_plus_h': neutral + 1.007276,
        'compound_m_plus_na': neutral + 22.989218,
    }, index=pd.Index([1, 2, 3, 4], name='compound_id'))


def _outputs(tmp_path, masses):
    ids = [f'f{i}' for i in range(len(masses))]
    pd.DataFrame({'Compound': ids, 'm/z': masses, 'Retention time (min)': 1.0}).to_csv(
        tmp_path / 'iondict.csv', index=False)
    rows = [['', '', '', 'G'], ['', '', '', 'S'], ['Compound', 'm/z', 'Retention time (min)', 'inj']]
    rows += [[i, m, 1.0, 10] for i, m in zip(ids[:-1], masses[:-1])]     # the last one was filtered out
    pd.DataFrame(rows).to_csv(tmp_path / 'x_filtered.csv', index=False, header=False)
    return ids


def test_negative_mode_matches_by_the_negative_ions():
    atlas = _atlas()
    as_anion = featuredata.matches_for_mass(atlas, 180.06339 - 1.007276, 10, ['[M-H]-', '[M+Cl]-'])
    assert [(m['compound_name'], m['adduct']) for m in as_anion] == [('a', '[M-H]-')]
    chloride = featuredata.matches_for_mass(atlas, 300.1 + 34.969402, 10, ['[M-H]-', '[M+Cl]-'])
    assert [(m['compound_name'], m['adduct']) for m in chloride] == [('b', '[M+Cl]-')]
    # The same mass means nothing as a positive ion.
    assert featuredata.matches_for_mass(atlas, 180.06339 - 1.007276, 10) == []


def test_the_general_search_counts_what_the_vendored_one_counts(tmp_path):
    atlas = _atlas()
    masses = [181.070666, 323.089218, 500.0, 265.082276, 421.207276]
    _outputs(tmp_path, masses)
    dbsearch.search_npatlas(tmp_path, 'x', atlas, 10)
    vendored = pd.read_csv(tmp_path / 'iondict.csv', index_col=0)['hits']

    _outputs(tmp_path, masses)
    from app.core import csvcache
    csvcache.invalidate()
    searched, matched = featuredata.search_atlas(tmp_path, 'x', atlas, 10, ions.LEGACY_SEARCH)
    general = pd.read_csv(tmp_path / 'iondict.csv', index_col=0)['hits']

    pd.testing.assert_series_equal(vendored, general)
    assert (searched, matched) == (4, 3)
    assert np.isnan(general['f4'])          # not searched: it did not pass filtering


# --- sessions ---------------------------------------------------------------

def _saved(**attrs):
    base = dict(graphfilters=['cv', 'rel', 'insource'], statstgrps=['A', 'B'], queries=[])
    base.update(attrs)
    return SimpleNamespace(**base)


def test_a_session_from_before_the_beta_filters_gets_the_defaults():
    settings = mpctsession.settings_from(_saved(), [])
    assert (settings['mdfil'], settings['relfil_beta'], settings['adductgrp']) == (True, False, False)
    assert settings['ionmode'] == 'pos'
    assert settings['adducts'] == AnalysisSettings().adducts
    AnalysisSettings(**settings)            # and it is a form the server accepts


def test_a_session_keeps_the_beta_settings_it_was_saved_with():
    warnings = []
    settings = mpctsession.settings_from(_saved(
        mdfil=False, md_halogen=True, relfil_mode='beta', iso_ppm=6.0, adductgrp=True,
        adduct_mode='neg', adduct_ppm=4.0, adducts=['[M+Cl]-', '[M+Gone]-'],
        search_adducts=['[M-H]-'], corr_cut=0.8, ring_ratio_max=7.0), warnings)
    assert (settings['mdfil'], settings['md_halogen']) == (False, True)
    assert settings['relfil_beta'] is True and settings['iso_ppm'] == 6.0
    assert settings['adductgrp'] is True and settings['ionmode'] == 'neg'
    assert settings['adducts'] == ['[M+Cl]-']           # a form this version lacks is dropped
    assert settings['search_adducts'] == ['[M-H]-']
    assert settings['corr_cut'] == 0.8
    assert settings['ring_ratio_max'] == 0.01 and any('ring_ratio_max' in w for w in warnings)
    AnalysisSettings(**settings)


def test_parameters_round_trip_through_a_session(tmp_path):
    params = build(tmp_path, mdfil=False, relfil_beta=True, adductgrp=True, ionmode='neg',
                   adducts=['[M+Cl]-'], search_adducts=['[M-H]-', '[M+FA-H]-'], iso_ppm=7)
    settings = mpctsession.settings_from(params, [])
    assert (settings['mdfil'], settings['relfil_beta'], settings['adductgrp']) == (False, True, True)
    assert (settings['ionmode'], settings['iso_ppm']) == ('neg', 7.0)
    assert settings['adducts'] == ['[M+Cl]-']
    assert settings['search_adducts'] == ['[M-H]-', '[M+FA-H]-']


# --- what an uploaded file says about its polarity --------------------------

@pytest.mark.parametrize('text,expected', [
    ('NAME: x\nPRECURSORTYPE: [M-H]-\nIONMODE: Negative\nNum Peaks: 0\n', 'neg'),
    ('Name: Unknown\nCharge: 1+\nPrecursorMZ: 102.15\n', 'pos'),
    ('BEGIN IONS\nPEPMASS=301.1\nCHARGE=1-\nEND IONS\n', 'neg'),
    ('Alignment ID\tAverage Mz\tAdduct type\n0\t150.02\t[M+H]+\n1\t151.03\t[M+H]+\n', 'pos'),
    ('Compound,m/z,Retention time (min),a,b\n1,150.02,1.0,5,6\n', None),
])
def test_ionmode_hint(tmp_path, text, expected):
    path = tmp_path / 'file.txt'
    path.write_text(text)
    assert inspection.ionmode_hint(path) == expected


# --- a real run with every beta filter on -----------------------------------

@pytest.fixture(scope='module')
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp('mpact-beta')
    os.environ['MPACT_DATA_ROOT'] = str(root)
    os.environ['MPACT_DEV'] = '1'
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app
    config.reload()
    with TestClient(app) as test_client:
        yield test_client


def _run(client, **overrides):
    info = client.get('/api/files/demo').json()
    if not any(d['id'] == 'pty087i2' and d['available'] for d in info['datasets']):
        pytest.skip('the PTY087I2 demo dataset is not installed')
    records = client.post('/api/files/demo', json={'id': 'pty087i2'}).json()
    inputs = {record['kind']: record['id'] for record in records}
    groups = client.post('/api/files/groups', json={
        kind: inputs[kind] for kind in ('peaktable', 'samplelist', 'metadata')}).json()
    settings = client.get('/api/runs/defaults').json()['settings']
    settings.update(blnkgrp=groups['suggested']['blank'], expgrp=groups['suggested']['experimental'],
                    ctrgrp=groups['suggested']['control'], **overrides)
    started = client.post('/api/runs', json={'inputs': inputs, 'settings': settings})
    assert started.status_code == 200, started.text
    run_id = started.json()['run_id']
    deadline = time.time() + 900
    while time.time() < deadline:
        status = client.get(f'/api/runs/{run_id}/status').json()
        if status['state'] in ('complete', 'failed', 'cancelled'):
            break
        time.sleep(0.5)
    assert status['state'] == 'complete', status
    return run_id


@pytest.fixture(scope='module')
def beta_run(client):
    return _run(client, mdfil=True, relfil_beta=True, adductgrp=True)


def _plot(client, run_id, name):
    response = client.post(f'/api/runs/{run_id}/plots/{name}', json={})
    assert response.status_code == 200, response.text
    return response.json()


def test_every_beta_filter_reports_its_yield(client, beta_run):
    summary = _plot(client, beta_run, 'summary')
    counts = {row['label']: row['count'] for row in summary['filters']}
    assert counts['Failing mass defect filtering (beta)'] > 0
    assert counts['Removed by adduct filtering (beta)'] > 0
    assert 'Failing isotope filtering (beta)' in counts
    # The steps the beta filters stood in for did not run.
    assert 'Failing peak correction filtering' not in counts
    assert 'Failing in-source/deconvolution filtering' not in counts


def test_removed_features_are_classed_and_not_plotted(client, beta_run):
    summary = _plot(client, beta_run, 'summary')
    passing = summary['counts']['features_passing']
    quality = {series['name']: series['count'] for series in _plot(client, beta_run, 'quality')['series']}
    assert quality['Mass defect (beta)'] > 0 and quality['Adducts (beta)'] > 0
    assert quality['High Quality'] == passing
    assert sum(quality.values()) == summary['counts']['features_total']
    treemap = {item['label'] for item in _plot(client, beta_run, 'treemap')['items']}
    assert {'Mass defect', 'Adducts', 'High Quality'} <= treemap
    plotted = sum(series['count'] for series in _plot(client, beta_run, 'mzrt')['series'])
    assert plotted == passing


def test_the_attribution_table_can_be_downloaded(client, beta_run):
    keys = [item['key'] for item in client.get(f'/api/runs/{beta_run}/downloads').json()]
    assert 'adduct_attribution' in keys
    body = client.get(f'/api/runs/{beta_run}/downloads/adduct_attribution').text
    assert body.startswith('Compound,m/z,Retention time (min),adduct_form,is_main')


def test_a_negative_mode_run_searches_as_anions_and_has_no_msms_search(client):
    run_id = _run(client, ionmode='neg')
    searched = client.post(f'/api/runs/{run_id}/dbsearch', json={})
    assert searched.status_code == 200, searched.text
    assert searched.json()['ions'] == ['[M-H]-', '[M+Cl]-']
    msms = client.post(f'/api/runs/{run_id}/msmssearch', json={})
    assert msms.status_code == 422 and 'positive-mode' in msms.json()['detail']


def test_a_run_without_beta_filters_shows_none_of_their_classes(client):
    run_id = _run(client, mdfil=False)
    quality = [series['name'] for series in _plot(client, run_id, 'quality')['series']]
    assert quality == ['Mispicked', 'Blank features', 'Nonreproducable', 'In-source ions',
                       'High Quality']
    labels = [row['label'] for row in _plot(client, run_id, 'summary')['filters']]
    assert not any('beta' in label for label in labels)
    keys = [item['key'] for item in client.get(f'/api/runs/{run_id}/downloads').json()]
    assert 'adduct_attribution' not in keys
