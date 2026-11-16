#!/usr/bin/env python3
"""Vendor (or verify) MPACT's Qt-free core modules into ``server/mpact_core``.

The web app is a new *front end* for the existing MPACT desktop pipeline, not
a fork of it. Every module listed in ``VENDORED_MODULES`` is copied
byte-for-byte out of the desktop repo's ``code/`` directory and must stay
that way -- all web-specific behaviour lives in ``server/app/``, which calls
into these modules exactly the way ``code/main.py`` does.

Usage::

    python scripts/vendor_core.py verify              # CI: fail on any drift
    python scripts/vendor_core.py sync --src ../mpact # re-copy from upstream

``verify`` is the default and needs no upstream checkout -- it just checks the
vendored files against the hashes recorded in ``VENDOR_MANIFEST.json``, so a
local edit to a vendored file is caught even without the desktop repo present.
"""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / 'server' / 'mpact_core'
MANIFEST_PATH = VENDOR_DIR / 'VENDOR_MANIFEST.json'

# The Qt-free subset of MPACT's ``code/`` directory. Anything importing PyQt5
# (main.py, ui_*.py, plotting.py, dialogs.py, searchtree.py, svgloader.py,
# files*.py) is deliberately NOT vendored: rendering is the one layer the web
# app has to reimplement, and it does so from these modules' *outputs*.
VENDORED_MODULES = (
    # analysis pipeline (the actual "backend", called unmodified)
    'MSFaST.py',
    'filter.py',
    'stats.py',
    'groupsets.py',
    # Qt-free compute already extracted upstream for testability
    'ordination.py',
    'pvclust.py',
    'clusterpurity.py',
    'qualityscore.py',
    'treefilters.py',
    'biogroups.py',
    'dbsearch.py',
    # import/export translators + IO helpers
    'translators.py',
    'getfragdb.py',
    'mspwriter.py',
    'csvcache.py',
    'mzmineimport.py',
    # optional extras (structure rendering / atlas refresh)
    'npatlassearch.py',
    'npatlasupdate.py',
)

# Files intentionally left behind, with the reason. Kept here (not just in
# prose) so a future sync can tell "not vendored on purpose" from "forgotten".
EXCLUDED_MODULES = {
    'main.py': 'PyQt5 MainWindow -- replaced by server/app + web/',
    'ui_main.py': 'generated Qt Designer UI',
    'ui_main1.py': 'generated Qt Designer UI (alternate)',
    'ui_featureinfo.py': 'generated Qt Designer UI',
    'ui_plotparam.py': 'generated Qt Designer UI',
    'ui_functions.py': 'Qt widget/controller glue',
    'plotting.py': 'matplotlib-in-Qt rendering -- replaced by app/plotdata + web/src/plots',
    'dialogs.py': 'Qt message boxes',
    'searchtree.py': 'Qt tree model/proxy (its rules live in treefilters.py, which IS vendored)',
    'svgloader.py': 'Qt SVG widget loader',
    'files.py': 'generated Qt resource bundle',
    'files_rc.py': 'generated Qt resource bundle',
    'plotslots.py': 'Qt plot-widget slot registry',
    'paramfields.py': 'Qt checkbox<->param schema (web equivalent: app/schemas.py)',
    'importdependencies.py': 'runtime pip installer -- server deps are pinned in requirements.txt',
    'crashreport.py': 'desktop crash dialog/log',
    'mpactupdate.py': 'desktop self-update check',
    'masstdriver.py': 'selenium browser driver for MASST submission',
}


def sha256(path):
    """Hash of the file's content with line endings normalised to LF.

    Git on Windows checks text files out with CRLF, so the same commit has
    different bytes on a Windows desktop and in a Linux container. Hashing the
    raw bytes made the manifest match only the machine that wrote it.
    """
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def load_manifest():
    if not MANIFEST_PATH.exists():
        return None
    return json.loads(MANIFEST_PATH.read_text())


def cmd_sync(args):
    src = Path(args.src).expanduser().resolve() / 'code'
    if not src.is_dir():
        sys.exit(f'error: {src} is not a directory (pass --src <path to mpact repo>)')

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    entries, missing = {}, []
    for name in VENDORED_MODULES:
        source = src / name
        if not source.exists():
            missing.append(name)
            continue
        shutil.copy2(source, VENDOR_DIR / name)
        entries[name] = sha256(VENDOR_DIR / name)

    if missing:
        sys.exit(f'error: not found upstream: {", ".join(missing)}')

    manifest = {
        'upstream': 'https://github.com/robertsamples/mpact',
        'upstream_path': 'code/',
        'note': ('Vendored byte-for-byte. Do not edit these files here -- fix '
                 'upstream and re-run scripts/vendor_core.py sync.'),
        'excluded': EXCLUDED_MODULES,
        'files': entries,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print(f'vendored {len(entries)} modules into {VENDOR_DIR.relative_to(REPO_ROOT)}')
    return 0


def cmd_verify(args):
    manifest = load_manifest()
    if manifest is None:
        sys.exit(f'error: {MANIFEST_PATH} missing -- run "vendor_core.py sync" first')

    problems = []
    for name, expected in sorted(manifest['files'].items()):
        path = VENDOR_DIR / name
        if not path.exists():
            problems.append(f'{name}: missing')
        elif sha256(path) != expected:
            problems.append(f'{name}: MODIFIED (vendored core must stay identical to upstream)')

    extra = {p.name for p in VENDOR_DIR.glob('*.py')} - set(manifest['files'])
    for name in sorted(extra):
        problems.append(f'{name}: untracked file in vendor dir')

    if problems:
        print('vendored core drift detected:', file=sys.stderr)
        for line in problems:
            print(f'  - {line}', file=sys.stderr)
        return 1
    print(f'ok: {len(manifest["files"])} vendored modules match manifest')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command')
    sync = sub.add_parser('sync', help='re-copy modules from an upstream checkout')
    sync.add_argument('--src', default='../mpact', help='path to the mpact desktop repo')
    sync.set_defaults(func=cmd_sync)
    verify = sub.add_parser('verify', help='check vendored files against the manifest')
    verify.set_defaults(func=cmd_verify)
    args = parser.parse_args()
    if not getattr(args, 'func', None):
        args = parser.parse_args(['verify'])
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
