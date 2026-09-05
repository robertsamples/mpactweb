"""Opening a saved ``.mpct`` session.

A save file is the desktop's own format, so these tests build one the way
``MainWindow.write_save`` does and check the web app restores from it what the
desktop restores: the four input files, every filtering and analysis
parameter, the statistics groups and the Plot Feature Sets.

Two hazards get their own tests because they only bite in deployment:

* a save file is a **pickle**, and pickles arriving over HTTP are executable
  unless something stops them;
* saves are written on Windows and Macs but the server will run on Linux, and
  ``pathlib`` paths do not unpickle across platforms without help.
"""

import os
import pickle
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    os.environ['MPACT_DATA_ROOT'] = str(tmp_path_factory.mktemp('mpact-session-data'))
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app
    config.reload()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope='module')
def demo_frames():
    """The bundled demo files, read the way ``write_save`` reads them."""
    from app import demo

    return {
        'peaktable': pd.read_csv(demo.source_path('200826_PTY087I2codingdataset.csv'),
                                 sep=',', header=None, index_col=None, low_memory=False),
        'samplelist': pd.read_csv(demo.source_path('samplelist.csv'), sep=',',
                                  header=None, index_col=None),
        'extractmetadata': pd.read_csv(demo.source_path('extractmetadata.csv'), sep=',',
                                       header=None, index_col=None),
        'fragdb': demo.source_path('200826_PTY087I2codingdataset.msp').read_text(
            encoding='utf-8', errors='replace'),
    }


def build_params(path_type=Path):
    """An ``analysis_parameters`` filled in as the desktop leaves it at save
    time, with the input paths in whichever ``pathlib`` flavour is being
    exercised."""
    from app.core import MSFaST, groupsets

    params = MSFaST.analysis_parameters()
    root = 'C:\\data' if path_type is PureWindowsPath else '/data'
    params.filename = path_type(f'{root}/200826_PTY087I2codingdataset.csv')
    params.samplelistfilename = path_type(f'{root}/samplelist.csv')
    params.extractmetadatafilename = path_type(f'{root}/extractmetadata.csv')
    params.fragfilename = path_type(f'{root}/200826_PTY087I2codingdataset.msp')
    params.outputdir = path_type(f'{root}/output')

    params.graphfilters = ['cv', 'insource']       # mispick filter off
    params.relfil, params.CVfil, params.decon = False, True, True
    params.merge = False
    params.RTwin, params.ringingwin, params.isopeakwin = 0.007, 0.4, 0.02
    params.maxisowin = 3.0
    params.deconthresh = 0.9
    params.blnkfltr, params.blnkgrp = True, 'Blanks'
    params.blankfilparam, params.blankfilthresh = 'absolute', 250.0
    params.cvthresh, params.cvparam = 0.35, 'average CV'
    params.statstgrps = ['0um_Ce', '250um_Ce']
    params.FC = params.Ttest = True
    params.FDR = False
    params.PCA = params.Dendrogram = params.MZRTplt = params.KMD = True
    params.FC3Dplt = params.Volcanoplt = True
    params.ppmthresh, params.kingdom, params.genus = 7.5, 'Bacterium', 'Streptomyces'

    model = groupsets.GroupSetModel()
    model.add('Not in blanks', ['Blanks', '0um_Ce', '250um_Ce', 'Media'])
    model.update(model.selected_index, name='Not in blanks',
                 src=['0um_Ce', '250um_Ce', 'Media'], incl=[], excl=['Blanks'],
                 colour='#26a69a')
    params.queries = model.to_legacy_list()
    return params


def save_bytes(demo_frames, path_type=Path):
    payload = {'parameters': build_params(path_type), **demo_frames}
    return pickle.dumps(payload)


def post(client, blob, filename='session.mpct'):
    return client.post('/api/files/session',
                       files={'file': (filename, blob, 'application/octet-stream')})


# --- the happy path --------------------------------------------------------

def test_import_restores_every_input_file(client, demo_frames):
    body = post(client, save_bytes(demo_frames)).json()

    assert set(body['inputs']) == {'peaktable', 'samplelist', 'metadata', 'fragments'}
    by_kind = {record['kind']: record for record in body['files']}
    assert by_kind['peaktable']['name'] == '200826_PTY087I2codingdataset.csv'
    assert by_kind['fragments']['name'] == '200826_PTY087I2codingdataset.msp'

    # Inspected on arrival like any upload, so File Selection has something to
    # show and the peak table is recognised rather than re-converted.
    assert by_kind['peaktable']['fmt'] == 'progenesis'
    assert by_kind['peaktable']['detail']['features'] > 1000
    assert by_kind['samplelist']['detail']['missing_columns'] == []


def test_import_restores_every_setting(client, demo_frames):
    settings = post(client, save_bytes(demo_frames)).json()['settings']

    assert settings['relfil'] is False          # 'rel' absent from graphfilters
    assert settings['CVfil'] is True
    assert settings['decon'] is True
    assert settings['merge'] is False
    assert settings['RTwin'] == pytest.approx(0.007)
    assert settings['ringingwin'] == pytest.approx(0.4)
    assert settings['isopeakwin'] == pytest.approx(0.02)
    assert settings['maxisowin'] == pytest.approx(3.0)
    assert settings['deconthresh'] == pytest.approx(0.9)

    assert settings['blnkfltr'] is True
    assert settings['blnkgrp'] == 'Blanks'
    assert settings['blankfilparam'] == 'absolute'
    assert settings['blankfilthresh'] == pytest.approx(250.0)

    assert settings['cvthresh'] == pytest.approx(0.35)
    assert settings['cvparam'] == 'average CV'

    assert settings['FDR'] is False
    assert settings['expgrp'] == '0um_Ce'
    assert settings['ctrgrp'] == '250um_Ce'
    assert settings['ppmthresh'] == pytest.approx(7.5)
    assert settings['kingdom'] == 'Bacterium'
    assert settings['genus'] == 'Streptomyces'


def test_import_restores_plot_feature_sets(client, demo_frames):
    settings = post(client, save_bytes(demo_frames)).json()['settings']

    assert len(settings['groupsets']) == 1
    first = settings['groupsets'][0]
    assert first['name'] == 'Not in blanks'
    assert first['excl'] == ['Blanks']
    assert set(first['src']) == {'0um_Ce', '250um_Ce', 'Media'}
    assert first['colour'] == '#26a69a'


def test_restored_settings_are_runnable(client, demo_frames):
    """What comes back has to satisfy the run request's own schema, or the
    import would leave the form in a state the Run button rejects."""
    from app.schemas import AnalysisSettings

    body = post(client, save_bytes(demo_frames)).json()
    AnalysisSettings(**body['settings'])           # raises if the import lied


def test_importing_twice_does_not_duplicate_the_library(client, demo_frames):
    blob = save_bytes(demo_frames)
    first = post(client, blob).json()
    before = len(client.get('/api/files').json())
    second = post(client, blob).json()
    after = len(client.get('/api/files').json())

    assert after == before, 'a re-import copied the same files into the library again'
    assert second['inputs'] == first['inputs']


# --- the legacy feature-set shape ------------------------------------------

def test_main_query_is_mapped_to_the_local_stand_in():
    """Old saves pickle feature sets as ``main.query`` when the desktop was
    imported as a module, and as ``__main__.query`` when it was launched as a
    script -- the normal way to start it. Neither module exists on the
    server."""
    import io

    from app import mpctsession

    unpickler = mpctsession._SessionUnpickler(io.BytesIO(b''))
    assert unpickler.find_class('main', 'query') is mpctsession.LegacyQuery
    assert unpickler.find_class('__main__', 'query') is mpctsession.LegacyQuery


def test_legacy_query_objects_become_feature_sets():
    from app import mpctsession

    params = build_params()
    legacy = mpctsession.LegacyQuery()
    legacy.name, legacy.colour = 'Old set', '#ff0000'
    legacy.src, legacy.incl, legacy.excl = ['Media'], [], ['Blanks']
    params.queries = [legacy]

    warnings = []
    sets = mpctsession.groupsets_from(params, warnings)
    assert [item['name'] for item in sets] == ['Old set']
    assert sets[0]['excl'] == ['Blanks']
    assert sets[0]['src'] == ['Media']
    assert not warnings


# --- portability -----------------------------------------------------------

def _forge_path_class(payload, pure, concrete):
    """A pickle naming ``pathlib.<concrete>`` for its paths.

    Protocol 2 writes a class reference as the literal text
    ``cpathlib\nPureWindowsPath\n`` and adds none of protocol 4+'s
    length-prefixed frames, so the name can be swapped without the byte count
    mattering. That is the only way to produce a ``WindowsPath`` pickle on a
    machine that can build one and a ``PosixPath`` pickle on one that cannot.
    """
    blob = pickle.dumps(payload, protocol=2)
    forged = blob.replace(f'cpathlib\n{pure.__name__}\n'.encode(),
                          f'cpathlib\n{concrete}\n'.encode())
    assert forged != blob, 'the test failed to forge the path class'
    return forged


@pytest.mark.parametrize('flavour, pure, concrete', [
    ('windows', PureWindowsPath, 'WindowsPath'),
    ('posix', PurePosixPath, 'PosixPath'),
])
def test_paths_from_another_platform_load(flavour, pure, concrete):
    """A save written on Windows must open on a Linux server, and vice versa.

    ``WindowsPath`` cannot even be constructed on Linux, so unpickling one
    there raises ``NotImplementedError``; the reader substitutes the pure
    form, which can be built anywhere and still knows its file name.
    """
    from app import mpctsession

    blob = _forge_path_class({'parameters': build_params(pure)}, pure, concrete)
    payload = mpctsession.load(blob)

    assert isinstance(payload['parameters'].filename, pure)
    assert payload['parameters'].filename.name == '200826_PTY087I2codingdataset.csv'


def test_a_session_with_foreign_paths_imports_end_to_end(client, demo_frames):
    """The same file names have to survive all the way into the library."""
    body = post(client, save_bytes(demo_frames, PureWindowsPath)).json()

    names = {record['kind']: record['name'] for record in body['files']}
    assert names['peaktable'] == '200826_PTY087I2codingdataset.csv'
    assert names['samplelist'] == 'samplelist.csv'
    assert names['metadata'] == 'extractmetadata.csv'


# --- a pickle is executable unless something stops it ----------------------

class _Exploit:
    """Pickles to a call of ``os.system``, the classic malicious-pickle
    payload. Nothing is executed here: the reader must refuse it first."""

    def __reduce__(self):
        import os as _os
        return (_os.system, ('echo pwned',))


def test_a_hostile_pickle_is_refused(client, demo_frames):
    blob = pickle.dumps({'parameters': _Exploit(), **demo_frames})
    response = post(client, blob)

    assert response.status_code == 422
    # `os.system` is `nt.system` on Windows; either way the reader names it
    # and refuses rather than calling it.
    assert 'system' in response.json()['detail']


def test_reader_refuses_arbitrary_callables_directly():
    """The same guard, asserted at the reader rather than through HTTP."""
    from app import mpctsession

    with pytest.raises(mpctsession.SessionError):
        mpctsession.load(pickle.dumps({'parameters': _Exploit()}))


# --- rejections ------------------------------------------------------------

def test_a_non_session_file_is_rejected(client):
    response = post(client, b'Compound,m/z\n1,2\n', filename='peaks.csv')
    assert response.status_code == 400
    assert '.mpct' in response.json()['detail']


def test_a_corrupt_session_file_is_rejected(client):
    response = post(client, b'not a pickle at all')
    assert response.status_code == 422


def test_a_pickle_that_is_not_a_session_is_rejected(client):
    response = post(client, pickle.dumps({'something': 'else'}))
    assert response.status_code == 422
    assert 'not an MPACT session file' in response.json()['detail']


def test_mpact_extension_is_accepted_too(client, demo_frames):
    """The format is .mpct; people say .mpact."""
    response = post(client, save_bytes(demo_frames), filename='saved.mpact')
    assert response.status_code == 200
