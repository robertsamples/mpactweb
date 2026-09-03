"""The bundled demo dataset.

The demo is the first thing a new visitor touches, so it gets its own tests:
it has to be present, it has to land in the library looking exactly like an
upload (inspected, format-detected), and clicking the button twice must not
fill the library with duplicates.
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
    assert body['available'] is True, f'demo files missing: {body["missing"]}'
    assert body['groups'], 'demo description lists no biological groups'


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
    peaktables = [f for f in library if f['kind'] == 'peaktable']
    assert len(peaktables) == 1, 'the demo peak table was added more than once'


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

    before = {name: demo.source_path(name).stat().st_mtime_ns
              for _, name in demo.DEMO_FILES}
    client.post('/api/files/demo')
    after = {name: demo.source_path(name).stat().st_mtime_ns
             for _, name in demo.DEMO_FILES}
    assert before == after


def test_demo_path_traversal_is_refused():
    from app import demo
    with pytest.raises(FileNotFoundError):
        demo.source_path('../npatlas.tsv')
