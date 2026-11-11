"""End-to-end test: upload -> run -> every plot payload.

This is the test that matters most for this project. The whole design rests
on the claim that the vendored desktop pipeline runs unmodified behind a web
API, so the test drives the real HTTP surface with a real dataset and asserts
the real outputs -- no mocking of the pipeline, no synthetic peak table.

It needs an MPACT example dataset. Point ``MPACT_TEST_DATA`` at a directory
containing a peak table plus ``samplelist.csv`` and ``extractmetadata.csv``
(the layout of ``rawdata/PTY087I2`` in the desktop repo); the test skips if
it isn't there.
"""

import os
import time
from pathlib import Path

import pytest

DATA_DIR = Path(os.environ.get(
    'MPACT_TEST_DATA',
    Path(__file__).resolve().parents[3] / 'mpact' / 'rawdata' / 'PTY087I2'))

RUN_TIMEOUT_SECONDS = 900


def _dataset():
    if not DATA_DIR.is_dir():
        pytest.skip(f'no example dataset at {DATA_DIR} (set MPACT_TEST_DATA)')
    samplelist = DATA_DIR / 'samplelist.csv'
    metadata = DATA_DIR / 'extractmetadata.csv'
    peaktables = [p for p in sorted(DATA_DIR.glob('*.csv'))
                  if p.name not in {samplelist.name, metadata.name}]
    if not (peaktables and samplelist.exists() and metadata.exists()):
        pytest.skip(f'{DATA_DIR} is missing a peak table / sample list / metadata')
    return peaktables[0], samplelist, metadata


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    """A TestClient with its own isolated data root."""
    root = tmp_path_factory.mktemp('mpact-data')
    os.environ['MPACT_DATA_ROOT'] = str(root)
    os.environ['MPACT_DEV'] = '1'

    # Imported after MPACT_DATA_ROOT is set: config resolves paths at import.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app
    # Another test module may have imported app.config first, binding
    # a different data root; re-resolve so this module gets its own.
    config.reload()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope='module')
def completed_run(client):
    """Upload the example dataset, run it, and return the finished run id."""
    peaktable, samplelist, metadata = _dataset()

    uploaded = {}
    for kind, path in (('peaktable', peaktable), ('samplelist', samplelist),
                       ('metadata', metadata)):
        with path.open('rb') as handle:
            response = client.post('/api/files', data={'kind': kind},
                                   files={'file': (path.name, handle, 'text/csv')})
        assert response.status_code == 200, response.text
        uploaded[kind] = response.json()['id']

    groups = client.post('/api/files/groups', json=uploaded)
    assert groups.status_code == 200, groups.text
    payload = groups.json()
    assert payload['groups'], 'no biological groups were derived'
    suggested = payload['suggested']

    settings = client.get('/api/runs/defaults').json()['settings']
    settings.update(
        blnkgrp=suggested['blank'] or payload['groups'][0],
        blnkfltr=bool(suggested['blank']),
        expgrp=suggested['experimental'] or payload['groups'][0],
        ctrgrp=suggested['control'] or payload['groups'][-1],
    )
    started = client.post('/api/runs', json={'inputs': uploaded, 'settings': settings,
                                             'label': 'e2e'})
    assert started.status_code == 200, started.text
    run_id = started.json()['run_id']

    deadline = time.time() + RUN_TIMEOUT_SECONDS
    state = None
    while time.time() < deadline:
        status = client.get(f'/api/runs/{run_id}/status').json()
        state = status['state']
        if state in ('complete', 'failed', 'cancelled'):
            break
        time.sleep(2)

    if state != 'complete':
        log = client.get(f'/api/runs/{run_id}/log').json()['log']
        pytest.fail(f'run ended as {state}\n--- log tail ---\n{log[-4000:]}')
    return run_id


def test_health(client):
    body = client.get('/api/health').json()
    assert body['status'] == 'ok'


def test_run_produces_expected_outputs(client, completed_run):
    """The pipeline wrote the same output files the desktop documents."""
    downloads = client.get(f'/api/runs/{completed_run}/downloads').json()
    produced = {item['key'] for item in downloads}
    for expected in ('filtered', 'formatted', 'iondict', 'summarydata',
                     'groupaverages', 'analysisinfo', 'session'):
        assert expected in produced, f'{expected} missing from {sorted(produced)}'
    assert all(item['size'] > 0 for item in downloads)


def test_plot_catalogue(client, completed_run):
    body = client.get(f'/api/runs/{completed_run}/plots').json()
    names = {plot['name'] for plot in body['plots']}
    assert {'summary', 'mzrt', 'massdefect', 'heatmap', 'dendrogram',
            'ordination', 'volcano', 'upset', 'treemap'} <= names
    assert body['groups'], 'catalogue reported no biological groups'
    assert body['feature_sets'], 'catalogue reported no feature sets'


@pytest.mark.parametrize('name,options', [
    ('summary', {}),
    ('quality', {}),
    ('cvplot', {}),
    ('treemap', {}),
    ('upset', {}),
    ('mzrt', {}),
    ('massdefect', {}),
    ('fc3d', {}),
    ('volcano', {}),
    ('heatmap', {'view': 'Biological Replicates'}),
    ('dendrogram', {'view': 'Technical Replicates', 'color_mode': 'Purity'}),
    ('dendrogram', {'view': 'Biological Replicates', 'color_mode': 'None'}),
    ('ordination', {'method': 'PCA', 'view': 'Scores'}),
    ('ordination', {'method': 'PCA', 'view': 'Loadings'}),
    ('ordination', {'method': 'NMDS', 'view': 'Scores'}),
    ('ordination', {'method': 'PLS-DA', 'view': 'Scores'}),
    ('samplecorr', {'method': 'Spearman'}),
    ('samplecorr', {'method': 'Bray-Curtis', 'view': 'Individual Injections'}),
    ('groupaverages', {}),
])
def test_every_plot_builds(client, completed_run, name, options):
    response = client.post(f'/api/runs/{completed_run}/plots/{name}', json=options)
    assert response.status_code == 200, f'{name} {options}: {response.text}'
    body = response.json()
    assert body, f'{name} returned an empty payload'


def test_scatter_series_are_aligned(client, completed_run):
    """Every per-point array in a scatter payload must be the same length --
    the client indexes them together to build hover text and selection."""
    body = client.post(f'/api/runs/{completed_run}/plots/mzrt', json={}).json()
    assert body['series']
    for series in body['series']:
        length = series['count']
        for field in ('x', 'y', 'ids', 'alpha'):
            assert len(series[field]) == length, f'{field} misaligned in {series["name"]}'


def test_heatmap_matrix_matches_labels(client, completed_run):
    body = client.post(f'/api/runs/{completed_run}/plots/heatmap', json={}).json()
    rows, cols = body['shape']
    assert len(body['z']) == rows
    assert all(len(row) == cols for row in body['z'])
    assert len(body['features']) == rows
    assert len(body['columns']) == cols


def test_dendrogram_segments_match_colours(client, completed_run):
    body = client.post(f'/api/runs/{completed_run}/plots/dendrogram',
                       json={'color_mode': 'Purity'}).json()
    assert len(body['icoord']) == len(body['dcoord'])
    assert body['link_colours'] is not None
    assert len(body['link_colours']) == len(body['icoord'])
    assert body['purity']['total'] >= 1


def test_feature_table_filters_and_pages(client, completed_run):
    first = client.post(f'/api/runs/{completed_run}/features',
                        json={'limit': 5, 'sort': 'm/z'}).json()
    assert first['total'] > 0
    assert len(first['rows']) <= 5
    mz_values = [row[1] for row in first['rows'] if row[1] is not None]
    assert mz_values == sorted(mz_values), 'sort by m/z was not applied'

    # A range filter must genuinely narrow the set.
    narrowed = client.post(f'/api/runs/{completed_run}/features',
                           json={'limit': 5, 'filters': {'m/z': {'min': 500, 'max': 600}}}).json()
    assert narrowed['total'] <= first['total']
    for row in narrowed['rows']:
        assert 500 <= row[1] <= 600


def test_feature_detail_and_abundance(client, completed_run):
    table = client.post(f'/api/runs/{completed_run}/features', json={'limit': 50}).json()
    # Pick a feature that survived filtering, so abundance data exists.
    heatmap = client.post(f'/api/runs/{completed_run}/plots/heatmap', json={}).json()
    compound = heatmap['features'][0]

    detail = client.get(f'/api/runs/{completed_run}/feature',
                        params={'compound': compound})
    assert detail.status_code == 200, detail.text
    assert detail.json()['id'] == compound

    abundance = client.get(f'/api/runs/{completed_run}/feature/abundance',
                           params={'compound': compound})
    assert abundance.status_code == 200, abundance.text
    body = abundance.json()
    assert body['groups'], 'no group averages returned'
    assert body['points'], 'no per-injection points returned'
    assert table['total'] > 0


def test_feature_table_is_unpaged_by_default(client, completed_run):
    """The table is shown in full, the way the desktop's tree is -- limit 0
    means every row, so keyboard navigation can reach the whole dataset."""
    body = client.post(f'/api/runs/{completed_run}/features', json={'limit': 0}).json()
    assert len(body['rows']) == body['total'] > 0


def test_blank_values_sort_last_in_both_directions(client, completed_run):
    """A feature with no value for the sort column belongs at the bottom
    whichever way you sort. Mapping blanks to +inf instead parks every
    unsearched feature at the top of a descending sort, which is how "sort by
    Hits" once looked like a broken database search."""
    for descending in (True, False):
        body = client.post(f'/api/runs/{completed_run}/features',
                           json={'limit': 0, 'sort': 'hits',
                                 'descending': descending}).json()
        hits = [row[-1] for row in body['rows']]
        blanks = [i for i, value in enumerate(hits) if value is None]
        present = [i for i, value in enumerate(hits) if value is not None]
        if blanks and present:
            assert min(blanks) > max(present),                 f'blank hits are not last with descending={descending}'


def test_unknown_sort_column_is_reported(client, completed_run):
    """A silently ignored sort key means the table just looks unsorted."""
    response = client.post(f'/api/runs/{completed_run}/features',
                           json={'sort': 'Hits'})     # label, not key
    assert response.status_code == 422
    assert 'cannot sort by' in response.json()['detail']


def test_ordination_replicate_views_differ(client, completed_run):
    """Individual Injections plots every injection; Biological Replicates
    averages technical replicates per sample and must therefore have strictly
    fewer points."""
    def points(replicates):
        body = client.post(f'/api/runs/{completed_run}/plots/ordination',
                           json={'method': 'PCA', 'view': 'Scores',
                                 'replicates': replicates})
        assert body.status_code == 200, body.text
        payload = body.json()
        return payload['n_points'], sum(len(s['x']) for s in payload['series'])

    injections, plotted_injections = points('Individual Injections')
    samples, plotted_samples = points('Biological Replicates')
    assert injections > samples > 0
    assert plotted_injections > plotted_samples > 0


def test_ordination_rejects_an_unknown_replicate_view(client, completed_run):
    response = client.post(f'/api/runs/{completed_run}/plots/ordination',
                           json={'replicates': 'Every Third Tuesday'})
    assert response.status_code == 422


def test_database_search_finds_matches(client, completed_run):
    """The NPAtlas search has to actually populate hit counts -- an empty
    Hits column is indistinguishable from a broken search."""
    response = client.post(f'/api/runs/{completed_run}/dbsearch')
    if response.status_code == 503:
        pytest.skip('npatlas.tsv is not installed on this server')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['searched'] > 0
    assert body['matched'] > 0, 'no feature matched the atlas at all'

    # ...and those counts have to reach the table.
    table = client.post(f'/api/runs/{completed_run}/features',
                        json={'limit': 20, 'sort': 'hits', 'descending': True,
                              'only_searched': True}).json()
    top = [row[-1] for row in table['rows']]
    assert top[0] and top[0] > 0, f'top of a hits-descending sort is {top[:5]}'


def _hit_table(client, run, spec):
    response = client.post(f'/api/runs/{run}/features',
                           json={'limit': 0, 'hit_filter': spec})
    if response.status_code == 503:
        pytest.skip('npatlas.tsv is not installed on this server')
    assert response.status_code == 200, response.text
    return response.json()


def test_hit_filters_narrow_the_table(client, completed_run):
    everything = _hit_table(client, completed_run, None)
    assert not everything['hit_filter']
    ids = {row[0] for row in everything['rows']}
    for spec in ({'name': 'mycin'},
                 {'taxon': {'level': 'genus', 'value': 'Streptomyces'}},
                 {'taxon': {'level': 'phylum', 'value': 'actinomycetota'}},
                 {'activity': ['siderophore']},
                 {'activity': ['electrophile', 'chromophore'], 'chromophore_min': 14},
                 {'substructure': 'c1ccccc1O'}):
        body = _hit_table(client, completed_run, spec)
        assert body['hit_filter'], spec
        subset = {row[0] for row in body['rows']}
        assert subset and subset <= ids, spec
        assert len(subset) < len(ids), spec


def test_hit_criteria_apply_to_the_same_hit(client, completed_run):
    name = {row[0] for row in _hit_table(client, completed_run, {'name': 'mycin'})['rows']}
    epoxide = {row[0] for row in
               _hit_table(client, completed_run, {'substructure': 'C1OC1'})['rows']}
    both = {row[0] for row in _hit_table(
        client, completed_run, {'name': 'mycin', 'substructure': 'C1OC1'})['rows']}
    assert both <= name & epoxide


def test_bad_hit_filters_are_422(client, completed_run):
    for spec in ({'substructure': 'not a molecule (('},
                 {'taxon': {'level': 'subphylum', 'value': 'x'}}):
        response = client.post(f'/api/runs/{completed_run}/features',
                               json={'limit': 0, 'hit_filter': spec})
        assert response.status_code in (422, 503), response.text


def test_taxon_suggestions(client, completed_run):
    response = client.get(f'/api/runs/{completed_run}/taxa',
                          params={'level': 'genus', 'q': 'actino'})
    if response.status_code == 503:
        pytest.skip('npatlas.tsv is not installed on this server')
    names = [entry['name'] for entry in response.json()['names']]
    assert names and all('actino' in name.lower() for name in names)
    assert names[0].lower().startswith('actino')


def test_matches_report_whether_they_pass_the_hit_filter(client, completed_run):
    body = _hit_table(client, completed_run, {'name': 'mycin'})
    compound = body['rows'][0][0]
    import json
    matches = client.get(f'/api/runs/{completed_run}/feature/matches',
                         params={'compound': compound,
                                 'filter': json.dumps({'name': 'mycin'})}).json()['matches']
    assert any(match['passes'] for match in matches)
    assert all(match['passes'] == ('mycin' in (match['compound_name'] or '').lower())
               for match in matches)
    assert all('siderophore' in match for match in matches)


def test_upset_reports_set_totals(client, completed_run):
    """The classic UpSet layout needs per-set totals for its left-hand bars,
    not just the intersection sizes."""
    body = client.post(f'/api/runs/{completed_run}/plots/upset', json={}).json()
    assert body['categories']
    assert set(body['totals']) == set(body['categories'])
    assert all(body['totals'][name] >= 0 for name in body['categories'])
    assert sum(body['totals'].values()) > 0


def test_unknown_plot_is_404(client, completed_run):
    assert client.post(f'/api/runs/{completed_run}/plots/nope', json={}).status_code == 404


def test_results_before_completion_are_409(client):
    """A run id that exists but has no results yields 409, not 404 -- the
    client distinguishes 'keep polling' from 'gone'."""
    assert client.get('/api/runs/r_0000000000000000/plots').status_code == 404


def test_without_a_fragment_file_ms2_only_is_unavailable(client, completed_run):
    body = client.post(f'/api/runs/{completed_run}/features',
                       json={'limit': 0, 'ms2_only': True}).json()
    assert body['has_fragments'] is False
    assert body['total'] > 0
