"""Shared FastAPI dependencies: session resolution and run/result lookup."""

from fastapi import Depends, HTTPException, Request, Response

from . import config, storage
from .results import MissingResults, RunResults


def get_session(request: Request, response: Response) -> storage.Session:
    """Resolve (or mint) the caller's session and refresh its cookie.

    A session is just a directory of uploads and runs -- there is no login.
    The cookie is refreshed on every request so an actively used session
    doesn't expire mid-analysis.
    """
    session = storage.Session.load_or_create(request.cookies.get(config.SESSION_COOKIE))
    response.set_cookie(
        config.SESSION_COOKIE, session.id,
        max_age=config.SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite='lax',
        secure=config.SESSION_COOKIE_SECURE,
    )
    return session


def get_run(run_id: str, session: storage.Session = Depends(get_session)):
    run = session.run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f'run {run_id} not found')
    return run


def get_results(run=Depends(get_run)) -> RunResults:
    """A ``RunResults`` for a completed run.

    A run that hasn't finished is a 409, not a 404: the client should keep
    polling rather than treat the run as gone.
    """
    status = run.read_status() or {}
    state = status.get('state')
    if state != 'complete':
        raise HTTPException(
            status_code=409,
            detail=f'run {run.id} is {state or "not started"}; results are not ready')
    try:
        return RunResults(run)
    except MissingResults as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def get_file(session: storage.Session, file_id: str, kind=None):
    record = session.file(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f'file {file_id} not found')
    if kind and record.kind != kind:
        raise HTTPException(status_code=400,
                            detail=f'file {file_id} is a {record.kind}, expected {kind}')
    return record
