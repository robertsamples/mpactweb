"""MS-DIAL exports from different versions, in both file types.

MS-DIAL 5.5 put five more annotation columns before the samples and writes
a .csv in which a compound name it has already quoted comes out half
escaped (``""2,8-Dimethyl-5,7-dimethoxychromone""``). Both broke the import
in class. These build small exports in the old and new layouts and check
that every one reaches the pipeline with the same injections, features and
abundances.
"""

import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import msdial  # noqa: E402
from app.core import mzmineimport  # noqa: E402

NEW_COLUMNS = ['Measured terms', 'Evidence source', 'Candidates found',
               'Candidates above threshold', 'Candidates reference matched']
INJECTIONS = ['blank_r1', 'blank_r2', 'A_S1_Cu_r1', 'A_S1_Cu_r2', 'A_S2_Cu_r1', 'A_S2_Cu_r2']
NAMES = ['Unknown', '"3,4-DIHYDROXYPHENYLACETATE"', 'terrein',
         '"NCGC00160211-01!2,3-Dihydro-1H-pyrrolo[2,1-c][1,4]benzodiazepine-5,11(10H,11aH)-dione"']
ONTOLOGY = ['null', 'Catechols', '1,2-diols', '1,4-benzodiazepines']


def export_rows(new_layout=True, summary=True):
    """Cells of a small alignment export, as the .txt export holds them."""
    meta = (['Alignment ID', 'Average Rt(min)', 'Average Mz', 'Metabolite name', 'Adduct type']
            + [f'm{i}' for i in range(30)])          # 35 annotation columns, as MS-DIAL 5.4
    if new_layout:
        meta += NEW_COLUMNS                          # 40, as MS-DIAL 5.5
    lead = [''] * (len(meta) - 1)
    pairs = ['Average', 'Stdev'] if summary else []
    tail = ['NA'] * len(pairs)
    rows = [
        lead + ['Class'] + ['B', 'B', 'Cu', 'Cu', 'Cu', 'Cu'] + tail,
        lead + ['File type'] + ['Blank', 'Blank'] + ['Sample'] * 4 + tail,
        lead + ['Injection order'] + [str(i) for i in range(1, 7)] + tail,
        lead + ['Batch ID'] + ['1'] * 6 + pairs,
        meta + INJECTIONS + (['Cu', 'Cu'] if summary else []),
    ]
    for feature in range(4):
        cells = [str(feature), f'{1 + feature * 0.517:.3f}', f'{150.04938 + feature * 10.1:.5f}',
                 NAMES[feature], '[M+H]+'] + ['null'] * 30
        cells[10] = ONTOLOGY[feature]
        if new_layout:
            cells += ['Spectrum,AccurateMass', 'null', '3', '1', '1']
        values = [str(100 * feature + i * 7) for i in range(6)]
        rows.append(cells + values + (['12.5', '3.25'] if summary else []))
    return rows


def as_txt(rows):
    return ''.join('\t'.join(row) + '\r\n' for row in rows)


def as_msdial_csv(rows):
    """What MS-DIAL's .csv export does: a field it already quoted gets its
    quotes doubled but is not enclosed; any other field with a comma is
    enclosed properly."""
    def cell(value):
        if value.startswith('"'):
            return value.replace('"', '""')
        if ',' in value:
            return '"' + value + '"'
        return value
    return ''.join(','.join(cell(value) for value in row) + '\r\n' for row in rows)


class _Shim:
    def __init__(self, filename):
        self.filename = Path(filename)
        self.messages = []

    def error(self, message):
        self.messages.append(message)


def canonical(path):
    """Normalize, then run the vendored converter, as an upload + run does."""
    path = msdial.normalize(path) or path
    shim = _Shim(path)
    mzmineimport.format_check(shim)
    assert not shim.messages, shim.messages
    return pd.read_csv(shim.filename, header=None, low_memory=False)


@pytest.mark.parametrize('new_layout', [True, False])
@pytest.mark.parametrize('suffix', ['.csv', '.txt'])
def test_every_layout_reaches_the_pipeline_intact(tmp_path, new_layout, suffix):
    rows = export_rows(new_layout)
    path = tmp_path / f'Area_1{suffix}'
    path.write_bytes((as_txt(rows) if suffix == '.txt' else as_msdial_csv(rows)).encode('utf-8'))
    table = canonical(path)
    assert list(table.iloc[2, :3]) == ['Compound', 'm/z', 'Retention time (min)']
    assert list(table.iloc[2, 3:]) == INJECTIONS
    body = table.iloc[3:]
    assert len(body) == 4
    for feature in range(4):
        row = body.iloc[feature]
        assert float(row.iloc[1]) == pytest.approx(150.04938 + feature * 10.1)
        assert float(row.iloc[2]) == pytest.approx(1 + feature * 0.517)
        assert [float(v) for v in row.iloc[3:]] == [100 * feature + i * 7 for i in range(6)]


def test_csv_and_txt_exports_give_the_same_table(tmp_path):
    rows = export_rows()
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    csv_path = tmp_path / 'a' / 'Area.csv'
    txt_path = tmp_path / 'b' / 'Area.txt'
    csv_path.write_bytes(as_msdial_csv(rows).encode('utf-8'))
    txt_path.write_bytes(as_txt(rows).encode('utf-8'))
    assert canonical(csv_path).equals(canonical(txt_path))


def test_the_half_quoted_csv_reads_like_the_txt():
    rows = export_rows()
    for row in rows:
        line = as_msdial_csv([row]).rstrip('\r\n')
        assert msdial.split_csv_line(line) == row


@pytest.mark.parametrize('line, cells', [
    ('a,b,c', ['a', 'b', 'c']),
    ('a,"1,2-diols",c', ['a', '1,2-diols', 'c']),
    ('a,""2,3-x"",c', ['a', '"2,3-x"', 'c']),
    ('a,"",c', ['a', '', 'c']),
    ('a,"say ""hi""",c', ['a', 'say "hi"', 'c']),
    ('a,b,', ['a', 'b', '']),
    ('""x,y""', ['"x,y"']),
])
def test_split_csv_line(line, cells):
    assert msdial.split_csv_line(line) == cells


def test_the_rewrite_is_the_legacy_layout_and_idempotent(tmp_path):
    path = tmp_path / 'Area.csv'
    path.write_bytes(as_msdial_csv(export_rows()).encode('utf-8'))
    out = msdial.normalize(path)
    assert out == tmp_path / 'Area.txt' and not path.exists()
    lines = out.read_text(encoding='utf-8').splitlines()
    assert lines[0].split('\t')[34] == 'Class'
    assert lines[4].split('\t')[:3] == ['Alignment ID', 'Average Rt(min)', 'Average Mz']
    assert lines[4].split('\t')[35:41] == INJECTIONS
    assert '"' not in out.read_text(encoding='utf-8')
    before = out.read_bytes()
    assert msdial.normalize(out) is None
    assert out.read_bytes() == before


def test_an_export_without_average_columns_still_converts(tmp_path):
    path = tmp_path / 'Area.txt'
    path.write_bytes(as_txt(export_rows(summary=False)).encode('utf-8'))
    table = canonical(path)
    assert list(table.iloc[2, 3:]) == INJECTIONS


def test_a_row_split_by_an_unexpected_comma_is_put_back_together(tmp_path):
    rows = export_rows()
    text = as_msdial_csv(rows).replace('""3,4-DIHYDROXYPHENYLACETATE""', '3,4-DIHYDRO,XY')
    path = tmp_path / 'Area.csv'
    path.write_bytes(text.encode('utf-8'))
    table = canonical(path)
    assert [float(v) for v in table.iloc[4, 3:]] == [100 + i * 7 for i in range(6)]


def test_a_line_cut_short_in_the_samples_is_refused(tmp_path):
    rows = export_rows()
    rows[6] = rows[6][:-4]                 # two abundances and the summary gone
    path = tmp_path / 'Area.txt'
    path.write_bytes(as_txt(rows).encode('utf-8'))
    with pytest.raises(msdial.MsdialError, match='line 7'):
        msdial.normalize(path)


def test_a_line_missing_only_its_averages_is_kept(tmp_path):
    rows = export_rows()
    rows[6] = rows[6][:-2]                 # only Average/Stdev missing
    path = tmp_path / 'Area.txt'
    path.write_bytes(as_txt(rows).encode('utf-8'))
    table = canonical(path)
    assert [float(v) for v in table.iloc[4, 3:]] == [100 + i * 7 for i in range(6)]


def test_other_files_are_not_touched(tmp_path):
    progenesis = tmp_path / 'peaks.csv'
    progenesis.write_text(',,\n,,\nCompound,m/z,Retention time (min)\n', encoding='utf-8')
    assert msdial.normalize(progenesis) is None
    notes = tmp_path / 'notes.txt'
    notes.write_text('hello\tworld\n', encoding='utf-8')
    assert msdial.normalize(notes) is None


def test_an_export_without_sample_columns_is_refused(tmp_path):
    rows = [row[:40] for row in export_rows()]
    path = tmp_path / 'Area.txt'
    path.write_bytes(as_txt(rows).encode('utf-8'))
    with pytest.raises(msdial.MsdialError):
        msdial.normalize(path)


CLASS_DATA = Path(__file__).resolve().parents[2] / 'temp' / 'data'


@pytest.mark.skipif(not CLASS_DATA.is_dir(), reason='the CHM350 exports are not on this machine')
def test_the_class_exports_in_both_formats_agree(tmp_path):
    """The two datasets exported both ways (scratch data, not in the repo)."""
    for folder, csv_name, txt_name in (
            ('cp ms data', 'Area_1_2026_11_09_14_55_12.csv', 'Area_1_2026_11_09_15_17_38.txt'),
            ('sas data', 'Area_1_2026_11_09_15_15_29.csv', 'Area_1_2026_11_09_15_16_32.txt')):
        a, b = tmp_path / folder / 'a', tmp_path / folder / 'b'
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        shutil.copy2(CLASS_DATA / folder / csv_name, a / 'peaks.csv')
        shutil.copy2(CLASS_DATA / folder / txt_name, b / 'peaks.txt')
        assert canonical(a / 'peaks.csv').equals(canonical(b / 'peaks.txt'))
