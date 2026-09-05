#!/usr/bin/env python
"""Manage MPACT accounts.

The password file is a plain text list of ``username:scrypt-hash`` lines, so
it can be read, backed up and version-controlled (it holds no passwords, only
hashes). This script is the way to write it: hashes are not something to
produce by hand.

    python scripts/mpact_users.py add rsamples
    python scripts/mpact_users.py list
    python scripts/mpact_users.py passwd rsamples
    python scripts/mpact_users.py remove olduser

The file location follows the server's own configuration, so run this with
the same ``MPACT_DATA_ROOT``/``MPACT_USERS_FILE`` the server uses -- or pass
``--file``. Nothing here needs the server to be stopped; it re-reads the file
on every login.
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))

from app import auth, config  # noqa: E402  (after the path insert)

MIN_PASSWORD = 8


def ask_password(username):
    """Prompt twice, and refuse a password too short to be worth hashing."""
    while True:
        first = getpass.getpass(f'Password for {username}: ')
        if len(first) < MIN_PASSWORD:
            print(f'  too short: use at least {MIN_PASSWORD} characters')
            continue
        second = getpass.getpass('Repeat: ')
        if first != second:
            print('  those do not match')
            continue
        return first


def cmd_list(args):
    users = auth.load_users()
    print(f'{auth.users_path()}')
    if not users:
        print('  (no accounts yet)')
        return 0
    for name in sorted(users):
        print(f'  {name}')
    return 0


def cmd_add(args):
    users = auth.load_users()
    if args.username in users:
        print(f'{args.username} already exists; use "passwd" to change the password')
        return 1
    if not auth.USERNAME_RE.match(args.username):
        print('usernames may use letters, digits, and . _ @ - (up to 64 characters)')
        return 1
    password = args.password or ask_password(args.username)
    users[args.username] = auth.hash_password(password)
    auth.save_users(users)
    print(f'added {args.username} to {auth.users_path()}')
    return 0


def cmd_passwd(args):
    users = auth.load_users()
    if args.username not in users:
        print(f'no such account: {args.username}')
        return 1
    password = args.password or ask_password(args.username)
    users[args.username] = auth.hash_password(password)
    auth.save_users(users)
    print(f'changed the password for {args.username}')
    return 0


def cmd_remove(args):
    users = auth.load_users()
    if users.pop(args.username, None) is None:
        print(f'no such account: {args.username}')
        return 1
    auth.save_users(users)
    print(f'removed {args.username}')
    print('note: their uploads and runs are still on disk; delete their workspace '
          f'directory ({auth.workspace_id(args.username)}) to reclaim the space')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--file', help='password file (default: the server\'s)')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('list', help='show the accounts that exist')

    for name, handler, helptext in [('add', cmd_add, 'create an account'),
                                    ('passwd', cmd_passwd, 'change a password'),
                                    ('remove', cmd_remove, 'delete an account')]:
        p = sub.add_parser(name, help=helptext)
        p.add_argument('username')
        if name != 'remove':
            p.add_argument('--password',
                           help='non-interactive; leaves the password in your shell '
                                'history, so prefer the prompt')

    args = parser.parse_args(argv)
    if args.file:
        os.environ['MPACT_USERS_FILE'] = args.file
        config.reload()

    handlers = {'list': cmd_list, 'add': cmd_add,
                'passwd': cmd_passwd, 'remove': cmd_remove}
    return handlers[args.command](args)


if __name__ == '__main__':
    raise SystemExit(main())
