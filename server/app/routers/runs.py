"""Starting, monitoring and cancelling analysis runs."""

import shutil
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from .. import config, jobs, storage
from ..deps import get_file, get_run, get_session
from ..schemas import AnalysisSettings, JobStatus, RunRequest

router = APIRouter(prefix='/api/runs', tags=['runs'])


@router.get('/defaults')
def defaults():
    """The form's initial state: the desktop's Qt Designer widget defaults."""
    return {'settings': AnalysisSettings().model_dump()}


@router.get('')
def list_runs(session: storage.Session = Depends(get_session)):
    """This session's runs, newest first, with just enough to build a list."""
    out = []
    for run in session.runs():
        status = jobs.manager.status_for(run) or {'state': 'unknown'}
        request = run.read_request() or {}
        out.append({
            'run_id': run.id,
            'state': status.get('state'),
            'stage': status.get('stage', ''),
            'label': request.get('label'),
            'created_at': run.created_at,
            'started_at': status.get('started_at'),
            'finished_at': status.get('finished_at'),
            'peaktable': (request.get('inputs') or {}).get('peaktable_name'),
        })
    return out


@router.post('')
async def create_run(payload: RunRequest, session: storage.Session = Depends(get_session)):
    """Copy the selected library files into a fresh run and queue it.

    The copy is what makes a run reproducible and self-contained: the
    pipeline converts and rewrites its peak table in place (as the desktop
    does), and deleting a library file later must not disturb a run that has
    already started.
    """
    peaktable = get_file(session, payload.inputs.peaktable, 'peaktable')
    samplelist = get_file(session, payload.inputs.samplelist, 'samplelist')
    metadata = get_file(session, payload.inputs.metadata, 'metadata')
    fragments = (get_file(session, payload.inputs.fragments, 'fragments')
                 if payload.inputs.fragments else None)

    active = jobs.manager.active_for(session.id)
    if active is not None:
        return _already_active(active)

    settings = payload.settings
    if settings.blnkfltr and not settings.blnkgrp:
        raise HTTPException(status_code=422,
                            detail='blank filtering is on but no blank group was selected')
    if settings.Ttest and not (settings.expgrp and settings.ctrgrp):
        raise HTTPException(
            status_code=422,
            detail='the t-test needs both an experimental and a control group')
    if settings.expgrp and settings.expgrp == settings.ctrgrp:
        raise HTTPException(status_code=422,
                            detail='experimental and control groups must differ')

    run = session.new_run()
    copied = {}
    for kind, record in (('peaktable', peaktable), ('samplelist', samplelist),
                         ('metadata', metadata), ('fragments', fragments)):
        if record is None:
            continue
        destination = run.rawdata / record.name
        shutil.copy2(session.path_for(record), destination)
        copied[f'{kind}_name'] = record.name

    request = payload.model_dump()
    request['inputs'].update(copied)
    request['created_at'] = time.time()
    run.write_request(request)

    try:
        await jobs.manager.submit(session, run)
    except jobs.AlreadyActive as exc:
        shutil.rmtree(run.root, ignore_errors=True)
        return _already_active(exc.run_id)
    return {'run_id': run.id, 'state': 'queued'}


def _already_active(run_id):
    return JSONResponse(status_code=409, content={
        'detail': 'You already have an analysis queued or running. '
                  'Wait for it to finish, or cancel it.',
        'run_id': run_id,
    })


@router.get('/{run_id}/status', response_model=JobStatus)
def run_status(run=Depends(get_run)):
    status = jobs.manager.status_for(run)
    if status is None:
        raise HTTPException(status_code=404, detail='run has no status')
    status.setdefault('run_id', run.id)
    request = run.read_request() or {}
    status.setdefault('label', request.get('label'))
    return JobStatus(**{k: v for k, v in status.items()
                        if k in JobStatus.model_fields})


@router.get('/{run_id}/log')
def run_log(run=Depends(get_run), tail: int = 64 * 1024):
    """Tail of the pipeline's stdout/stderr.

    The pipeline reports progress by printing, so this is the live progress
    view while a run is in flight and the diagnostic record afterwards.

    Absolute paths are rewritten to a placeholder on the way out: the
    pipeline prints where it wrote each output, which is useful context, but
    the server's real directory layout is nobody's business but the
    operator's. The log on disk keeps the full paths.
    """
    text = run.read_log(tail_bytes=max(1024, min(tail, 1024 * 1024)))
    return {'run_id': run.id, 'log': _redact_paths(text)}


def _redact_paths(text):
    """Replace the server's data root with a stable placeholder.

    Both separator styles are handled because the log can carry either
    depending on how a path was constructed.
    """
    root = str(config.DATA_ROOT)
    if not root:
        return text
    for variant in {root, root.replace('\\', '/'), root.replace('/', '\\')}:
        text = text.replace(variant, '<data>')
    return text


@router.post('/{run_id}/cancel')
async def cancel_run(run=Depends(get_run), session: storage.Session = Depends(get_session)):
    status = await jobs.manager.cancel(session, run)
    return {'run_id': run.id, 'state': status.get('state')}


@router.delete('/{run_id}')
async def delete_run(run=Depends(get_run), session: storage.Session = Depends(get_session)):
    """Cancel if needed, then remove the run and everything it produced."""
    await jobs.manager.cancel(session, run)
    shutil.rmtree(run.root, ignore_errors=True)
    return {'deleted': run.id}


@router.get('/{run_id}/request')
def run_request(run=Depends(get_run)):
    """The submitted parameters, for re-running with tweaks."""
    request = run.read_request()
    if request is None:
        raise HTTPException(status_code=404, detail='run request not found')
    return request
