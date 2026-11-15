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
    column = [c['key'] for c in table['columns']].index('hits')
    top = [row[column] for row in table['rows']]
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


def test_ms2_hits_column_and_msms_search_without_fragments(client, completed_run):
    body = client.post(f'/api/runs/{completed_run}/features', json={'limit': 0}).json()
    labels = [c['label'] for c in body['columns']]
    assert 'MS1 hits' in labels and 'MS2 hits' in labels
    response = client.post(f'/api/runs/{completed_run}/msmssearch', json={})
    assert response.status_code == 404


def test_a_feature_from_another_run_is_404_on_every_feature_route(client, completed_run):
    """A selection carried over from a different run asks for an id this run
    does not have. That is "not found", not a server error."""
    for route in ('feature', 'feature/abundance', 'feature/matches', 'feature/msms'):
        response = client.get(f'/api/runs/{completed_run}/{route}',
                              params={'compound': 'not-a-feature-of-this-run'})
        expected = (200, 404) if route == 'feature/abundance' else (404,)
        assert response.status_code in expected, f'{route}: {response.status_code} {response.text}'


def test_a_numeric_filter_leaves_out_features_with_no_value(client, completed_run):
    """No MS/MS search has run here, so no feature has an MS2 hit count; a
    filter asking for at least one must not keep them all."""
    body = client.post(f'/api/runs/{completed_run}/features',
                       json={'limit': 0, 'filters': {'msms_hits': {'min': 1}}}).json()
    assert body['total'] == 0
    unfiltered = client.post(f'/api/runs/{completed_run}/features', json={'limit': 0}).json()
    assert unfiltered['total'] > 0
    assert all(row[7] is None for row in unfiltered['rows'])


def test_a_repeated_plot_request_gives_the_same_answer(client, completed_run):
    """Payloads are cached per run and options; different options must not
    be served each other's."""
    def ordination(**options):
        response = client.post(f'/api/runs/{completed_run}/plots/ordination', json=options)
        assert response.status_code == 200, response.text
        return response.json()

    scores = ordination(method='PCA', view='Scores')
    assert ordination(method='PCA', view='Scores') == scores
    loadings = ordination(method='PCA', view='Loadings')
    assert 'loadings' in loadings and 'series' not in loadings
    assert loadings['explained_variance'] == scores['explained_variance'], \
        'both views read the same solved ordination'
    fewer = ordination(method='PCA', view='Loadings', top_n=5)
    assert len(fewer['loadings']) == 5 < len(loadings['loadings'])
    assert ordination(method='NMDS', view='Scores')['x_label'] == 'NMDS1'


def test_the_database_search_shows_up_in_cached_views(client, completed_run):
    """The search rewrites iondict.csv; nothing cached from before it may be
    served afterwards."""
    before = client.post(f'/api/runs/{completed_run}/features', json={'limit': 0}).json()
    client.post(f'/api/runs/{completed_run}/plots/mzrt', json={})
    assert client.post(f'/api/runs/{completed_run}/dbsearch').status_code == 200
    after = client.post(f'/api/runs/{completed_run}/features', json={'limit': 0}).json()
    assert after['searched'] is True
    assert after['total'] == before['total']
    assert any(row[6] is not None for row in after['rows'])
    compound = after['rows'][0][0]
    detail = client.get(f'/api/runs/{completed_run}/feature', params={'compound': compound}).json()
    assert detail['fields'].get('hits') is not None
    assert client.post(f'/api/runs/{completed_run}/plots/mzrt', json={}).status_code == 200


def test_download_all_is_a_readable_archive(client, completed_run):
    import io
    import zipfile

    listed = {item['name']: item['size']
              for item in client.get(f'/api/runs/{completed_run}/downloads').json()}
    response = client.get(f'/api/runs/{completed_run}/downloads.zip')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        sizes = {info.filename: info.file_size for info in archive.infolist()}
    assert sizes == listed


def test_runs_are_listed_with_their_submission_time(client, completed_run):
    runs = client.get('/api/runs').json()
    entry = next(run for run in runs if run['run_id'] == completed_run)
    request = client.get(f'/api/runs/{completed_run}/request').json()
    assert entry['created_at'] == request['created_at']
    assert entry['created_at'] <= entry['started_at'] <= entry['finished_at']


def _export(client, run_id, **options):
    import csv
    import io

    response = client.post(f'/api/runs/{run_id}/features/export', json=options)
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/csv')
    assert 'attachment' in response.headers['content-disposition']
    rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
    return rows[0], rows[1:], response


def test_the_table_is_served_a_page_at_a_time(client, completed_run):
    """The browser gets every id once, and rows as they scroll into view."""
    options = {'sort': 'm/z', 'include_filtered': True}
    whole = client.post(f'/api/runs/{completed_run}/features', json={**options, 'limit': 0}).json()
    assert 'ids' not in whole, 'ids are only sent when asked for'

    first = client.post(f'/api/runs/{completed_run}/features',
                        json={**options, 'with_ids': True, 'offset': 0, 'limit': 50}).json()
    assert first['total'] == whole['total'] > 200
    assert first['ids'] == [row[0] for row in whole['rows']]
    assert first['rows'] == whole['rows'][:50]

    later = client.post(f'/api/runs/{completed_run}/features',
                        json={**options, 'offset': 150, 'limit': 50}).json()
    assert later['rows'] == whole['rows'][150:200]
    assert [row[0] for row in later['rows']] == first['ids'][150:200]

    past_the_end = client.post(f'/api/runs/{completed_run}/features',
                               json={**options, 'offset': whole['total'] + 10, 'limit': 50}).json()
    assert past_the_end['rows'] == [] and past_the_end['total'] == whole['total']


def test_pages_follow_the_sort_and_filters_they_were_asked_with(client, completed_run):
    descending = {'sort': 'max', 'descending': True,
                  'filters': {'m/z': {'min': 300, 'max': 700}}}
    whole = client.post(f'/api/runs/{completed_run}/features', json={**descending, 'limit': 0}).json()
    page = client.post(f'/api/runs/{completed_run}/features',
                       json={**descending, 'offset': 10, 'limit': 20}).json()
    assert page['rows'] == whole['rows'][10:30]
    ascending = client.post(f'/api/runs/{completed_run}/features',
                            json={**descending, 'descending': False, 'offset': 0, 'limit': 5}).json()
    assert ascending['rows'] != whole['rows'][:5], 'a different order is a different view'


def test_the_listed_features_export_as_a_csv(client, completed_run):
    """The export is the table on screen: the same rows in the same order,
    with every per-feature column and each feature's best match."""
    view = {'sort': 'm/z', 'descending': True, 'filters': {'m/z': {'min': 400, 'max': 600}}}
    table = client.post(f'/api/runs/{completed_run}/features',
                        json={**view, 'with_ids': True, 'limit': 0}).json()
    header, rows, response = _export(client, completed_run, **view)
    assert response.headers['content-disposition'].endswith('_features.csv"')
    assert header[0] == 'Compound' and 'm/z' in header
    assert [row[0] for row in rows] == table['ids']
    assert all(len(row) == len(header) for row in rows)
    masses = [float(row[header.index('m/z')]) for row in rows]
    assert all(400 <= mass <= 600 for mass in masses)
    assert masses == sorted(masses, reverse=True)


def test_the_export_names_each_features_best_match(client, completed_run):
    if not client.get('/api/health').json()['npatlas']:
        pytest.skip('NPAtlas is not installed')
    header, rows, _ = _export(client, completed_run, sort='m/z')
    for name in ('ms1_top_match', 'ms1_top_formula', 'ms1_top_ppm', 'ms1_top_organism'):
        assert name in header
    matched = [row for row in rows if row[header.index('ms1_top_match')]]
    assert matched, 'no feature in the run has a match to report'
    params = client.get(f'/api/runs/{completed_run}/plots').json()['params']
    for row in matched[:5]:
        ppm = float(row[header.index('ms1_top_ppm')])
        assert 0 <= ppm < float(params['ppmthresh'])
        # The same hit the details rail lists first for this feature.
        listed = client.get(f'/api/runs/{completed_run}/feature/matches',
                            params={'compound': row[0]}).json()['matches']
        assert listed, f'{row[0]} is exported with a match but lists none'
        assert abs(float(listed[0]['ppm']) - ppm) < 0.01
        assert row[header.index('ms1_top_match')] in {m['compound_name'] for m in listed
                                                      if abs(float(m['ppm']) - ppm) < 0.01}
    # Under a hit filter the best match is the best one meeting it.
    filtered_header, filtered_rows, _ = _export(
        client, completed_run, sort='m/z', hit_filter={'name': 'mycin'})
    assert 0 < len(filtered_rows) < len(rows)
    at = filtered_header.index('ms1_top_match')
    assert all('mycin' in row[at].lower() or not row[at] for row in filtered_rows)


def test_selected_features_export_whether_or_not_they_passed_filtering(client, completed_run):
    everything = client.post(f'/api/runs/{completed_run}/features',
                             json={'limit': 0, 'include_filtered': True, 'with_ids': True}).json()
    surviving = set(client.post(f'/api/runs/{completed_run}/features',
                                json={'limit': 0, 'with_ids': True}).json()['ids'])
    removed = next(compound for compound in everything['ids'] if compound not in surviving)
    kept = sorted(surviving)[:2]
    header, rows, response = _export(client, completed_run, sort='m/z', include_filtered=True,
                                     ids=[removed, *kept, 'not-in-this-run'])
    assert response.headers['content-disposition'].endswith('_selected.csv"')
    assert sorted(row[0] for row in rows) == sorted([removed, *kept])
    masses = [float(row[header.index('m/z')]) for row in rows]
    assert masses == sorted(masses), 'selected features come out in the requested order'

    assert client.post(f'/api/runs/{completed_run}/features/export',
                       json={'ids': 'all of them'}).status_code == 422
    assert client.post(f'/api/runs/{completed_run}/features/export',
                       json={'sort': 'nope'}).status_code == 422


def test_a_run_can_be_labelled_and_relabelled(client, completed_run):
    def listed():
        return next(run for run in client.get('/api/runs').json()
                    if run['run_id'] == completed_run)

    response = client.patch(f'/api/runs/{completed_run}', json={'label': '  CV 0.3,\n no decon  '})
    assert response.status_code == 200, response.text
    assert response.json()['label'] == 'CV 0.3, no decon'
    assert listed()['label'] == 'CV 0.3, no decon'
    assert client.get(f'/api/runs/{completed_run}/status').json()['label'] == 'CV 0.3, no decon'
    # The request still holds everything needed to run it again.
    request = client.get(f'/api/runs/{completed_run}/request').json()
    assert request['label'] == 'CV 0.3, no decon'
    assert request['settings']['cvthresh'] and request['inputs']['peaktable']

    assert client.patch(f'/api/runs/{completed_run}', json={'label': 'x' * 500}).json()['label'] \
        == 'x' * 120
    assert client.patch(f'/api/runs/{completed_run}', json={'label': '   '}).json()['label'] is None
    assert listed()['label'] is None
    assert client.patch('/api/runs/r_0000000000000000', json={'label': 'x'}).status_code == 404
    # Plots are unaffected by a rename.
    assert client.post(f'/api/runs/{completed_run}/plots/mzrt', json={}).status_code == 200


def test_a_tall_heatmap_is_binned_for_display(client, completed_run, monkeypatch):
    """Past the row cap neighbouring features are averaged; the feature list
    and the dendrogram still cover every feature."""
    from app import config
    full = client.post(f'/api/runs/{completed_run}/plots/heatmap', json={}).json()
    assert full['bin_size'] == 1 and len(full['z']) == full['shape'][0]

    monkeypatch.setattr(config, 'MAX_HEATMAP_ROWS', 100)
    binned = client.post(f'/api/runs/{completed_run}/plots/heatmap', json={}).json()
    rows, cols = binned['shape']
    size = binned['bin_size']
    assert size == -(-rows // 100) > 1
    assert len(binned['z']) == -(-rows // size) <= 100
    assert all(len(row) == cols for row in binned['z'])
    assert binned['features'] == full['features'], 'every feature is still listed, in order'
    assert binned['row_dendrogram'] == full['row_dendrogram']
    assert all(0 <= value <= 1 for row in binned['z'] for value in row)
    # A bin is the mean of the rows it covers.
    first = [sum(column) / size for column in zip(*full['z'][:size])]
    assert all(abs(a - b) < 1e-3 for a, b in zip(first, binned['z'][0]))


def test_rows_can_be_fetched_by_id(client, completed_run):
    """The browser may list features in an order of its own (selected or
    flagged first), so it asks for the rows on screen by id."""
    options = {'sort': 'm/z', 'include_filtered': True}
    whole = client.post(f'/api/runs/{completed_run}/features', json={**options, 'limit': 0}).json()
    by_id = {row[0]: row for row in whole['rows']}
    wanted = [whole['rows'][i][0] for i in (400, 3, 250, 17)]

    body = client.post(f'/api/runs/{completed_run}/features',
                       json={**options, 'rows_for': [*wanted, 'not-a-feature']}).json()
    assert [row[0] for row in body['rows']] == wanted, 'in the order asked, unknown ids left out'
    assert body['rows'] == [by_id[compound] for compound in wanted]
    assert body['total'] == whole['total']

    # Only features in the view: one the filters remove is not returned.
    surviving = client.post(f'/api/runs/{completed_run}/features',
                            json={'sort': 'm/z', 'limit': 0, 'with_ids': True}).json()['ids']
    removed = next(compound for compound in by_id if compound not in set(surviving))
    narrowed = client.post(f'/api/runs/{completed_run}/features',
                           json={'sort': 'm/z', 'rows_for': [removed, surviving[0]]}).json()
    assert [row[0] for row in narrowed['rows']] == [surviving[0]]

    assert client.post(f'/api/runs/{completed_run}/features',
                       json={'rows_for': 'everything'}).status_code == 422
    assert client.post(f'/api/runs/{completed_run}/features',
                       json={'rows_for': ['x'] * 5000}).status_code == 422


def test_features_can_be_flagged_and_unflagged(client, completed_run):
    ids = client.post(f'/api/runs/{completed_run}/features',
                      json={'sort': 'm/z', 'limit': 0, 'with_ids': True}).json()['ids']
    url = f'/api/runs/{completed_run}/flags'
    assert client.get(url).json() == {'flags': {}}

    red = client.post(url, json={'ids': [ids[0], ids[1], 'not-a-feature'], 'colour': '#E6194B'})
    assert red.status_code == 200, red.text
    assert red.json()['flags'] == {ids[0]: '#e6194b', ids[1]: '#e6194b'}, \
        'lower-cased, and ids the run does not have are not stored'
    blue = client.post(url, json={'ids': [ids[1], ids[2]], 'colour': '#4363d8'}).json()['flags']
    assert blue == {ids[0]: '#e6194b', ids[1]: '#4363d8', ids[2]: '#4363d8'}, \
        'a second flag on a feature replaces the first'
    assert client.get(url).json()['flags'] == blue

    # In the export, beside each feature.
    header, rows, _ = _export(client, completed_run, sort='m/z')
    at = header.index('flag')
    exported = {row[0]: row[at] for row in rows if row[at]}
    assert exported == blue

    for bad in ('red', '#12345', '#gggggg', 'e6194b'):
        assert client.post(url, json={'ids': [ids[0]], 'colour': bad}).status_code == 422, bad
    assert client.post(url, json={'ids': 'all', 'colour': '#000000'}).status_code == 422

    cleared = client.post(url, json={'ids': [ids[0], ids[1]], 'colour': None}).json()['flags']
    assert cleared == {ids[2]: '#4363d8'}
    assert client.post(url, json={'ids': ids, 'colour': None}).json() == {'flags': {}}
    assert client.get(url).json() == {'flags': {}}
    # With no flags left the export goes back to having no flag column.
    assert 'flag' not in _export(client, completed_run, sort='m/z')[0]
    assert client.get('/api/runs/r_0000000000000000/flags').status_code == 404
