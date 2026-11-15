"""The bundled demo datasets.

The demo is the first thing a new visitor touches, so it gets its own tests:
it has to be present, it has to land in the library looking exactly like an
upload (inspected, format-detected), and clicking the button twice must not
fill the library with duplicates. The CHM350 class datasets are MS-DIAL
exports, go through the MS-DIAL import, and come set up for the course.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    os.environ['MPACT_DATA_ROOT'] = str(tmp_path_factory.mktemp('mpact-demo-data'))
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app
    # Another test module may have imported app.config first, binding
    # a different data root; re-resolve so this module gets its own.
    config.reload()
    with TestClient(app) as test_client:
        yield test_client


def test_demo_is_bundled(client):
    body = client.get('/api/files/demo').json()
    assert body['available'] is True
    datasets = {d['id']: d for d in body['datasets']}
    default = datasets[body['default']]
    assert default['available'], f'demo files missing: {default["missing"]}'
    assert default['groups'], 'demo description lists no biological groups'


CHM350 = ('AS', 'CP', 'JM', 'MJS', 'ML', 'PK', 'SAS')


def test_the_chm350_datasets_are_listed_by_initials(client):
    datasets = {d['id']: d for d in client.get('/api/files/demo').json()['datasets']}
    for initials in CHM350:
        entry = datasets[f'chm350-{initials.lower()}']
        assert entry['name'] == initials
        assert entry['description'] == 'CHM350 Dataset'
        assert entry['summary'] == 'CHM350 MS-DIAL dataset'
        assert entry['kinds'] == ['peaktable', 'samplelist', 'metadata', 'fragments']
        assert entry['available'], f'{initials}: missing {entry["missing"]}'
        assert entry['peaktable'] == f'CHM350_{initials}.txt'


def test_each_chm350_dataset_comes_set_up(client):
    """An Eu condition against its culture's growth control, blank filtering
    only where there is a blank, and the course's two feature sets."""
    datasets = {d['id']: d for d in client.get('/api/files/demo').json()['datasets']}
    for initials in CHM350:
        preset = datasets[f'chm350-{initials.lower()}']['preset']
        settings = preset['settings']
        assert 'Eu' in settings['expgrp'].split('_')
        assert settings['ctrgrp'].endswith('GC')
        assert settings['expgrp'].split('Eu')[0] == settings['ctrgrp'].split('GC')[0]
        names = [g['name'] for g in preset['groupsets']]
        assert names == ['All features', 'Elicitor only']
        everything, elicitor = preset['groupsets']
        assert everything['colour'] == '#000000' and elicitor['colour'] == '#d62728'
        background = set(everything['excl'])
        assert all('blank' in g.lower() or 'media' in g.lower() for g in background)
        assert set(elicitor['excl']) == background | {g for g in elicitor['excl'] if g.endswith('GC')}
        assert not any(g.endswith('GC') for g in elicitor['src'])
        assert settings['blnkfltr'] is True and settings['blnkgrp'] == 'Blank'
        assert 'Blank' in background


def test_the_mjs_groups_use_s_as_the_biological_replicate(client):
    """MJS_C1_S1_Cu_high_r1: C1/C2 are cultures, S1-S3 biological replicates."""
    datasets = {d['id']: d for d in client.get('/api/files/demo').json()['datasets']}
    groups = datasets['chm350-mjs']['preset']['groupsets'][0]['src']
    assert sorted(groups) == sorted(f'{c}_{t}' for c in ('C1', 'C2')
                                    for t in ('Cu_high', 'EDTA', 'Eu_high', 'GC'))


def test_a_chm350_dataset_loads_as_an_msdial_upload(client):
    records = client.post('/api/files/demo', json={'id': 'chm350-cp'})
    assert records.status_code == 200, records.text
    by_kind = {record['kind']: record for record in records.json()}
    assert set(by_kind) == {'peaktable', 'samplelist', 'metadata', 'fragments'}
    peaks = by_kind['peaktable']
    assert peaks['name'] == 'CHM350_CP.txt'
    assert peaks['fmt'] == 'msdial'
    assert peaks['detail']['features'] == 19104
    assert peaks['detail']['injections'] == 42
    assert by_kind['fragments']['detail']['entries'] == 19104

    names = client.get(f'/api/files/{peaks["id"]}/injections').json()['names']
    assert names[:4] == ['AS_media_2_r1', 'AS_media_2_r2', 'AS_media_2_r3', 'blank_r1']
    assert all(name.startswith(('AS_', 'blank', 'CPA_')) for name in names)

    groups = client.post('/api/files/groups', json={
        kind: by_kind[kind]['id'] for kind in ('peaktable', 'samplelist', 'metadata')}).json()
    assert groups['unresolved_total'] == 0
    assert groups['groups'] == ['AS_media_2', 'Blank', 'CPA_1B_Cu_high', 'CPA_1B_EDTA',
                                'CPA_1B_Eu_high', 'CPA_1B_GC']


def test_an_unknown_demo_dataset_is_a_404(client):
    assert client.post('/api/files/demo', json={'id': 'nope'}).status_code == 404


def test_loading_the_demo_fills_every_slot(client):
    records = client.post('/api/files/demo').json()
    kinds = {record['kind'] for record in records}
    assert kinds == {'peaktable', 'samplelist', 'metadata', 'fragments'}

    # Each file is inspected on arrival, exactly as an upload is -- otherwise
    # the File Selection page would show a demo entry with no detail.
    by_kind = {record['kind']: record for record in records}
    assert by_kind['peaktable']['fmt'] == 'progenesis'
    assert by_kind['peaktable']['detail']['features'] > 1000
    assert by_kind['peaktable']['detail']['injections'] > 1
    assert by_kind['samplelist']['detail']['missing_columns'] == []
    assert by_kind['metadata']['detail']['missing_columns'] == []
    assert by_kind['fragments']['detail']['entries'] > 0
    assert all(record['size'] > 0 for record in records)


def test_loading_twice_does_not_duplicate(client):
    first = client.post('/api/files/demo').json()
    second = client.post('/api/files/demo').json()
    assert [r['id'] for r in first] == [r['id'] for r in second]

    library = client.get('/api/files').json()
    peaktables = [f for f in library if f['kind'] == 'peaktable' and 'PTY087I2' in f['name']]
    assert len(peaktables) == 1, 'the demo peak table was added more than once'

    again = client.post('/api/files/demo', json={'id': 'chm350-pk'}).json()
    assert [r['id'] for r in again] == [r['id'] for r in
                                        client.post('/api/files/demo', json={'id': 'chm350-pk'}).json()]


def test_demo_files_resolve_biological_groups(client):
    records = {r['kind']: r['id'] for r in client.post('/api/files/demo').json()}
    body = client.post('/api/files/groups', json={
        'peaktable': records['peaktable'],
        'samplelist': records['samplelist'],
        'metadata': records['metadata'],
    })
    assert body.status_code == 200, body.text
    payload = body.json()
    assert len(payload['groups']) == 4
    # The blank group is what seeds blank filtering; the suggestion has to
    # find it or the form starts in an invalid state.
    assert payload['suggested']['blank']
    assert payload['suggested']['experimental']
    assert payload['suggested']['control']
    assert payload['suggested']['experimental'] != payload['suggested']['control']


def test_demo_source_files_are_not_modified_by_loading(client):
    """A run converts its own copy; the bundled originals must stay pristine
    so the next visitor gets the same dataset."""
    from app import demo

    names = [name for dataset in demo.DATASETS for _, name in dataset.files]
    before = {name: demo.source_path(name).stat().st_mtime_ns for name in names}
    for dataset in demo.DATASETS:
        client.post('/api/files/demo', json={'id': dataset.id})
    after = {name: demo.source_path(name).stat().st_mtime_ns for name in names}
    assert before == after


def test_demo_path_traversal_is_refused():
    from app import demo
    with pytest.raises(FileNotFoundError):
        demo.source_path('../npatlas.tsv')
