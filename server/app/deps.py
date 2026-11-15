"""Shared FastAPI dependencies: session resolution and run/result lookup."""

from fastapi import Depends, HTTPException, Request, Response

from . import accounts, auth, config, storage
from .results import MissingResults, RunResults


def get_session(request: Request, response: Response) -> storage.Session:
    """Resolve (or mint) the caller's session and refresh its cookie.

    A session is a directory of uploads and runs. Without authentication it
    belongs to a browser, and the cookie is refreshed on every request so an
    actively used session doesn't expire mid-analysis. With named accounts it
    belongs to the *account* instead, so someone's uploads and results are
    there when they sign in from a different machine -- which is the whole
    point of having named accounts on a shared instrument-facility server.
    """
    user = getattr(request.state, 'user', None)
    account = getattr(request.state, 'account', None)
    if user and auth.mode() == 'users':
        return storage.Session.at_id(auth.workspace_id(user))
    if account is not None:
        accounts.touch(account['email'])
        # The workspace belongs to the account, not the browser, so no
        # session cookie is needed.
        return storage.Session.at_id(account['workspace'])
    session = storage.Session.load_or_create(request.cookies.get(config.SESSION_COOKIE))
    response.set_cookie(
        config.SESSION_COOKIE, session.id,
        max_age=config.SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite='lax',
        secure=config.SESSION_COOKIE_SECURE,
    )
    return session


def get_role(request: Request) -> str:
    """'internal' or 'external'. Without email accounts there is no
    outside user to keep anything from, so everyone is internal."""
    account = getattr(request.state, 'account', None)
    return account['role'] if account is not None else 'internal'


def limits(role):
    """``(max runs, max bytes)`` for a role; 0 means no limit."""
    if role == 'internal':
        return config.INTERNAL_MAX_RUNS, config.INTERNAL_MAX_STORAGE_MB * 1024 * 1024
    return config.EXTERNAL_MAX_RUNS, config.EXTERNAL_MAX_STORAGE_MB * 1024 * 1024


def check_storage(session, role, adding=0):
    """Refuse (413) when ``adding`` more bytes would pass the workspace's
    storage quota."""
    _, max_bytes = limits(role)
    if not max_bytes:
        return
    used = session.usage()[0]
    if used + adding > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f'this workspace is using {used / 2**20:,.0f} MB of its '
                   f'{max_bytes / 2**20:,.0f} MB; delete old runs or uploads to make room')


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
