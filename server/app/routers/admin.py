"""The Admin page: who has signed up, from which domains, and what they are.

Only for the addresses in ``MPACT_ADMIN_EMAILS``, and only with email
accounts. To anyone else these routes do not exist (404), which says less
than "forbidden" would.
"""

import asyncio
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import accounts, auth, config, mailer, storage
from ..schemas import AccountUpdate, AllowlistEntry

router = APIRouter(prefix='/api/admin', tags=['admin'])


def require_admin(request: Request):
    account = getattr(request.state, 'account', None)
    if auth.mode() != 'email' or account is None or not account['admin']:
        raise HTTPException(status_code=404, detail='Not Found')
    return account


def _with_usage(account):
    session = storage.Session(account['workspace'])
    used, runs = session.usage() if session.root.is_dir() else (0, 0)
    return {**account, 'storage_bytes': used, 'runs': runs}


@router.get('/users')
async def users(admin=Depends(require_admin)):
    listed = await asyncio.to_thread(lambda: [_with_usage(a) for a in accounts.list_accounts()])
    by_domain = Counter(account['domain'] for account in listed)
    roles = {account['domain']: account['automatic_role'] for account in listed}
    return {
        'users': [{key: account[key] for key in (
            'email', 'domain', 'role', 'automatic_role', 'role_override', 'status', 'admin',
            'created', 'last_seen', 'storage_bytes', 'runs')} for account in listed],
        'domains': [{'domain': domain, 'count': count, 'role': roles[domain]}
                    for domain, count in by_domain.most_common()],
        'allowlist': accounts.allowlist(),
        'internal_domains': accounts.internal_domains(),
        'signup': config.SIGNUP,
        'quota': {'external_runs': config.EXTERNAL_MAX_RUNS,
                  'external_storage_mb': config.EXTERNAL_MAX_STORAGE_MB,
                  'internal_runs': config.INTERNAL_MAX_RUNS,
                  'internal_storage_mb': config.INTERNAL_MAX_STORAGE_MB},
        'mail': mailer.configured(),
    }


@router.post('/users')
def update_user(payload: AccountUpdate, admin=Depends(require_admin)):
    """Set or reset an account's role, or approve a waiting sign-up."""
    try:
        email = accounts.normalise(payload.email)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if email == admin['email'] and payload.role == 'blocked':
        raise HTTPException(status_code=422, detail='you cannot block your own account')

    before = accounts.lookup(email)
    role = None if payload.reset_role else (payload.role if payload.role else ...)
    try:
        account = accounts.update_account(email, role=role,
                                          status='active' if payload.approve else None)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if account is None:
        raise HTTPException(status_code=404, detail=f'no account for {email}')

    if payload.approve and before and before['status'] != 'active':
        try:
            mailer.send(email, 'Your MPACT account is ready', (
                'Your MPACT account has been approved.\n\n'
                f'Sign in with this address at {config.SITE_URL or "the MPACT site"}: '
                'you will be emailed a code each time, no password needed.\n'))
        except mailer.MailError:
            pass        # approved either way; the person can still sign in
    return account


@router.post('/allowlist')
def add_allowlist(payload: AllowlistEntry, admin=Depends(require_admin)):
    """Make an address (or ``@domain``) internal, before or after it signs up."""
    try:
        entry = accounts.add_to_allowlist(payload.entry)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {'entry': entry, 'allowlist': accounts.allowlist()}


@router.delete('/allowlist')
def remove_allowlist(entry: str = Query(..., max_length=320), admin=Depends(require_admin)):
    try:
        removed = accounts.remove_from_allowlist(entry)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {'entry': removed, 'allowlist': accounts.allowlist()}
