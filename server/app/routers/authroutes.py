"""Login, logout and "do I need to log in?".

Deliberately three small routes. Everything that decides *whether* a request
is allowed lives in the middleware in ``main.py``, so a route added later is
protected by default rather than by remembering to add a dependency.
"""

import time

from fastapi import APIRouter, HTTPException, Request, Response

from .. import accounts, auth, config, mailer
from ..schemas import EmailStart, EmailVerify, LoginRequest

router = APIRouter(prefix='/api/auth', tags=['auth'])

# A crude per-address throttle. Not a substitute for a real rate limiter in
# front of the app, but enough that an open form is not an oracle you can
# query thousands of times a second.
_ATTEMPTS = {}
_MAX_ATTEMPTS = 8
_WINDOW_SECONDS = 60


def _client_address(request: Request) -> str:
    """Who is knocking, as well as we can tell.

    Behind a reverse proxy every request comes from the proxy, so throttling
    on the socket address would let one person's typo lock out the room. The
    forwarded address is used only when the deployment declares it is behind
    a proxy: the header is client-supplied, so trusting it unconditionally
    would let an attacker rotate it and never be throttled at all.
    """
    if config.TRUST_PROXY:
        forwarded = request.headers.get('x-forwarded-for', '')
        first = forwarded.split(',')[0].strip()
        if first:
            return first
    return request.client.host if request.client else 'unknown'


def _throttle(request: Request, table=None, limit=_MAX_ATTEMPTS, window=_WINDOW_SECONDS,
              message='too many sign-in attempts; wait a minute and try again'):
    table = _ATTEMPTS if table is None else table
    who = _client_address(request)
    now = time.time()
    tries = [stamp for stamp in table.get(who, []) if now - stamp < window]
    if len(tries) >= limit:
        raise HTTPException(status_code=429, detail=message)
    tries.append(now)
    table[who] = tries
    # Keep the table from growing without bound on a long-lived server.
    if len(table) > 4096:
        for key in [k for k, v in table.items() if not v or now - v[-1] > window]:
            table.pop(key, None)


# Codes asked for, per address the request came from: each one is an email
# sent from your account, so this is kept well below anything a person needs.
_CODE_REQUESTS = {}


def _clear_throttle(request: Request):
    _ATTEMPTS.pop(_client_address(request), None)


@router.get('/status')
def status(request: Request):
    """What the front end needs to decide between the app and a login form."""
    account = getattr(request.state, 'account', None)
    body = {
        'mode': auth.mode(),
        'required': auth.required(),
        'authenticated': bool(getattr(request.state, 'user', None)) or not auth.required(),
        'user': getattr(request.state, 'user', None),
        # 'code' logins have no username, so the form shows one box.
        'needs_username': auth.mode() == 'users',
    }
    if account is not None:
        body.update(role=account['role'], admin=account['admin'])
    return body


# --- email sign-in ----------------------------------------------------------

def _email_mode():
    if auth.mode() != 'email':
        raise HTTPException(status_code=404, detail='this server does not sign in by email')


def _site():
    return config.SITE_URL or 'MPACT'


@router.post('/email/start')
def email_start(payload: EmailStart, request: Request):
    """Send a sign-in code to an address. The answer is the same whether or
    not the address has an account (or may have one), so this cannot be used
    to find out who does."""
    _email_mode()
    try:
        email = accounts.normalise(payload.email)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _throttle(request, _CODE_REQUESTS, limit=10, window=600,
              message='too many codes asked for from here; wait a few minutes')
    try:
        code = accounts.issue_code(email)
    except accounts.RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    if code is not None:
        minutes = accounts.CODE_TTL // 60
        try:
            mailer.send(email, f'{code} is your MPACT sign-in code', (
                f'Your MPACT sign-in code is:\n\n    {code[:3]} {code[3:]}\n\n'
                f'It works once, for the next {minutes} minutes. Enter it on the page '
                'where you asked for it.\n\n'
                'If you did not ask to sign in to MPACT, ignore this email: nobody can '
                'sign in without the code.\n\n'
                f'-- MPACT, {_site()}\n'))
        except mailer.MailError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {'sent': True, 'email': email, 'expires_in': accounts.CODE_TTL}


@router.post('/email/verify')
def email_verify(payload: EmailVerify, request: Request, response: Response):
    """Sign in with a code. The first sign-in creates the account."""
    _email_mode()
    try:
        email = accounts.normalise(payload.email)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _throttle(request)
    try:
        account, created = accounts.verify_code(email, payload.code)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _clear_throttle(request)

    if account['status'] != 'active':
        if created:
            _tell_admins_about(account)
        return {'authenticated': False, 'pending': True, 'user': email,
                'detail': 'Thanks for signing up. Your account is waiting for approval; '
                          'you will get an email when you can sign in.'}

    response.set_cookie(
        auth.AUTH_COOKIE, auth.issue_token(email),
        max_age=config.AUTH_MAX_AGE_SECONDS,
        httponly=True,
        samesite='lax',
        secure=config.SESSION_COOKIE_SECURE,
    )
    return {'authenticated': True, 'user': email, 'mode': 'email', 'role': account['role'],
            'created': created}


def _tell_admins_about(account):
    """Best effort: mail each administrator that an account awaits approval."""
    for admin in sorted(accounts.admin_emails()):
        try:
            mailer.send(admin, f'MPACT: {account["email"]} is waiting for approval', (
                f'{account["email"]} has signed up for MPACT and is waiting for approval.\n\n'
                f'Approve or block them on the Admin page: {_site()}\n'))
        except mailer.MailError:
            pass


@router.post('/login')
def login(payload: LoginRequest, request: Request, response: Response):
    if not auth.required():
        return {'authenticated': True, 'user': None, 'mode': 'none'}

    _throttle(request)
    try:
        user = auth.check_login(payload.username, payload.password)
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    _clear_throttle(request)
    response.set_cookie(
        auth.AUTH_COOKIE, auth.issue_token(user),
        max_age=config.AUTH_MAX_AGE_SECONDS,
        httponly=True,
        samesite='lax',
        secure=config.SESSION_COOKIE_SECURE,
    )
    return {'authenticated': True, 'user': user, 'mode': auth.mode()}


@router.post('/logout')
def logout(response: Response):
    response.delete_cookie(auth.AUTH_COOKIE, samesite='lax',
                           secure=config.SESSION_COOKIE_SECURE)
    return {'authenticated': False}
