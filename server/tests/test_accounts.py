"""Email accounts: sign-in by emailed code, roles, approval, quotas.

Mail goes to an outbox file instead of a mail server (MPACT_MAIL_OUTBOX), and
each test reads its code from there exactly as a person would read it from
their inbox.
"""

import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN = 'boss@smith.edu'


@pytest.fixture(autouse=True)
def _restore_environment():
    from app import config

    before = {k: v for k, v in os.environ.items() if k.startswith('MPACT_')}
    yield
    for key in [k for k in os.environ if k.startswith('MPACT_')]:
        del os.environ[key]
    os.environ.update(before)
    config.reload()


@pytest.fixture
def server(tmp_path):
    """``make(**env)`` -> a TestClient in email mode on a fresh data root."""
    from fastapi.testclient import TestClient

    from app import accounts, config
    from app.main import app
    from app.routers import authroutes

    clients = []

    def make(**env):
        for key in [k for k in os.environ if k.startswith('MPACT_')]:
            if key not in ('MPACT_PREWARM',):
                del os.environ[key]
        os.environ.update({
            'MPACT_DATA_ROOT': str(tmp_path / 'data'),
            'MPACT_AUTH': 'email',
            'MPACT_MAIL_OUTBOX': str(tmp_path / 'outbox.jsonl'),
            'MPACT_ADMIN_EMAILS': ADMIN,
            'MPACT_SITE_URL': 'https://mpact.example.org',
            **env,
        })
        config.reload()
        accounts.forget()
        accounts._SEEN.clear()
        authroutes._ATTEMPTS.clear()
        authroutes._CODE_REQUESTS.clear()
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    make.outbox = tmp_path / 'outbox.jsonl'
    yield make
    for client in clients:
        client.__exit__(None, None, None)


def mails(outbox):
    if not outbox.exists():
        return []
    return [json.loads(line) for line in outbox.read_text(encoding='utf-8').splitlines()]


def last_code(outbox, to):
    for mail in reversed(mails(outbox)):
        if mail['to'] == to:
            return re.search(r'(\d{3}) (\d{3})', mail['body']).group(0).replace(' ', '')
    raise AssertionError(f'no mail to {to}')


def sign_in(client, outbox, email):
    response = client.post('/api/auth/email/start', json={'email': email})
    assert response.status_code == 200, response.text
    code = last_code(outbox, email.strip().lower())
    response = client.post('/api/auth/email/verify', json={'email': email, 'code': code})
    assert response.status_code == 200, response.text
    return response.json()


# --- the flow -------------------------------------------------------------------

def test_signing_in_by_emailed_code(server):
    client = server()
    assert client.get('/api/auth/status').json()['mode'] == 'email'
    assert client.get('/api/files').status_code == 401

    sent = client.post('/api/auth/email/start', json={'email': ' Student@Smith.EDU '})
    assert sent.status_code == 200 and sent.json()['email'] == 'student@smith.edu'
    [mail] = mails(server.outbox)
    assert mail['to'] == 'student@smith.edu'
    code = last_code(server.outbox, 'student@smith.edu')
    assert mail['subject'].startswith(code), 'the code is in the subject, for phones'
    assert 'https://mpact.example.org' in mail['body']

    body = client.post('/api/auth/email/verify',
                       json={'email': 'student@smith.edu', 'code': f'{code[:3]} {code[3:]}'}).json()
    assert body['authenticated'] and body['created'] and body['role'] == 'internal'
    status = client.get('/api/auth/status').json()
    assert status['user'] == 'student@smith.edu' and status['role'] == 'internal'
    assert status['admin'] is False
    assert client.get('/api/files').status_code == 200

    # The workspace is the account's: a second browser lands in it too.
    client.post('/api/files', data={'kind': 'samplelist'},
                files={'file': ('s.csv', b'Injection,Sample_Code\na,b\n', 'text/csv')})
    other = server()
    sign_in(other, server.outbox, 'STUDENT@smith.edu')
    assert [f['name'] for f in other.get('/api/files').json()] == ['s.csv']

    client.post('/api/auth/logout')
    assert client.get('/api/files').status_code == 401


def test_a_code_works_once_and_only_the_newest_works(server):
    client = server()
    client.post('/api/auth/email/start', json={'email': 'a@smith.edu'})
    first = last_code(server.outbox, 'a@smith.edu')
    client.post('/api/auth/email/start', json={'email': 'a@smith.edu'})
    second = last_code(server.outbox, 'a@smith.edu')
    if first != second:
        refused = client.post('/api/auth/email/verify', json={'email': 'a@smith.edu', 'code': first})
        assert refused.status_code == 401
    assert client.post('/api/auth/email/verify',
                       json={'email': 'a@smith.edu', 'code': second}).status_code == 200
    again = server()
    assert again.post('/api/auth/email/verify',
                      json={'email': 'a@smith.edu', 'code': second}).status_code == 401


def test_wrong_guesses_use_a_code_up(server):
    client = server()
    client.post('/api/auth/email/start', json={'email': 'a@smith.edu'})
    code = last_code(server.outbox, 'a@smith.edu')
    wrong = f'{(int(code) + 1) % 10**6:06d}'
    for left in (4, 3, 2, 1):
        response = client.post('/api/auth/email/verify', json={'email': 'a@smith.edu', 'code': wrong})
        assert response.status_code == 401 and f'{left} tries left' in response.json()['detail']
    client.post('/api/auth/email/verify', json={'email': 'a@smith.edu', 'code': wrong})
    from app.routers import authroutes
    authroutes._ATTEMPTS.clear()      # the per-address throttle is not what is under test
    used_up = client.post('/api/auth/email/verify', json={'email': 'a@smith.edu', 'code': code})
    assert used_up.status_code == 401 and 'new one' in used_up.json()['detail']


def test_an_expired_code_is_refused(server):
    from app import accounts
    client = server()
    client.post('/api/auth/email/start', json={'email': 'a@smith.edu'})
    code = last_code(server.outbox, 'a@smith.edu')
    with sqlite3.connect(accounts.db_path()) as conn:
        conn.execute('UPDATE codes SET expires = ?', (time.time() - 1,))
    assert client.post('/api/auth/email/verify',
                       json={'email': 'a@smith.edu', 'code': code}).status_code == 401


def test_codes_are_stored_hashed(server):
    from app import accounts
    client = server()
    client.post('/api/auth/email/start', json={'email': 'a@smith.edu'})
    code = last_code(server.outbox, 'a@smith.edu')
    raw = accounts.db_path().read_bytes()
    with sqlite3.connect(accounts.db_path()) as conn:
        stored = conn.execute('SELECT code_hash FROM codes').fetchone()[0]
    assert code not in stored and len(stored) == 64
    assert code.encode() not in raw


def test_an_address_can_only_be_sent_so_many_codes(server):
    from app import accounts
    client = server()
    for _ in range(accounts.CODES_PER_HOUR):
        assert client.post('/api/auth/email/start', json={'email': 'a@smith.edu'}).status_code == 200
    refused = client.post('/api/auth/email/start', json={'email': 'a@smith.edu'})
    assert refused.status_code == 429
    assert len(mails(server.outbox)) == accounts.CODES_PER_HOUR


def test_one_address_can_only_ask_for_so_many_codes(server):
    client = server()
    statuses = [client.post('/api/auth/email/start', json={'email': f'p{i}@smith.edu'}).status_code
                for i in range(12)]
    assert statuses[:10] == [200] * 10 and statuses[10:] == [429, 429]


def test_a_bad_address_is_refused_and_nothing_is_sent(server):
    client = server()
    for bad in ('', 'nobody', 'a@b', 'two@@smith.edu', 'a b@smith.edu', 'x' * 300 + '@smith.edu'):
        assert client.post('/api/auth/email/start', json={'email': bad}).status_code == 422, bad
    assert mails(server.outbox) == []


def test_without_a_mail_server_signing_in_says_so(server):
    client = server(MPACT_MAIL_OUTBOX='', MPACT_SMTP_HOST='127.0.0.1', MPACT_SMTP_PORT='1')
    response = client.post('/api/auth/email/start', json={'email': 'a@smith.edu'})
    assert response.status_code == 503
    assert '127.0.0.1' not in response.json()['detail'], 'server detail is not for the user'


# --- roles ------------------------------------------------------------------------

def test_the_domain_decides_internal_or_external(server):
    from app import accounts
    server()
    assert accounts.automatic_role('a@smith.edu', []) == 'internal'
    assert accounts.automatic_role('a@science.smith.edu', []) == 'internal'
    assert accounts.automatic_role('a@notsmith.edu', []) == 'external'
    assert accounts.automatic_role('a@smith.edu.evil.com', []) == 'external'
    assert accounts.automatic_role('friend@umass.edu', ['friend@umass.edu']) == 'internal'
    assert accounts.automatic_role('anyone@umass.edu', ['@umass.edu']) == 'internal'


def test_outside_users_do_not_see_the_internal_datasets(server):
    client = server()
    assert sign_in(client, server.outbox, 'visitor@gmail.com')['role'] == 'external'
    datasets = client.get('/api/files/demo').json()
    ids = [d['id'] for d in datasets['datasets']]
    assert 'pty087i2' in ids and not any(i.startswith('chm350') for i in ids)
    assert datasets['default'] == 'pty087i2'
    assert client.post('/api/files/demo', json={'id': 'chm350-jm'}).status_code == 404

    inside = server()
    sign_in(inside, server.outbox, 'student@smith.edu')
    assert any(d['id'].startswith('chm350')
               for d in inside.get('/api/files/demo').json()['datasets'])


def test_external_workspaces_have_a_quota(server):
    client = server(MPACT_EXTERNAL_MAX_STORAGE_MB='1', MPACT_EXTERNAL_MAX_RUNS='1')
    sign_in(client, server.outbox, 'visitor@gmail.com')
    big = client.post('/api/files', data={'kind': 'samplelist'},
                      files={'file': ('big.csv', b'Injection,Sample_Code\n' + b'a,b\n' * 400_000,
                                      'text/csv')})
    assert big.status_code == 413 and 'MB' in big.json()['detail']
    assert client.get('/api/files').json() == [], 'nothing over quota is kept'

    ids = {}
    for kind, text in (('peaktable', b'Compound,m/z\n'), ('samplelist', b'Injection,Sample_Code\na,s\n'),
                       ('metadata', b'Sample_Code,Biological_Group\ns,g\n')):
        ids[kind] = client.post('/api/files', data={'kind': kind},
                                files={'file': (f'{kind}.csv', text, 'text/csv')}).json()['id']
    from app import accounts, storage
    session = storage.Session(accounts.lookup('visitor@gmail.com')['workspace'])
    session.new_run()
    settings = client.get('/api/runs/defaults').json()['settings']
    settings.update(blnkfltr=False, expgrp='g', ctrgrp='h')
    refused = client.post('/api/runs', json={'inputs': ids, 'settings': settings})
    assert refused.status_code == 409 and 'at most 1' in refused.json()['detail']


def test_internal_workspaces_have_no_quota_by_default(server):
    client = server(MPACT_EXTERNAL_MAX_STORAGE_MB='1')
    sign_in(client, server.outbox, 'student@smith.edu')
    big = client.post('/api/files', data={'kind': 'samplelist'},
                      files={'file': ('big.csv', b'Injection,Sample_Code\n' + b'a,b\n' * 400_000,
                                      'text/csv')})
    assert big.status_code == 200, big.text


# --- administration ------------------------------------------------------------------

def test_the_admin_pages_exist_only_for_admins(server):
    client = server()
    sign_in(client, server.outbox, 'student@smith.edu')
    assert client.get('/api/admin/users').status_code == 404
    assert client.post('/api/admin/allowlist', json={'entry': 'x@y.org'}).status_code == 404
    anonymous = server()
    assert anonymous.get('/api/admin/users').status_code in (401, 404)


def test_an_admin_sees_signups_by_domain_and_can_change_roles(server):
    visitor = server()
    sign_in(visitor, server.outbox, 'visitor@gmail.com')
    sign_in(server(), server.outbox, 'student@smith.edu')
    admin = server()
    assert sign_in(admin, server.outbox, ADMIN)['role'] == 'internal'
    assert admin.get('/api/auth/status').json()['admin'] is True

    body = admin.get('/api/admin/users').json()
    assert {u['email'] for u in body['users']} == {'visitor@gmail.com', 'student@smith.edu', ADMIN}
    assert {d['domain']: d['count'] for d in body['domains']} == {'smith.edu': 2, 'gmail.com': 1}
    assert body['internal_domains'] == ['smith.edu'] and body['mail'] is True

    # Make the visitor a collaborator: internal at once, no sign-out needed.
    assert admin.post('/api/admin/allowlist', json={'entry': 'Visitor@Gmail.com'}).json()['entry'] \
        == 'visitor@gmail.com'
    assert visitor.get('/api/auth/status').json()['role'] == 'internal'
    admin.delete('/api/admin/allowlist', params={'entry': 'visitor@gmail.com'})
    from app import accounts
    accounts.forget()
    assert visitor.get('/api/auth/status').json()['role'] == 'external'

    # Block them: signed out, and no code is sent (though the page says it was).
    blocked = admin.post('/api/admin/users', json={'email': 'visitor@gmail.com', 'role': 'blocked'})
    assert blocked.json()['role'] == 'blocked'
    assert visitor.get('/api/files').status_code == 401
    sent_before = len(mails(server.outbox))
    assert visitor.post('/api/auth/email/start', json={'email': 'visitor@gmail.com'}).json()['sent']
    assert len(mails(server.outbox)) == sent_before
    admin.post('/api/admin/users', json={'email': 'visitor@gmail.com', 'reset_role': True})
    assert accounts.lookup('visitor@gmail.com')['role'] == 'external'

    assert admin.post('/api/admin/users', json={'email': ADMIN, 'role': 'blocked'}).status_code == 422
    assert admin.post('/api/admin/users', json={'email': 'nobody@x.org', 'role': 'internal'}).status_code == 404


def test_with_approval_on_outside_signups_wait(server):
    client = server(MPACT_SIGNUP='approval')
    body = sign_in(client, server.outbox, 'visitor@gmail.com')
    assert body['pending'] and not body['authenticated']
    assert client.get('/api/files').status_code == 401
    notice = [m for m in mails(server.outbox) if m['to'] == ADMIN]
    assert notice and 'visitor@gmail.com' in notice[0]['subject']

    # Smith addresses do not wait.
    assert sign_in(server(), server.outbox, 'student@smith.edu')['authenticated']

    admin = server(MPACT_SIGNUP='approval')
    sign_in(admin, server.outbox, ADMIN)
    assert admin.post('/api/admin/users',
                      json={'email': 'visitor@gmail.com', 'approve': True}).json()['status'] == 'active'
    assert any(m['to'] == 'visitor@gmail.com' and 'ready' in m['subject'] for m in mails(server.outbox))
    assert sign_in(client, server.outbox, 'visitor@gmail.com')['authenticated']
    assert client.get('/api/files').status_code == 200
