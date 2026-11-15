"""A class's composite sample list and metadata, used with one dataset.

In 2024 the class shared one sample list and one metadata sheet covering
everybody's samples. Any one peak table only uses its own rows; the rest
have to be ignored, and the repeats that pasting sheets together produces
must not stop the run.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import samplemap  # noqa: E402

PEAK = ['blank_r1', 'blank_r2', 'CP_S1_Cu_r1', 'CP_S1_Cu_r2', 'CP_S2_Cu_r1', 'CP_S2_Cu_r2']

SAMPLES = """Injection,Vial,Sample_Code
JM_S1_Cu_r1,1,JM_S1_Cu
JM_S1_Cu_r2,2,JM_S1_Cu
blank_r1,3,blank
blank_r2,4,blank
CP_S1_Cu_r1,5,CP_S1_Cu
CP_S1_Cu_r2 ,6,CP_S1_Cu
CP_S2_Cu_r1,7,CP_S2_Cu
CP_S2_Cu_r2,8,CP_S2_Cu
blank_r1,3,blank
SAS_S1_Eu_r1,9,SAS_S1_Eu
"""

METADATA = """Sample_Code,Organism,Biological_Group
JM_S1_Cu,x,JM_Cu
blank,,Blank
CP_S1_Cu,y,CP_Cu
CP_S1_Cu,y,CP_Cu
CP_S2_Cu,y,CP_Cu
blank,,Blank
SAS_S1_Eu,z,SAS_Eu
"""


def _write(tmp_path, samples=SAMPLES, metadata=METADATA):
    s, m = tmp_path / 'samplelist.csv', tmp_path / 'metadata.csv'
    s.write_text(samples, encoding='utf-8')
    m.write_text(metadata, encoding='utf-8')
    return s, m


def test_fit_keeps_only_this_peak_tables_rows(tmp_path):
    s, m = _write(tmp_path)
    assert samplemap.fit(PEAK, s, m) == []
    assert s.read_text(encoding='utf-8').splitlines() == [
        'Injection,Vial,Sample_Code',
        'blank_r1,3,blank',
        'blank_r2,4,blank',
        'CP_S1_Cu_r1,5,CP_S1_Cu',
        'CP_S1_Cu_r2,6,CP_S1_Cu',            # stray space trimmed
        'CP_S2_Cu_r1,7,CP_S2_Cu',
        'CP_S2_Cu_r2,8,CP_S2_Cu',
    ]
    assert m.read_text(encoding='utf-8').splitlines() == [
        'Sample_Code,Organism,Biological_Group',
        'blank,,Blank',
        'CP_S1_Cu,y,CP_Cu',
        'CP_S2_Cu,y,CP_Cu',
    ]


def test_conflicting_repeats_are_reported(tmp_path):
    s, m = _write(tmp_path, metadata=METADATA.replace('CP_S1_Cu,y,CP_Cu\nCP_S2',
                                                      'CP_S1_Cu,y,CP_Eu\nCP_S2'))
    warnings = samplemap.fit(PEAK, s, m)
    assert len(warnings) == 1 and 'CP_S1_Cu' in warnings[0]
    assert 'CP_S1_Cu,y,CP_Cu' in m.read_text(encoding='utf-8')     # the first row wins


def test_numeric_injection_names_match_the_way_pandas_reads_them(tmp_path):
    s, m = _write(tmp_path, samples='Injection,Sample_Code\n001,A\n2,B\n3,C\n',
                  metadata='Sample_Code,Biological_Group\nA,g1\nB,g2\nC,g3\n')
    assert samplemap.fit(['1.0', '2.0'], s, m) == []
    assert s.read_text(encoding='utf-8').splitlines()[1:] == ['001,A', '2,B']


def test_sheets_missing_a_key_column_are_left_alone(tmp_path):
    s, m = _write(tmp_path, samples='Injection,Code\nblank_r1,blank\n')
    assert samplemap.fit(PEAK, s, m) == []
    assert s.read_text(encoding='utf-8') == 'Injection,Code\nblank_r1,blank\n'


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    os.environ['MPACT_DATA_ROOT'] = str(tmp_path_factory.mktemp('mpact-composite'))
    for key in ('MPACT_AUTH', 'MPACT_ACCESS_CODE'):
        os.environ.pop(key, None)
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app
    config.reload()
    with TestClient(app) as test_client:
        yield test_client


def _composite_for(names, extra):
    """A composite sheet: these injections (grouped by their name) plus
    another dataset's rows and a pasted-twice repeat."""
    samples = ['Injection,Sample_Code']
    groups = {}
    for name in names + extra:
        code = name.rsplit('_r', 1)[0]
        samples.append(f'{name},{code}')
        groups[code] = 'Blank' if name.startswith('blank') else code.split('_')[0] + '_' + code.split('_')[-1]
    samples.append(f'{names[0]},{names[0].rsplit("_r", 1)[0]}')          # repeat
    metadata = ['Sample_Code,Biological_Group'] + [f'{c},{g}' for c, g in groups.items()]
    metadata.append(metadata[1])                                           # repeat
    return '\n'.join(samples) + '\n', '\n'.join(metadata) + '\n'


def test_a_composite_sheet_previews_only_this_datasets_groups(client):
    peak = {r['kind']: r for r in client.post('/api/files/demo', json={'id': 'chm350-cp'}).json()}
    names = client.get(f'/api/files/{peak["peaktable"]["id"]}/injections').json()['names']
    samples, metadata = _composite_for(names, ['JM_CA_S1_GC_r1', 'SAS_D_S3_GC_r1'])
    ids = {'peaktable': peak['peaktable']['id']}
    for kind, text in (('samplelist', samples), ('metadata', metadata)):
        ids[kind] = client.post('/api/files', data={'kind': kind},
                                files={'file': (f'{kind}_all.csv', text, 'text/csv')}).json()['id']
    body = client.post('/api/files/groups', json=ids)
    assert body.status_code == 200, body.text
    body = body.json()
    assert body['unresolved_total'] == 0
    assert not any(group.startswith(('JM', 'SAS')) for group in body['groups'])
    assert 'Blank' in body['groups']
