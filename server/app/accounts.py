"""Email accounts: sign-in by a one-time code sent to the address.

There are no passwords. Someone types their email address, the server sends
a six-digit code to it, and entering the code signs them in (the same signed
cookie every other mode uses, carrying the address). Signing in for the first
time is signing up; "forgot my password" does not arise.

Each account has its own workspace, and a role:

``internal``  an address at one of ``MPACT_INTERNAL_DOMAINS`` (smith.edu by
              default) or on the allowlist an administrator keeps. Sees the
              internal datasets, and has no quota.
``external``  everyone else. Sees only the public demo and their own work,
              within a quota.
``blocked``   set by an administrator. Cannot sign in.

An administrator can override the role of any account. Nothing is recorded
about where people are beyond the domain of their address.

The codes are stored as keyed hashes, never as themselves; they expire after
``CODE_TTL`` seconds, allow ``CODE_ATTEMPTS`` guesses, and an address can be
sent only so many in an hour.
"""

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import config

ROLES = ('internal', 'external', 'blocked')
CODE_TTL = 15 * 60
CODE_ATTEMPTS = 5
CODES_PER_HOUR = 5

# Deliberately loose: the real test of an address is that the code arrives.
_EMAIL = re.compile(r'^[^@\s"<>,;]{1,64}@([A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63}$')

_ENSURED = set()
_GUARD = threading.Lock()


class AccountError(Exception):
    """Something the person signing in needs to be told."""


class RateLimited(AccountError):
    """Too many codes asked for."""


# --- storage ----------------------------------------------------------------

def db_path():
    return Path(config.DATA_ROOT) / 'accounts.db'


@contextmanager
def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        with _GUARD:
            if str(path) not in _ENSURED:
                conn.executescript('''
                    PRAGMA journal_mode = WAL;
                    CREATE TABLE IF NOT EXISTS accounts (
                        email TEXT PRIMARY KEY,
                        domain TEXT NOT NULL,
                        workspace TEXT NOT NULL UNIQUE,
                        role_override TEXT,
                        status TEXT NOT NULL DEFAULT 'active',
                        created REAL NOT NULL,
                        last_seen REAL
                    );
                    CREATE TABLE IF NOT EXISTS codes (
                        email TEXT NOT NULL,
                        code_hash TEXT NOT NULL,
                        created REAL NOT NULL,
                        expires REAL NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE INDEX IF NOT EXISTS codes_email ON codes (email);
                    CREATE TABLE IF NOT EXISTS allowlist (
                        entry TEXT PRIMARY KEY,
                        added REAL NOT NULL
                    );
                ''')
                _ENSURED.add(str(path))
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction():
    """One write at a time, start to finish (BEGIN IMMEDIATE takes the
    write lock up front, so two sign-ins cannot both create an account)."""
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:
            yield conn
            conn.execute('COMMIT')
        except BaseException:
            conn.execute('ROLLBACK')
            raise


# --- addresses and roles ------------------------------------------------------

def normalise(email):
    """An address as stored: trimmed and lower-cased. ``AccountError`` if it
    does not look like an address at all."""
    email = str(email or '').strip().lower()
    if len(email) > 254 or not _EMAIL.match(email):
        raise AccountError('that does not look like an email address')
    return email


def domain_of(email):
    return email.rsplit('@', 1)[-1]


def _domain_matches(domain, wanted):
    """``science.smith.edu`` is at ``smith.edu``; ``notsmith.edu`` is not."""
    return domain == wanted or domain.endswith('.' + wanted)


def internal_domains():
    return [d.strip().lower().lstrip('@') for d in str(config.INTERNAL_DOMAINS or '').split(',')
            if d.strip()]


def admin_emails():
    return {e.strip().lower() for e in str(config.ADMIN_EMAILS or '').split(',') if e.strip()}


def allowlist(conn=None):
    """Allowlist entries: addresses, and ``@domain`` for a whole domain."""
    if conn is None:
        with connect() as own:
            return allowlist(own)
    return [row['entry'] for row in conn.execute('SELECT entry FROM allowlist ORDER BY entry')]


def automatic_role(email, entries=None):
    """The role an address gets unless an administrator says otherwise."""
    entries = allowlist() if entries is None else entries
    domain = domain_of(email)
    if email in entries:
        return 'internal'
    for entry in entries:
        if entry.startswith('@') and _domain_matches(domain, entry[1:]):
            return 'internal'
    if any(_domain_matches(domain, wanted) for wanted in internal_domains()):
        return 'internal'
    return 'external'


def describe(row, entries=None):
    """An account row as a plain dict, with its effective role."""
    account = dict(row)
    account['automatic_role'] = automatic_role(account['email'], entries)
    account['role'] = account['role_override'] or account['automatic_role']
    account['admin'] = account['email'] in admin_emails()
    return account


# A request carries only the address; its account is looked up once and kept
# a few seconds, so a page load's dozen requests are one database read.
_CACHE = {}
_CACHE_SECONDS = 5.0


def lookup(email):
    """The account for a signed-in address, or None."""
    now = time.monotonic()
    hit = _CACHE.get(email)
    if hit and now - hit[0] < _CACHE_SECONDS:
        return hit[1]
    with connect() as conn:
        row = conn.execute('SELECT * FROM accounts WHERE email = ?', (email,)).fetchone()
        account = describe(row, allowlist(conn)) if row else None
    if len(_CACHE) > 2048:
        _CACHE.clear()
    _CACHE[email] = (now, account)
    return account


def forget(email=None):
    """Drop cached accounts (after an administrator changes one)."""
    if email is None:
        _CACHE.clear()
    else:
        _CACHE.pop(email, None)


_SEEN = {}


def touch(email):
    """Record that an account was used (at most every few minutes)."""
    now = time.time()
    if now - _SEEN.get(email, 0) < 300:
        return
    _SEEN[email] = now
    try:
        with connect() as conn:
            conn.execute('UPDATE accounts SET last_seen = ? WHERE email = ?', (now, email))
    except sqlite3.Error:
        pass                    # bookkeeping only


# --- codes --------------------------------------------------------------------

def _hash(email, code):
    from .auth import secret_key
    return hmac.new(secret_key(), f'code:{email}:{code}'.encode(), hashlib.sha256).hexdigest()


def issue_code(email):
    """A new sign-in code for ``email``, or None when none should be sent
    (a blocked account). The caller says the same thing either way, so the
    response never tells anyone which addresses have accounts.

    Raises ``RateLimited`` when this address has asked too often.
    """
    now = time.time()
    with transaction() as conn:
        row = conn.execute('SELECT role_override FROM accounts WHERE email = ?',
                           (email,)).fetchone()
        if row and row['role_override'] == 'blocked':
            return None
        recent = conn.execute('SELECT COUNT(*) FROM codes WHERE email = ? AND created > ?',
                              (email, now - 3600)).fetchone()[0]
        if recent >= CODES_PER_HOUR:
            raise RateLimited('too many codes have been sent to this address; '
                              'try again in an hour')
        code = f'{secrets.randbelow(10 ** 6):06d}'
        # Only the newest code works, and spent or stale ones are cleared.
        conn.execute('UPDATE codes SET attempts = ? WHERE email = ?', (CODE_ATTEMPTS, email))
        conn.execute('DELETE FROM codes WHERE created < ?', (now - 3600,))
        conn.execute('INSERT INTO codes (email, code_hash, created, expires) VALUES (?, ?, ?, ?)',
                     (email, _hash(email, code), now, now + CODE_TTL))
    return code


def verify_code(email, code):
    """Check a code and sign the address in, creating its account the first
    time. Returns ``(account, created)``.

    Raises ``AccountError`` with a message for the person typing.
    """
    code = re.sub(r'\D', '', str(code or ''))
    now = time.time()
    # A wrong guess has to be counted even though the sign-in fails, so the
    # count is committed first and the error raised after.
    wrong = None
    with transaction() as conn:
        row = conn.execute(
            'SELECT rowid, * FROM codes WHERE email = ? ORDER BY created DESC LIMIT 1',
            (email,)).fetchone()
        if row is None or row['expires'] < now or row['attempts'] >= CODE_ATTEMPTS:
            raise AccountError('that code has expired or been used up; ask for a new one')
        if len(code) != 6 or not hmac.compare_digest(row['code_hash'], _hash(email, code)):
            conn.execute('UPDATE codes SET attempts = attempts + 1 WHERE rowid = ?', (row['rowid'],))
            wrong = CODE_ATTEMPTS - row['attempts'] - 1
    if wrong is not None:
        raise AccountError('that code is not right'
                           + (f' ({wrong} tries left)' if wrong > 0 else '; ask for a new one'))
    with transaction() as conn:
        row = conn.execute(
            'SELECT rowid, * FROM codes WHERE email = ? ORDER BY created DESC LIMIT 1',
            (email,)).fetchone()
        # Checked again under the write lock: two requests with the same right
        # code must not both get in.
        if (row is None or row['expires'] < now or row['attempts'] >= CODE_ATTEMPTS
                or not hmac.compare_digest(row['code_hash'], _hash(email, code))):
            raise AccountError('that code has expired or been used up; ask for a new one')
        conn.execute('DELETE FROM codes WHERE email = ?', (email,))

        account = conn.execute('SELECT * FROM accounts WHERE email = ?', (email,)).fetchone()
        created = account is None
        if created:
            entries = allowlist(conn)
            pending = (str(config.SIGNUP).lower() == 'approval'
                       and automatic_role(email, entries) == 'external')
            conn.execute(
                'INSERT INTO accounts (email, domain, workspace, status, created, last_seen) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (email, domain_of(email), 's_' + secrets.token_hex(8),
                 'pending' if pending else 'active', now, now))
            account = conn.execute('SELECT * FROM accounts WHERE email = ?', (email,)).fetchone()
        elif account['role_override'] == 'blocked':
            raise AccountError('this account cannot sign in')
        else:
            conn.execute('UPDATE accounts SET last_seen = ? WHERE email = ?', (now, email))
        result = describe(account, allowlist(conn))
    forget(email)
    return result, created


# --- administration -----------------------------------------------------------

def list_accounts():
    with connect() as conn:
        entries = allowlist(conn)
        return [describe(row, entries)
                for row in conn.execute('SELECT * FROM accounts ORDER BY created DESC')]


def update_account(email, role=..., status=None):
    """Set an account's role override (None: back to automatic) and/or its
    status. Returns the account, or None if there is no such account."""
    if role is not ... and role is not None and role not in ROLES:
        raise AccountError(f'role must be one of {", ".join(ROLES)}')
    if status is not None and status not in ('active', 'pending'):
        raise AccountError('status must be active or pending')
    with transaction() as conn:
        if conn.execute('SELECT 1 FROM accounts WHERE email = ?', (email,)).fetchone() is None:
            return None
        if role is not ...:
            conn.execute('UPDATE accounts SET role_override = ? WHERE email = ?', (role, email))
        if status is not None:
            conn.execute('UPDATE accounts SET status = ? WHERE email = ?', (status, email))
        row = conn.execute('SELECT * FROM accounts WHERE email = ?', (email,)).fetchone()
        result = describe(row, allowlist(conn))
    forget(email)
    return result


def normalise_entry(entry):
    """An allowlist entry: an address, or ``@domain``."""
    entry = str(entry or '').strip().lower()
    if entry.startswith('@'):
        domain = entry[1:]
        if not re.fullmatch(r'([a-z0-9-]{1,63}\.)+[a-z]{2,63}', domain):
            raise AccountError('that does not look like a domain')
        return '@' + domain
    return normalise(entry)


def add_to_allowlist(entry):
    entry = normalise_entry(entry)
    with connect() as conn:
        conn.execute('INSERT OR IGNORE INTO allowlist (entry, added) VALUES (?, ?)',
                     (entry, time.time()))
    forget()
    return entry


def remove_from_allowlist(entry):
    entry = normalise_entry(entry)
    with connect() as conn:
        conn.execute('DELETE FROM allowlist WHERE entry = ?', (entry,))
    forget()
    return entry
