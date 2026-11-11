#!/usr/bin/env python3
"""Vendor the browser libraries into ``web/vendor`` for offline deployments.

``web/index.html`` tries ``/vendor/plotly.min.js`` first and falls back to
cdnjs, so running this makes a deployment self-contained: no outbound
requests from the browser, and no dependency on a CDN being reachable from
inside a lab network.

Usage::

    python scripts/fetch_web_vendor.py
    python scripts/fetch_web_vendor.py --version 4.0.0
"""

import argparse
import hashlib
import io
import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / 'web' / 'vendor'

# Keep in step with the fallback URL in web/index.html -- if you change one,
# change the other, or an offline deployment and an online one will run
# different library versions.
DEFAULT_VERSION = '4.0.0'
URL_TEMPLATE = 'https://cdnjs.cloudflare.com/ajax/libs/plotly.js/{version}/plotly.min.js'
MIN_EXPECTED_BYTES = 1_000_000   # a real plotly bundle is several MB

# JSME molecule editor (BSD), used by the Feature Info substructure filter.
# Keep in step with the fallback in web/src/views/sketcher.js.
JSME_URL = 'https://jsme-editor.github.io/downloads/JSME_2024-04-29.zip'


def download(url, destination, timeout=180):
    """Stream to a temp file and atomically replace, so an interrupted
    download can't leave a truncated library that the page would then try to
    execute."""
    temp = destination.with_name(destination.name + '.part')
    digest = hashlib.sha256()
    written = 0
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response, temp.open('wb') as out:
            if getattr(response, 'status', 200) != 200:
                raise RuntimeError(f'HTTP {response.status}')
            while chunk := response.read(256 * 1024):
                out.write(chunk)
                digest.update(chunk)
                written += len(chunk)
        if written < MIN_EXPECTED_BYTES:
            raise RuntimeError(f'downloaded only {written} bytes; that is not the real bundle')
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)
    return written, digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', default=DEFAULT_VERSION,
                        help=f'Plotly version to vendor (default {DEFAULT_VERSION})')
    args = parser.parse_args()

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    destination = VENDOR_DIR / 'plotly.min.js'
    url = URL_TEMPLATE.format(version=args.version)

    print(f'Downloading {url}')
    try:
        written, digest = download(url, destination)
    except Exception as exc:  # noqa: BLE001
        print(f'error: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1

    print(f'Done: {destination} ({written / (1024 * 1024):.1f} MB)')
    print(f'sha256: {digest}')

    print(f'Downloading {JSME_URL}')
    try:
        fetch_jsme(VENDOR_DIR / 'jsme')
    except Exception as exc:  # noqa: BLE001
        print(f'error: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    print(f'Done: {VENDOR_DIR / "jsme"}')
    print('The page prefers these local copies automatically; no config change needed.')
    return 0


def fetch_jsme(destination, timeout=180):
    """Unpack the distribution's ``jsme/`` directory and its licence."""
    with urllib.request.urlopen(JSME_URL, timeout=timeout) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    staging = destination.with_name(destination.name + '.part')
    shutil.rmtree(staging, ignore_errors=True)
    found = False
    for member in archive.infolist():
        parts = Path(member.filename).parts
        if len(parts) < 2 or member.is_dir():
            continue
        if parts[1] == 'license.txt':
            relative = Path('license.txt')
        elif parts[1] == 'jsme' and len(parts) > 2:
            relative = Path(*parts[2:])
            found = found or relative.name == 'jsme.nocache.js'
        else:
            continue
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(member))
    if not found:
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError('jsme.nocache.js not found in the archive')
    shutil.rmtree(destination, ignore_errors=True)
    os.replace(staging, destination)


if __name__ == '__main__':
    sys.exit(main())
