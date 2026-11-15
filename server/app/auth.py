"""Authentication, for deployments that are reachable by more than their owner.

The desktop application has no notion of a user: it runs on your machine and
sees your files. On a server that assumption stops holding the moment the
port is open, because uploading a peak table and starting an analysis both
cost real CPU and disk. So this module exists to put a door in front of the
API, in three modes:

``none``   (default) no login at all -- correct for ``localhost`` and for the
           test suite, wrong for anything with a public address.
``code``   one shared access code for everybody. What a teaching lab wants:
           tell the room one code, everyone gets their own workspace.
``users``  named accounts from a password file, each with its own persistent
           workspace that follows them between browsers and machines.

Passwords are hashed with :func:`hashlib.scrypt` -- memory-hard, in the
standard library, no dependency to audit. The login cookie is a value signed
with HMAC-SHA256 under a server secret, so it carries who you are and when it
expires without the server keeping any session table.

What this deliberately is *not* is an identity provider. If you need
institutional single sign-on, put an authenticating reverse proxy in front
(Caddy's ``forward_auth``, oauth2-proxy, Shibboleth) and run this in ``none``
mode on a loopback address, so the proxy is the only way in.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path

from . import config

# Cookie holding the signed login token. Distinct from the session cookie:
# one says who you are, the other says which workspace you are looking at.
AUTH_COOKIE = 'mpact_auth'

MODES = ('none', 'code', 'users')

# scrypt work factors. n=2**14 with r=8 is the usual interactive setting: a
# verification costs ~100 ms and ~16 MB, which is nothing for a login and a
# great deal for someone working through a word list.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024

USERNAME_RE = re.compile(r'^[A-Za-z0-9._@-]{1,64}$')


class AuthError(Exception):
    """A login could not be completed."""


# --- password hashing -------------------------------------------------------

def hash_password(password: str) -> str:
    """A self-describing scrypt hash: ``scrypt$n$r$p$salt$key``."""
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=_SCRYPT_N,
                         r=_SCRYPT_R, p=_SCRYPT_P, maxmem=_SCRYPT_MAXMEM)
    return '$'.join(['scrypt', str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P),
                     salt.hex(), key.hex()])


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash, in constant time."""
    try:
        scheme, n, r, p, salt_hex, key_hex = stored.strip().split('$')
        if scheme != 'scrypt':
            return False
        key = hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt_hex),
                             n=int(n), r=int(r), p=int(p), maxmem=_SCRYPT_MAXMEM)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(key.hex(), key_hex)


# --- the password file ------------------------------------------------------

def users_path() -> Path:
    return Path(config.USERS_FILE)


def load_users() -> dict:
    """``{username: hash}`` from the password file, or ``{}`` if there is none.

    Blank lines and ``#`` comments are ignored, so the file can be annotated
    with who each account belongs to.
    """
    path = users_path()
    if not path.is_file():
        return {}
    users = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, _, stored = line.partition(':')
        if name and stored:
            users[name.strip()] = stored.strip()
    return users


def save_users(users: dict) -> None:
    path = users_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ['# MPACT accounts: username:scrypt-hash, one per line.',
            '# Manage with scripts/mpact_users.py -- do not edit hashes by hand.']
    body += [f'{name}:{stored}' for name, stored in sorted(users.items())]
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text('\n'.join(body) + '\n', encoding='utf-8')
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:      # pragma: no cover - Windows and some shares
        pass


# --- the signing secret -----------------------------------------------------

def secret_key() -> bytes:
    """The HMAC key for login cookies.

    Taken from ``MPACT_SECRET_KEY`` when set. Otherwise generated once and
    kept in the data root, so restarting the server does not sign everyone
    out -- and so a deployment that never sets the variable still gets a
    strong key rather than a hardcoded one.
    """
    configured = os.environ.get('MPACT_SECRET_KEY')
    if configured:
        return configured.encode('utf-8')

    # Every authenticated request verifies its cookie, so the key is read
    # from disk once per data root rather than once per request.
    path = Path(config.DATA_ROOT) / 'secret.key'
    cached = _SECRET_KEYS.get(str(path))
    if cached:
        return cached

    if path.is_file():
        key = path.read_bytes().strip()
        if key:
            _SECRET_KEYS[str(path)] = key
            return key

    # Hex text, not raw bytes: the read above strips whitespace, and a raw
    # key that happened to start or end with a whitespace byte (about 1 in 20)
    # would sign the first login with a different key than every later check.
    key = secrets.token_hex(32).encode('ascii')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:      # pragma: no cover
        pass
    _SECRET_KEYS[str(path)] = key
    return key


_SECRET_KEYS = {}


def forget_secret_key():
    """Drop the cached signing key, so the next request reads it again."""
    _SECRET_KEYS.clear()


# --- tokens -----------------------------------------------------------------

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def issue_token(user: str, ttl_seconds: int = None) -> str:
    """A signed ``user + expiry`` cookie value."""
    ttl = config.AUTH_MAX_AGE_SECONDS if ttl_seconds is None else ttl_seconds
    payload = _b64(json.dumps({'u': user, 'e': int(time.time()) + ttl},
                              separators=(',', ':')).encode('utf-8'))
    signature = hmac.new(secret_key(), payload.encode('ascii'), hashlib.sha256).digest()
    return f'{payload}.{_b64(signature)}'


def read_token(token: str):
    """The username inside a valid, unexpired token, else ``None``."""
    if not token or '.' not in token:
        return None
    payload, _, signature = token.partition('.')
    expected = hmac.new(secret_key(), payload.encode('ascii'), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(_unb64(signature), expected):
            return None
        data = json.loads(_unb64(payload))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get('e', 0) < time.time():
        return None
    user = data.get('u')
    return user if isinstance(user, str) and user else None


# --- login ------------------------------------------------------------------

def mode() -> str:
    value = str(config.AUTH_MODE or 'none').strip().lower()
    return value if value in MODES else 'none'


def required() -> bool:
    return mode() != 'none'


def check_login(username: str, password: str) -> str:
    """Validate credentials and return the identity to sign in as.

    Raises :class:`AuthError` with a message meant for the person typing --
    but never one that distinguishes "no such user" from "wrong password",
    which would turn the login form into a list of who has an account.
    """
    current = mode()
    if current == 'none':
        raise AuthError('this server does not require a login')

    if current == 'code':
        # Comma-separated, so one server can hand different groups (a class,
        # a lab) their own code. Case-insensitive: these are typed off a slide.
        codes = [c.strip().lower() for c in str(config.ACCESS_CODE or '').split(',')
                 if c.strip()]
        if not codes:
            raise AuthError('this server is set to code login but has no access '
                            'code configured; set MPACT_ACCESS_CODE')
        given = (password or '').strip().lower()
        # Compare against every code, so the answer takes the same time
        # whichever one (if any) matched.
        matched = [hmac.compare_digest(given.encode('utf-8'), code.encode('utf-8'))
                   for code in codes]
        if not any(matched):
            raise AuthError('that access code is not correct')
        return 'shared'

    users = load_users()
    if not users:
        raise AuthError('no accounts exist on this server yet; create one with '
                        'scripts/mpact_users.py')
    name = (username or '').strip()
    stored = users.get(name)
    # Hash even when the user is unknown, so a missing account and a wrong
    # password take the same time to answer.
    reference = stored or hash_password(secrets.token_hex(16))
    if not verify_password(password or '', reference) or stored is None:
        raise AuthError('that username and password do not match')
    return name


def workspace_id(user: str) -> str:
    """The session id a named account always lands in.

    Derived from the username under the server secret, so a user's uploads and
    runs follow them to another browser or machine, and one user cannot guess
    another's workspace. Shaped like every other session id so the rest of the
    storage layer needs no special case.
    """
    digest = hmac.new(secret_key(), f'workspace:{user}'.encode('utf-8'),
                      hashlib.sha256).hexdigest()
    return f's_{digest[:16]}'
