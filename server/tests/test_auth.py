"""Authentication.

The API is the whole application: uploading a peak table and starting an
analysis both spend real CPU and disk, so "is this endpoint reachable without
a login" is the question these tests exist to answer, endpoint by endpoint
rather than by inspecting the middleware.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_client(tmp_path, **env):
    """A TestClient with a fresh data root and the given auth configuration."""
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app

    os.environ['MPACT_DATA_ROOT'] = str(tmp_path)
    os.environ.pop('MPACT_SECRET_KEY', None)
    for key in ('MPACT_AUTH', 'MPACT_ACCESS_CODE', 'MPACT_USERS_FILE'):
        os.environ.pop(key, None)
    os.environ.update(env)
    config.reload()
    return TestClient(app)


@pytest.fixture
def code_client(tmp_path):
    with make_client(tmp_path, MPACT_AUTH='code',
                     MPACT_ACCESS_CODE='open-sesame-2026') as client:
        yield client


@pytest.fixture
def user_client(tmp_path):
    from app import auth

    users_file = tmp_path / 'users.txt'
    with make_client(tmp_path, MPACT_AUTH='users',
                     MPACT_USERS_FILE=str(users_file)) as client:
        auth.save_users({'rsamples': auth.hash_password('a-good-long-password'),
                         'student': auth.hash_password('another-long-one')})
        yield client


@pytest.fixture(autouse=True)
def _restore_environment():
    """Put every MPACT_* variable back after each test.

    These tests are the only ones that turn authentication on, and the
    settings are process-wide: leaving MPACT_AUTH set would make every other
    test module's requests 401. Relying on the last test in the file happening
    to clear it is not isolation, it is luck with a running clock.
    """
    from app import config

    before = {k: v for k, v in os.environ.items() if k.startswith('MPACT_')}
    yield
    for key in [k for k in os.environ if k.startswith('MPACT_')]:
        del os.environ[key]
    os.environ.update(before)
    config.reload()


@pytest.fixture(autouse=True)
def _fresh_throttle():
    """The sign-in throttle is process-wide and keyed by address, and every
    test here knocks from the same one -- so the throttling test would lock
    out the tests after it."""
    from app.routers import authroutes

    authroutes._ATTEMPTS.clear()
    yield
    authroutes._ATTEMPTS.clear()


@pytest.fixture
def open_client(tmp_path):
    with make_client(tmp_path) as client:
        yield client


# --- password hashing -------------------------------------------------------

def test_a_password_hash_is_salted_and_verifies():
    from app import auth

    first = auth.hash_password('correct horse battery staple')
    second = auth.hash_password('correct horse battery staple')

    assert first != second, 'two hashes of one password are identical: no salt'
    assert first.startswith('scrypt$')
    assert auth.verify_password('correct horse battery staple', first)
    assert not auth.verify_password('Correct horse battery staple', first)
    assert not auth.verify_password('', first)


def test_a_damaged_hash_never_verifies():
    from app import auth

    for broken in ['', 'not-a-hash', 'scrypt$x$8$1$aa$bb', 'md5$1$1$1$aa$bb']:
        assert not auth.verify_password('anything', broken)


# --- tokens -----------------------------------------------------------------

def test_a_token_round_trips_but_cannot_be_forged(tmp_path):
    from app import auth, config

    os.environ['MPACT_DATA_ROOT'] = str(tmp_path)
    os.environ.pop('MPACT_SECRET_KEY', None)
    config.reload()

    token = auth.issue_token('rsamples')
    assert auth.read_token(token) == 'rsamples'

    payload, _, signature = token.partition('.')
    assert auth.read_token(f'{payload}.{"A" * len(signature)}') is None, 'bad signature accepted'
    assert auth.read_token('') is None
    assert auth.read_token('garbage') is None

    # A token whose payload says someone else, signed with the same signature.
    other = auth.issue_token('someone-else')
    assert auth.read_token(f'{other.partition(".")[0]}.{signature}') is None


def test_an_expired_token_is_refused(tmp_path):
    from app import auth, config

    os.environ['MPACT_DATA_ROOT'] = str(tmp_path)
    config.reload()
    assert auth.read_token(auth.issue_token('rsamples', ttl_seconds=-1)) is None


# --- the gate ---------------------------------------------------------------

PROTECTED = [
    ('get', '/api/files'),
    ('get', '/api/runs'),
    ('get', '/api/runs/defaults'),
    ('post', '/api/files/demo'),
    ('post', '/api/files/groups'),
    ('post', '/api/runs'),
]


@pytest.mark.parametrize('method, path', PROTECTED)
def test_every_api_route_needs_a_login(code_client, method, path):
    response = getattr(code_client, method)(path, **({'json': {}} if method == 'post' else {}))
    assert response.status_code == 401, f'{method.upper()} {path} answered without a login'


def test_health_and_login_stay_reachable(code_client):
    """A container's health probe has no cookies, and the login form has to
    load before anyone can sign in."""
    assert code_client.get('/api/health').status_code == 200
    assert code_client.get('/api/auth/status').status_code == 200


def test_the_front_end_loads_so_the_login_form_can_be_shown(code_client):
    assert code_client.get('/').status_code == 200


def test_shared_code_status_asks_for_one_box(code_client):
    assert code_client.get('/api/auth/status').json() == {
        'mode': 'code', 'required': True, 'authenticated': False,
        'user': None, 'needs_username': False}


def test_named_account_status_asks_for_two(user_client):
    # One client per test: both fixtures configure the same process-wide
    # settings, so a test holding two of them would only see the last one.
    named = user_client.get('/api/auth/status').json()
    assert named['mode'] == 'users'
    assert named['needs_username'] is True


# --- signing in -------------------------------------------------------------

def test_the_shared_code_signs_in_and_opens_the_api(code_client):
    assert code_client.post('/api/auth/login',
                            json={'password': 'wrong'}).status_code == 401
    assert code_client.get('/api/files').status_code == 401

    ok = code_client.post('/api/auth/login', json={'password': 'open-sesame-2026'})
    assert ok.status_code == 200
    assert ok.json()['user'] == 'shared'

    assert code_client.get('/api/files').status_code == 200
    assert code_client.get('/api/auth/status').json()['authenticated'] is True


def test_a_named_account_signs_in(user_client):
    assert user_client.post('/api/auth/login',
                            json={'username': 'rsamples',
                                  'password': 'wrong'}).status_code == 401
    assert user_client.post('/api/auth/login',
                            json={'username': 'nobody',
                                  'password': 'a-good-long-password'}).status_code == 401

    ok = user_client.post('/api/auth/login',
                          json={'username': 'rsamples', 'password': 'a-good-long-password'})
    assert ok.status_code == 200
    assert ok.json()['user'] == 'rsamples'
    assert user_client.get('/api/files').status_code == 200


def test_a_wrong_password_does_not_reveal_whether_the_account_exists(user_client):
    known = user_client.post('/api/auth/login',
                             json={'username': 'rsamples', 'password': 'wrong'})
    unknown = user_client.post('/api/auth/login',
                               json={'username': 'ghost', 'password': 'wrong'})
    assert known.json()['detail'] == unknown.json()['detail']


def test_signing_out_closes_the_api_again(code_client):
    code_client.post('/api/auth/login', json={'password': 'open-sesame-2026'})
    assert code_client.get('/api/files').status_code == 200

    code_client.post('/api/auth/logout')
    assert code_client.get('/api/files').status_code == 401


def test_repeated_failures_are_throttled(code_client):
    codes = [code_client.post('/api/auth/login', json={'password': f'no-{i}'}).status_code
             for i in range(12)]
    assert 429 in codes, 'an unlimited number of guesses is allowed'
    assert codes[0] == 401, 'the throttle fired before any real attempt'


# --- workspaces -------------------------------------------------------------

def test_each_account_gets_its_own_workspace(user_client):
    """Two people on one server must not see each other's uploads."""
    user_client.post('/api/auth/login',
                     json={'username': 'rsamples', 'password': 'a-good-long-password'})
    mine = user_client.post('/api/files/demo').json()
    assert len(mine) == 4
    assert len(user_client.get('/api/files').json()) == 4

    user_client.post('/api/auth/logout')
    user_client.cookies.clear()
    user_client.post('/api/auth/login',
                     json={'username': 'student', 'password': 'another-long-one'})
    theirs = user_client.get('/api/files').json()
    assert theirs == [], "a second account saw the first library"


def test_a_workspace_follows_the_account_not_the_browser(user_client):
    """Signing in from another machine has to find your own data."""
    user_client.post('/api/auth/login',
                     json={'username': 'rsamples', 'password': 'a-good-long-password'})
    user_client.post('/api/files/demo')
    first = {record['id'] for record in user_client.get('/api/files').json()}

    # A different browser: no cookies at all, then the same login.
    user_client.cookies.clear()
    user_client.post('/api/auth/login',
                     json={'username': 'rsamples', 'password': 'a-good-long-password'})
    second = {record['id'] for record in user_client.get('/api/files').json()}
    assert second == first and first, 'the library did not follow the account'


# --- the default stays open -------------------------------------------------

def test_without_configuration_there_is_no_login(open_client):
    """`none` is the default: a laptop running this for itself should not have
    to sign in, and the rest of the test suite depends on it."""
    assert open_client.get('/api/auth/status').json() == {
        'mode': 'none', 'required': False, 'authenticated': True,
        'user': None, 'needs_username': False}
    assert open_client.get('/api/files').status_code == 200


# --- behind a reverse proxy -------------------------------------------------

def _fail_login(client, headers=None):
    return client.post('/api/auth/login', json={'password': 'wrong'},
                       headers=headers or {}).status_code


def test_forwarded_addresses_are_ignored_unless_the_proxy_is_trusted(tmp_path):
    """X-Forwarded-For is written by whoever is talking to us.

    Honouring it on a directly reachable server would let one client rotate
    the header and never be throttled, so it takes an explicit setting.
    """
    with make_client(tmp_path, MPACT_AUTH='code',
                     MPACT_ACCESS_CODE='open-sesame-2026') as client:
        codes = [_fail_login(client, {'x-forwarded-for': f'10.0.0.{i}'})
                 for i in range(12)]
    assert 429 in codes, 'rotating the header evaded the throttle'


def test_a_trusted_proxy_throttles_each_caller_separately(tmp_path):
    """Behind Caddy every request has the same socket address, so throttling
    on it would let one person's typo lock out the room."""
    with make_client(tmp_path, MPACT_AUTH='code',
                     MPACT_ACCESS_CODE='open-sesame-2026',
                     MPACT_TRUST_PROXY='1') as client:
        exhausted = [_fail_login(client, {'x-forwarded-for': '203.0.113.7'})
                     for _ in range(12)]
        assert 429 in exhausted, 'one caller was never throttled'

        # Someone else, arriving through the same proxy, is unaffected.
        assert _fail_login(client, {'x-forwarded-for': '203.0.113.8'}) == 401


def test_a_trusted_proxy_reads_only_the_first_forwarded_hop(tmp_path):
    """A client can append to X-Forwarded-For; the leftmost entry is the one
    the proxy itself wrote."""
    from app import config
    from app.routers import authroutes

    class FakeRequest:
        def __init__(self, header):
            self.headers = {'x-forwarded-for': header}
            self.client = type('c', (), {'host': '127.0.0.1'})()

    with make_client(tmp_path, MPACT_AUTH='code', MPACT_ACCESS_CODE='x',
                     MPACT_TRUST_PROXY='1'):
        assert config.TRUST_PROXY is True
        assert authroutes._client_address(
            FakeRequest('203.0.113.7, 10.0.0.1')) == '203.0.113.7'
