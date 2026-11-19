#!/usr/bin/env python3
"""Delete the oldest analysis runs until the data disk has enough free space.

Runs on the host (not in the container) from a nightly systemd timer; see
deploy/systemd/. It deletes whole run directories, oldest first, the same way
the Runs page does, and stops as soon as free space reaches the target.
Uploaded files and accounts are never touched. Queued or running jobs are
skipped unless their status has not changed for longer than any job can run.

    prune_runs.py [--data DIR] [--min-free-gb 10] [--dry-run]
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

GB = 1024 ** 3
# A queued/running status untouched for this long belongs to a job that died
# with an earlier server (jobs time out after an hour).
STALE_SECONDS = 3 * 3600


def volume_path(name):
    out = subprocess.run(['docker', 'volume', 'inspect', '-f', '{{.Mountpoint}}', name],
                         check=True, capture_output=True, text=True)
    return Path(out.stdout.strip())


def started_at(run_dir):
    """When the run was started: status.json's started_at, else the oldest
    of the run's own files."""
    try:
        status = json.loads((run_dir / 'status.json').read_text())
        if status.get('started_at'):
            return float(status['started_at'])
    except (OSError, ValueError):
        pass
    for name in ('request.json', 'status.json'):
        try:
            return (run_dir / name).stat().st_mtime
        except OSError:
            continue
    return run_dir.stat().st_mtime


def is_live(run_dir, now):
    try:
        status = json.loads((run_dir / 'status.json').read_text())
        mtime = (run_dir / 'status.json').stat().st_mtime
    except (OSError, ValueError):
        return False
    return status.get('state') in ('queued', 'running') and now - mtime < STALE_SECONDS


def dir_size(path):
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--data', type=Path,
                        help='MPACT data directory (default: the deploy_mpact-data volume)')
    parser.add_argument('--min-free-gb', type=float, default=10.0)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    data = args.data or volume_path('deploy_mpact-data')
    sessions = data / 'sessions'
    target = args.min_free_gb * GB
    free = shutil.disk_usage(data).free
    print(f'free space {free / GB:.1f} GB, target {args.min_free_gb:g} GB')
    if free >= target:
        return 0

    now = time.time()
    runs = sorted((p for p in sessions.glob('s_*/runs/r_*') if p.is_dir()), key=started_at)
    deleted = 0
    for run in runs:
        if free >= target:
            break
        if is_live(run, now):
            print(f'skip {run.parent.parent.name}/{run.name}: still queued or running')
            continue
        size = dir_size(run)
        when = time.strftime('%Y-%m-%d %H:%M', time.gmtime(started_at(run)))
        print(f'{"would delete" if args.dry_run else "delete"} '
              f'{run.parent.parent.name}/{run.name} (started {when} UTC, {size / GB:.2f} GB)')
        if args.dry_run:
            free += size
        else:
            shutil.rmtree(run, ignore_errors=True)
            free = shutil.disk_usage(data).free
        deleted += 1

    print(f'{deleted} run(s) {"would be " if args.dry_run else ""}deleted; '
          f'free space {free / GB:.1f} GB')
    if free < target:
        print('warning: still below target after removing every run that could be removed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
