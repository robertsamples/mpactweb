"""MS-DIAL .csv import, the sample list / metadata editor's endpoints, and
the one-run-at-a-time guard.

The editor writes the two files the pipeline joins on Sample_Code, so these
check the shape the pipeline needs rather than the UI: a peak table's full
injection list comes back, an edited table round-trips, and the mistakes the
join would otherwise report as "Data read failure" are refused up front.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEMO = Path(__file__).resolve().parents[1] / 'data' / 'demo'


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    os.environ['MPACT_DATA_ROOT'] = str(tmp_path_factory.mktemp('mpact-tables'))
    for key in ('MPACT_AUTH', 'MPACT_ACCESS_CODE'):
        os.environ.pop(key, None)
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app
    config.reload()
    with TestClient(app) as test_client:
        yield test_client


def msdial_rows(sep):
    """A minimal MS-DIAL alignment export: four header rows, a column-title
    row with 35 metadata columns, three injections, then Average/Stdev."""
    injections = ['Blank_r1', 'A_S1_r1', 'A_S1_r2']
    lead = [''] * 34
    rows = [
        lead + ['Class'] + ['1'] * 3 + ['NA', 'NA'],
        lead + ['File type'] + ['Sample'] * 3 + ['NA', 'NA'],
        lead + ['Injection order', '1', '2', '3', 'NA', 'NA'],
        lead + ['Batch ID'] + ['1'] * 3 + ['Average', 'Stdev'],
    ]
    title = ['Alignment ID', 'Average Rt(min)', 'Average Mz'] + [f'm{i}' for i in range(32)]
    rows.append(title + injections + ['A', 'A'])
    for feature in range(4):
        meta = [str(feature), f'{1 + feature * 0.5:.3f}', f'{150 + feature * 10:.5f}']
        meta += ['null'] * 32
        rows.append(meta + ['100', '5000', '5200', '3433', '2400'])
    return '\n'.join(sep.join(row) for row in rows) + '\n'


def test_an_msdial_csv_is_stored_as_its_tab_separated_form(client):
    response = client.post('/api/files', data={'kind': 'peaktable'},
                           files={'file': ('Area_1.csv', msdial_rows(','), 'text/csv')})
    assert response.status_code == 200, response.text
    record = response.json()
    assert record['name'] == 'Area_1.txt'
    assert record['fmt'] == 'msdial'
    assert record['detail']['injections'] == 3
    assert record['detail']['features'] == 4

    names = client.get(f'/api/files/{record["id"]}/injections').json()['names']
    assert names == ['Blank_r1', 'A_S1_r1', 'A_S1_r2']


def test_an_msdial_csv_matches_the_native_txt_byte_for_byte(tmp_path):
    from app import inspect as inspection
    source = tmp_path / 'Area.csv'
    source.write_text(msdial_rows(','))
    converted = inspection.normalize_peaktable(source)
    assert converted.read_text() == msdial_rows('\t')
    assert not source.exists()


def test_other_csv_peak_tables_are_left_alone(tmp_path):
    from app import inspect as inspection
    progenesis = tmp_path / 'peaks.csv'
    progenesis.write_bytes((DEMO / '200826_PTY087I2codingdataset.csv').read_bytes()[:20000])
    assert inspection.normalize_peaktable(progenesis) is None
    assert progenesis.exists()


def test_a_built_table_round_trips(client):
    created = client.post('/api/files/table', json={
        'kind': 'samplelist', 'name': 'built samplelist',
        'columns': ['Injection', 'Sample_Code'],
        'rows': [['Blank_r1', 'Blank'], ['A_S1_r1', 'A_S1'], ['A_S1_r2', 'A_S1']],
    })
    assert created.status_code == 200, created.text
    record = created.json()
    assert record['name'] == 'built_samplelist.csv'
    assert record['detail']['missing_columns'] == []
    assert record['detail']['rows'] == 3

    table = client.get(f'/api/files/{record["id"]}/table').json()
    assert table['columns'] == ['Injection', 'Sample_Code']
    assert table['rows'][1] == ['A_S1_r1', 'A_S1']

    replaced = client.put(f'/api/files/{record["id"]}/table', json={
        'kind': 'samplelist', 'columns': ['Injection', 'Sample_Code', 'Note'],
        'rows': [['Blank_r1', 'Blank', 'x'], ['A_S1_r1', 'A_S1', ''], ['A_S1_r2', 'A_S1', 'y,z']],
    })
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()['id'] == record['id']
    again = client.get(f'/api/files/{record["id"]}/table').json()
    assert again['rows'][2] == ['A_S1_r2', 'A_S1', 'y,z']

    download = client.get(f'/api/files/{record["id"]}/download')
    assert download.status_code == 200
    assert download.text.startswith('Injection,Sample_Code,Note\n')


@pytest.mark.parametrize('columns, rows, message', [
    (['Injection'], [['a']], 'Sample_Code'),
    (['Injection', 'Sample_Code'], [['a', 'x'], ['a', 'y']], 'more than once'),
    (['Injection', 'Sample_Code'], [['a', '']], 'needs a Sample_Code'),
])
def test_a_table_the_join_would_choke_on_is_refused(client, columns, rows, message):
    response = client.post('/api/files/table', json={
        'kind': 'samplelist', 'columns': columns, 'rows': rows})
    assert response.status_code == 422
    assert message in response.json()['detail']


def test_duplicate_sample_codes_in_metadata_are_refused(client):
    response = client.post('/api/files/table', json={
        'kind': 'metadata', 'columns': ['Sample_Code', 'Biological_Group'],
        'rows': [['A_S1', 'A'], ['A_S1', 'B']]})
    assert response.status_code == 422


def test_built_tables_give_the_groups_the_pipeline_sees(client):
    peak = client.post('/api/files', data={'kind': 'peaktable'},
                       files={'file': ('Groups.csv', msdial_rows(','), 'text/csv')}).json()
    sample = client.post('/api/files/table', json={
        'kind': 'samplelist', 'columns': ['Injection', 'Sample_Code'],
        'rows': [['Blank_r1', 'Blank'], ['A_S1_r1', 'A_S1'], ['A_S1_r2', 'A_S1']]}).json()
    meta = client.post('/api/files/table', json={
        'kind': 'metadata', 'columns': ['Sample_Code', 'Biological_Group'],
        'rows': [['Blank', 'Blank'], ['A_S1', 'A']]}).json()
    body = client.post('/api/files/groups', json={
        'peaktable': peak['id'], 'samplelist': sample['id'], 'metadata': meta['id']})
    assert body.status_code == 200, body.text
    assert body.json()['groups'] == ['Blank', 'A']
    assert body.json()['suggested']['blank'] == 'Blank'


def test_concurrent_library_writes_keep_every_change(tmp_path, monkeypatch):
    """The editor saves two files at once; neither update may be lost."""
    from concurrent.futures import ThreadPoolExecutor

    from app import config, storage
    monkeypatch.setattr(config, 'SESSIONS_DIR', tmp_path)
    session = storage.Session.create()
    records = [session.add_file('samplelist', f'f{i}.csv', 0) for i in range(8)]
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda r: [session.update_file(r.id, size=n) for n in range(1, 21)],
                      records))
    assert sorted(r.size for r in session.files()) == [20] * 8


# --- one run at a time per workspace ----------------------------------------

def test_a_second_submission_is_refused_while_the_first_is_queued(tmp_path, monkeypatch):
    from app import config, jobs, storage

    monkeypatch.setattr(config, 'SESSIONS_DIR', tmp_path)
    monkeypatch.setattr(config, 'MAX_CONCURRENT_JOBS', 0)   # nothing ever starts

    async def scenario():
        manager = jobs.JobManager()
        mine, theirs = storage.Session.create(), storage.Session.create()
        first = mine.new_run()
        await manager.submit(mine, first)
        with pytest.raises(jobs.AlreadyActive) as refused:
            await manager.submit(mine, mine.new_run())
        assert refused.value.run_id == first.id

        # Someone else's workspace still queues behind it.
        await manager.submit(theirs, theirs.new_run())
        assert manager.status_for(first)['stage'] == 'queued (position 1)'

        # Once the first is cancelled, the same workspace may submit again.
        await manager.cancel(mine, first)
        assert manager.active_for(mine.id) is None
        await manager.submit(mine, mine.new_run())
        await manager.shutdown()

    asyncio.run(scenario())


# --- NPAtlas + supplemental -------------------------------------------------

def test_the_supplemental_table_is_searched_with_npatlas(tmp_path, monkeypatch):
    import pandas as pd

    from app import config
    from app.plotdata import feature

    shipped = Path(__file__).resolve().parents[1] / 'data' / 'npatlas_supplemental.tsv'
    supplemental = pd.read_csv(shipped, sep='\t')
    head = list(supplemental.columns[:34])
    atlas = tmp_path / 'npatlas.tsv'
    pd.DataFrame([{**{c: '' for c in head}, 'npaid': 'NPA000001', 'compound_id': 1,
                   'compound_name': 'Desferrioxamine B', 'compound_m_plus_h': 561.3607,
                   'compound_m_plus_na': 583.3426, 'origin_type': 'Bacterium'}],
                 columns=head).to_csv(atlas, sep='\t', index=False)
    monkeypatch.setattr(config, 'NPATLAS_PATH', atlas)
    monkeypatch.setattr(config, 'NPATLAS_SUPPLEMENTAL_PATH', shipped)

    combined = feature.combined_atlas()
    assert combined.index.is_unique
    assert len(combined) == 1 + len(supplemental)
    assert combined.loc[1, 'source_database'] == 'NPAtlas'

    hits = feature.matches_for_mass(feature.load_atlas('Bacterium'), 519.3501, 5)
    assert [h['compound_name'] for h in hits] == ['Desferrioxamine G1t']
    assert hits[0]['source_database'] == 'SIDERITE'

    # The wrong SIDERITE mirubactin (C26H32N6O12) is not in the table.
    assert not (supplemental.compound_molecular_formula == 'C26H32N6O12').any()


def test_without_npatlas_the_supplemental_table_still_searches(tmp_path, monkeypatch):
    from app import config
    from app.plotdata import feature
    monkeypatch.setattr(config, 'NPATLAS_PATH', tmp_path / 'missing.tsv')
    assert feature.atlas_available()
    assert len(feature.combined_atlas()) > 500
