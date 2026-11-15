"""The caches, the atomic writes and the job queue's failure handling.

None of this changes what an analysis computes; it is the layer that has to
stay correct when several people use one server at once, so each piece is
pinned here on its own, without needing a dataset or a run.
"""

import asyncio
import io
import json
import os
import sys
import threading
import time
import zipfile
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    os.environ['MPACT_DATA_ROOT'] = str(tmp_path_factory.mktemp('mpact-stability-data'))
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app
    config.reload()
    with TestClient(app) as test_client:
        yield test_client


# --- Memo -------------------------------------------------------------------

def test_memo_evicts_the_least_recently_used():
    from app.results import Memo
    memo = Memo(limit=2)
    memo.get('a', lambda: 1)
    memo.get('b', lambda: 2)
    memo.get('a', lambda: pytest.fail('a should still be cached'))
    memo.get('c', lambda: 3)          # evicts b, the one not touched since
    assert memo.peek('a') == 1 and memo.peek('c') == 3
    assert memo.peek('b') is None


def test_memo_byte_budget_keeps_the_newest_entry():
    from app.results import Memo
    memo = Memo(limit=10, size_of=len, max_bytes=10)
    memo.put('a', b'x' * 6)
    memo.put('b', b'x' * 6)           # 12 bytes: over budget, a goes
    assert memo.peek('a') is None and memo.peek('b') is not None
    memo.put('huge', b'x' * 100)      # larger than the budget on its own
    assert memo.peek('huge') is not None, 'the newest entry always caches'
    assert len(memo) == 1


def test_memo_builds_once_for_concurrent_callers():
    from app.results import Memo
    memo = Memo(limit=4)
    calls = []
    gate = threading.Event()

    def build():
        calls.append(1)
        gate.wait(5)
        return 'value'

    results = []
    threads = [threading.Thread(target=lambda: results.append(memo.get('k', build)))
               for _ in range(6)]
    for thread in threads:
        thread.start()
    time.sleep(0.1)
    gate.set()
    for thread in threads:
        thread.join(5)
    assert results == ['value'] * 6
    assert len(calls) == 1, 'six requests for one thing should compute it once'


def test_memo_does_not_cache_a_failure():
    from app.results import Memo
    memo = Memo(limit=4)

    def broken():
        raise ValueError('nope')
    with pytest.raises(ValueError):
        memo.get('k', broken)
    assert memo.get('k', lambda: 'ok') == 'ok'


# --- load_csv ---------------------------------------------------------------

def test_load_csv_copies_by_default_and_sees_a_rewrite(tmp_path):
    from app import results
    path = tmp_path / 'table.csv'
    path.write_text('a,b\n1,2\n')
    first = results.load_csv(path)
    first['a'] = 99                                   # must not reach the cache
    assert results.load_csv(path)['a'].tolist() == [1]
    assert results.load_csv(path, copy=False) is results.load_csv(path, copy=False)

    time.sleep(0.02)
    path.write_text('a,b\n1,2\n3,4\n')                # a new size and mtime
    assert results.load_csv(path)['a'].tolist() == [1, 3]


def test_a_reader_waits_for_a_file_being_rewritten(tmp_path):
    from app import results
    path = tmp_path / 'iondict.csv'
    path.write_text('a\n1\n')
    seen = []

    def read():
        seen.append(results.load_csv(path)['a'].tolist())

    with results.exclusive(path):
        reader = threading.Thread(target=read)
        reader.start()
        time.sleep(0.15)
        assert not seen, 'the read should be waiting for the writer'
        path.write_text('a\n1\n2\n')
    reader.join(5)
    assert seen == [[1, 2]]


# --- atomic files -----------------------------------------------------------

def test_atomic_write_leaves_no_scratch_files(tmp_path):
    from app import storage
    target = tmp_path / 'status.json'
    for value in range(5):
        storage.write_text_atomic(target, json.dumps({'n': value}))
    assert storage.read_json(target) == {'n': 4}
    assert [p.name for p in tmp_path.iterdir()] == ['status.json']


def test_status_survives_readers_during_writes(tmp_path, monkeypatch):
    """The server polls status.json while the analysis process rewrites it.
    Every read is a whole status and no write fails, on any platform."""
    from app import config, storage
    monkeypatch.setattr(config, 'SESSIONS_DIR', tmp_path)
    session = storage.Session.create()
    run = session.new_run()
    run.write_status({'run_id': run.id, 'state': 'running', 'n': 0})
    stop = threading.Event()
    bad = []

    def read():
        while not stop.is_set():
            status = run.read_status()
            if not status or status.get('state') != 'running':
                bad.append(status)

    readers = [threading.Thread(target=read) for _ in range(3)]
    for reader in readers:
        reader.start()
    try:
        for n in range(200):
            run.write_status({'run_id': run.id, 'state': 'running', 'n': n})
    finally:
        stop.set()
        for reader in readers:
            reader.join(5)
    assert not bad, f'a reader saw a partial or missing status: {bad[:3]}'
    assert run.read_status()['n'] == 199


def test_read_json_gives_the_default_for_a_missing_or_broken_file(tmp_path):
    from app import storage
    assert storage.read_json(tmp_path / 'absent.json') is None
    broken = tmp_path / 'broken.json'
    broken.write_text('{"half":')
    assert storage.read_json(broken, default={}) == {}


# --- the zip download -------------------------------------------------------

def test_zip_stream_is_a_valid_archive_of_the_files(tmp_path):
    from app.routers.results import _zip_stream
    files = {'a.csv': b'x,y\n' + b'1,2\n' * 50_000, 'b.txt': b'', 'c.mpct': os.urandom(4096)}
    paths = []
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
        paths.append(tmp_path / name)
    paths.append(tmp_path / 'deleted-since-listing.csv')

    chunks = list(_zip_stream(paths))
    assert len(chunks) > 3, 'the archive should arrive in pieces, not one buffer'
    with zipfile.ZipFile(io.BytesIO(b''.join(chunks))) as archive:
        assert archive.testzip() is None
        assert sorted(archive.namelist()) == sorted(files)
        for name, content in files.items():
            assert archive.read(name) == content


# --- JSON responses ---------------------------------------------------------

def test_to_json_handles_numpy_and_refuses_nan():
    import numpy as np

    from app.routers.results import to_json
    body = json.loads(to_json({'i': np.int64(3), 'f': np.float64(0.5), 'b': np.bool_(True),
                               'a': np.arange(3), 'none': None}))
    assert body == {'i': 3, 'f': 0.5, 'b': True, 'a': [0, 1, 2], 'none': None}
    with pytest.raises(ValueError):
        to_json({'x': float('nan')})


def test_blank_numeric_cells_become_none():
    from app.plotdata.feature import _cells
    assert _cells(pd.Series([1.5, float('nan'), float('inf')])) == [1.5, None, None]
    assert _cells(pd.Series([1, 2])) == [1, 2]
    assert _cells(pd.Series(['a', None, float('nan')], dtype=object)) == ['a', None, None]


def test_a_blank_cell_is_never_inside_a_numeric_range():
    """An unsearched feature has no hit count. NaN compared true against any
    bound, so "hits >= 1" used to keep every feature that had none."""
    from app.core import treefilters
    from app.plotdata.feature import _cells
    at_least_one = treefilters.RangeFilter(1.0, None)
    blank, searched = _cells(pd.Series([float('nan'), 3.0]))
    assert not at_least_one.matches(blank)
    assert at_least_one.matches(searched)


# --- HTTP surface -----------------------------------------------------------

def test_static_files_must_be_revalidated_and_api_responses_are_not_stored(client):
    page = client.get('/src/app.js')
    assert page.status_code == 200
    assert page.headers['cache-control'] == 'no-cache'
    assert 'etag' in page.headers
    again = client.get('/src/app.js', headers={'If-None-Match': page.headers['etag']})
    assert again.status_code == 304, 'an unchanged module should cost a 304, not a download'

    assert client.get('/api/health').headers['cache-control'] == 'no-store'
    assert client.get('/assets/mpact-icon.png').headers['cache-control'].startswith('public')


def test_an_oversized_upload_is_refused_before_it_is_read(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, 'MAX_UPLOAD_BYTES', 1024)
    body = b'x' * (3 * 1024 * 1024)
    response = client.post('/api/files', data={'kind': 'samplelist'},
                           files={'file': ('big.csv', body, 'text/csv')})
    assert response.status_code == 413
    assert 'limit' in response.json()['detail']
    assert client.get('/api/files').json() == [], 'nothing should have reached the library'


def test_a_run_whose_files_vanished_is_reported_and_leaves_nothing_behind(client):
    """A library file removed (from another tab, say) between being selected
    and the run starting."""
    from app import storage
    ids = {}
    for kind, name, text in (('peaktable', 'peaks.csv', 'Compound,m/z\n'),
                             ('samplelist', 'samples.csv', 'Injection,Sample_Code\na,s\n'),
                             ('metadata', 'meta.csv', 'Sample_Code,Biological_Group\ns,g\n')):
        response = client.post('/api/files', data={'kind': kind},
                               files={'file': (name, text.encode(), 'text/csv')})
        assert response.status_code == 200, response.text
        ids[kind] = response.json()['id']

    session = storage.Session(client.cookies.get('mpact_session'))
    session.path_for(session.file(ids['samplelist'])).unlink()

    settings = client.get('/api/runs/defaults').json()['settings']
    settings.update(blnkfltr=False, expgrp='g', ctrgrp='h')
    response = client.post('/api/runs', json={'inputs': ids, 'settings': settings})
    assert response.status_code == 409, response.text
    assert 'library' in response.json()['detail']
    assert client.get('/api/runs').json() == [], 'the half-made run should be gone'
    for file_id in ids.values():
        client.delete(f'/api/files/{file_id}')


# --- the job queue ----------------------------------------------------------

class _FakeProcess:
    def __init__(self, alive=False, exitcode=1):
        self._alive, self.exitcode = alive, exitcode
        self.terminated = self.killed = False

    def is_alive(self):
        return self._alive

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self._alive = False

    def join(self, timeout=None):
        pass


def test_one_failed_bookkeeping_pass_does_not_stop_the_queue(tmp_path, monkeypatch):
    """A run whose directory has gone (the session was deleted under it) used
    to raise out of the scheduling pass, which ended the background loop."""
    from app import config, jobs, storage
    monkeypatch.setattr(config, 'SESSIONS_DIR', tmp_path)
    session = storage.Session.create()
    run = session.new_run()
    run.write_status({'run_id': run.id, 'state': 'running'})

    manager = jobs.JobManager()
    manager._running['r_gone'] = (_FakeProcess(), 's_missing', time.time())
    manager._running[run.id] = (_FakeProcess(exitcode=-9), session.id, time.time())

    def explode(session_id, run_id, exitcode, real=manager._reconcile_exit):
        if run_id == 'r_gone':
            raise OSError('directory is gone')
        return real(session_id, run_id, exitcode)
    monkeypatch.setattr(manager, '_reconcile_exit', explode)

    asyncio.run(manager._pump())
    assert manager._running == {}
    status = run.read_status()
    assert status['state'] == 'failed' and '-9' in status['error']


def test_a_job_that_cannot_be_started_is_reported_not_lost(tmp_path, monkeypatch):
    from app import config, jobs, storage
    monkeypatch.setattr(config, 'SESSIONS_DIR', tmp_path)
    session = storage.Session.create()
    run = session.new_run()
    run.write_status({'run_id': run.id, 'state': 'queued'})

    manager = jobs.JobManager()
    manager._queue.append((session.id, run.id))

    def cannot(session_id, run_id):
        raise OSError('no more processes')
    monkeypatch.setattr(manager, '_spawn', cannot)

    asyncio.run(manager._pump())
    status = run.read_status()
    assert status['state'] == 'failed'
    assert 'could not be started' in status['error']


def test_a_timed_out_job_that_ignores_terminate_is_killed(tmp_path, monkeypatch):
    from app import config, jobs, storage
    monkeypatch.setattr(config, 'SESSIONS_DIR', tmp_path)
    monkeypatch.setattr(config, 'JOB_TIMEOUT_SECONDS', 1)
    session = storage.Session.create()
    run = session.new_run()
    run.write_status({'run_id': run.id, 'state': 'running'})

    manager = jobs.JobManager()
    stubborn = _FakeProcess(alive=True)
    manager._running[run.id] = (stubborn, session.id, time.time() - 60)

    asyncio.run(manager._pump())
    assert stubborn.terminated and run.read_status()['stage'] == 'timed out'
    assert manager.live_state(run.id) is None
    asyncio.run(manager._pump())          # still there a pass later: escalate
    assert stubborn.killed
    asyncio.run(manager._pump())
    assert manager._stopping == []


def test_stop_escalates_to_kill():
    from app import jobs
    stubborn = _FakeProcess(alive=True)
    jobs._stop(stubborn, grace=0.01)
    assert stubborn.terminated and stubborn.killed


# --- hit index --------------------------------------------------------------

def test_references_are_told_apart_by_serial_not_address():
    """Cache keys used ``id()``, which a new object can share with one that
    has been freed."""
    import inspect

    from app.plotdata import hits
    assert 'ref.serial' in inspect.getsource(hits.hit_index)
    assert 'id(ref)' not in inspect.getsource(hits.hit_index)
