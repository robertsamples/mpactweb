"""Login, logout and "do I need to log in?".

Deliberately three small routes. Everything that decides *whether* a request
is allowed lives in the middleware in ``main.py``, so a route added later is
protected by default rather than by remembering to add a dependency.
"""

import time

from fastapi import APIRouter, HTTPException, Request, Response

from .. import auth, config
from ..schemas import LoginRequest

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


def _throttle(request: Request):
    who = _client_address(request)
    now = time.time()
    tries = [stamp for stamp in _ATTEMPTS.get(who, []) if now - stamp < _WINDOW_SECONDS]
    if len(tries) >= _MAX_ATTEMPTS:
        raise HTTPException(status_code=429,
                            detail='too many sign-in attempts; wait a minute and try again')
    tries.append(now)
    _ATTEMPTS[who] = tries
    # Keep the table from growing without bound on a long-lived server.
    if len(_ATTEMPTS) > 4096:
        for key in [k for k, v in _ATTEMPTS.items() if not v or now - v[-1] > _WINDOW_SECONDS]:
            _ATTEMPTS.pop(key, None)


def _clear_throttle(request: Request):
    _ATTEMPTS.pop(_client_address(request), None)


@router.get('/status')
def status(request: Request):
    """What the front end needs to decide between the app and a login form."""
    return {
        'mode': auth.mode(),
        'required': auth.required(),
        'authenticated': bool(getattr(request.state, 'user', None)) or not auth.required(),
        'user': getattr(request.state, 'user', None),
        # 'code' logins have no username, so the form shows one box.
        'needs_username': auth.mode() == 'users',
    }


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
