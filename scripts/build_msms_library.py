#!/usr/bin/env python3
"""Compile an MS-DIAL MSP library for the MS/MS search.

    python scripts/build_msms_library.py MSMS-Public_experimentspectra-pos-VS19.msp

Writes ``msdial_msms.npz`` (precursors and cleaned, normalised peaks) and
``msdial_msms_meta.tsv.gz`` (names, structures) to the reference directory
(``MPACT_REFERENCE_DIR``, default ``server/data``). Positive-mode spectra
only; peaks under 1% of the base peak are dropped, the 50 most intense kept,
and spectra left with fewer than 3 peaks skipped.
"""

import argparse
import gzip
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'server'))

META = ('name', 'precursortype', 'formula', 'ontology', 'inchikey', 'smiles',
        'collisionenergy', 'instrumenttype')


def records(path):
    fields, peaks = {}, []
    with open(path, encoding='utf-8', errors='replace') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                if fields:
                    yield fields, peaks
                fields, peaks = {}, []
                continue
            if line[0].isdigit():
                parts = line.replace(',', ' ').split()
                if len(parts) >= 2:
                    try:
                        peaks.append((float(parts[0]), float(parts[1])))
                    except ValueError:
                        pass
                continue
            key, _, value = line.partition(':')
            fields[key.strip().lower()] = value.strip()
    if fields:
        yield fields, peaks


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('msp')
    parser.add_argument('--out', help='output directory (default: the reference directory)')
    args = parser.parse_args()

    from app import config
    from app.plotdata.hits import canonical_smiles
    from app.plotdata.msms import clean_peaks
    out = Path(args.out) if args.out else config.REFERENCE_DIR
    out.mkdir(parents=True, exist_ok=True)

    precursors, ptr, mzs, intensities, meta = [], [0], [], [], []
    skipped = 0
    for fields, peaks in records(args.msp):
        if fields.get('ionmode', 'positive').lower() != 'positive':
            skipped += 1
            continue
        try:
            precursor = float(fields.get('precursormz', ''))
        except ValueError:
            skipped += 1
            continue
        mz, intensity = clean_peaks(np.array(peaks, dtype=float).reshape(-1, 2))
        if mz.size < 3:
            skipped += 1
            continue
        precursors.append(precursor)
        mzs.append(mz.astype(np.float32))
        intensities.append(intensity.astype(np.float32))
        ptr.append(ptr[-1] + mz.size)
        entry = {key: fields.get(key, '') for key in META}
        entry['name'] = entry['name'].split(';')[0].strip()
        entry['smiles'] = canonical_smiles(entry['smiles'])
        meta.append(entry)

    np.savez_compressed(out / 'msdial_msms.npz',
                        precursor=np.array(precursors, dtype=np.float64),
                        ptr=np.array(ptr, dtype=np.int64),
                        mz=np.concatenate(mzs), intensity=np.concatenate(intensities))
    with gzip.open(out / 'msdial_msms_meta.tsv.gz', 'wt', encoding='utf-8', newline='\n') as handle:
        handle.write('\t'.join(META) + '\n')
        for entry in meta:
            handle.write('\t'.join(entry[key].replace('\t', ' ') for key in META) + '\n')
    print(f'{out}: {len(precursors)} spectra, {ptr[-1]} peaks ({skipped} skipped)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
