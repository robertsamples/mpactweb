#!/usr/bin/env python3
"""Download the Natural Products Atlas table the database search needs.

``npatlas.tsv`` is ~33MB, so it is fetched rather than committed. Without it
the server still runs -- ``/api/health`` reports ``npatlas: false`` and the
front end hides the database-search controls instead of failing at them.

The download is done by the vendored ``npatlasupdate.download_atlas`` (the
same code the desktop app's update check uses): it streams to a temp file,
validates the header, and only then atomically replaces the target, so an
interrupted transfer or an HTML error page can't leave a corrupt atlas
behind.

Usage::

    python scripts/fetch_reference_data.py            # skip if already current
    python scripts/fetch_reference_data.py --force    # always re-download
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'server'))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--force', action='store_true',
                        help='re-download even if the atlas is present and current')
    parser.add_argument('--dest', default=None,
                        help='destination path (default: server/data/npatlas.tsv)')
    parser.add_argument('--max-age-days', type=int, default=None,
                        help='re-download if the existing atlas is older than this')
    args = parser.parse_args()

    from app import config                    # noqa: E402 - needs sys.path above
    from app.core import npatlasupdate        # noqa: E402

    destination = Path(args.dest) if args.dest else config.NPATLAS_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() and not args.force:
        max_age = args.max_age_days if args.max_age_days is not None \
            else npatlasupdate.DEFAULT_MAX_AGE_DAYS
        if not npatlasupdate.is_update_due(destination, max_age_days=max_age):
            age = npatlasupdate.atlas_age_days(destination)
            size = destination.stat().st_size / (1024 * 1024)
            print(f'{destination} is current ({size:.1f} MB, {age:.0f} days old). '
                  'Use --force to re-download.')
            return 0
        print(f'{destination} is older than {max_age} days; refreshing.')

    print(f'Downloading the Natural Products Atlas to {destination} ...')
    try:
        written = npatlasupdate.download_atlas(destination)
    except Exception as exc:  # noqa: BLE001 - report the cause, don't traceback at a user
        print(f'error: download failed ({type(exc).__name__}: {exc})', file=sys.stderr)
        print('The existing atlas, if any, was left untouched.', file=sys.stderr)
        return 1

    print(f'Done: {destination} ({written / (1024 * 1024):.1f} MB)')
    print('Restart the server for it to pick the atlas up.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
