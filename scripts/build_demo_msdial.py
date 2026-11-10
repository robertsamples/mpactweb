"""Package MS-DIAL alignment exports as bundled demo datasets.

    python scripts/build_demo_msdial.py server/data/demo/chm350 \\
        --dataset AS "path/to/Area_1_....csv" "path/to/Msp_....msp" \\
        --dataset CP "path/to/Area_1_....txt" "path/to/Msp_....msp" [--prefix CHM350] [--full]

Writes ``<prefix>_<NAME>.txt.gz`` and ``<prefix>_<NAME>.msp.gz`` per dataset.
The demo loader (server/app/demo.py) decompresses them into a session's
library, where they go through the same MS-DIAL import an upload does.

By default the peak table is *lean*: the export rewritten into the layout
the pipeline reads (server/app/msdial.py) with the columns MPACT never
reads emptied -- MS-DIAL's annotation text (names, SMILES, spectra...) and
its per-class Average/Stdev columns. Alignment IDs, retention times, m/z
and every abundance are kept exactly, so an analysis of the lean file is
identical to one of the original, at about half the size. ``--full`` keeps
the export byte for byte instead (compressed only).
"""

import argparse
import gzip
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'server'))

from app import msdial  # noqa: E402


def lean_copy(normalized, out):
    """The normalized legacy table with only what the pipeline reads."""
    layout, header_at = msdial.read_layout(normalized)
    keys = layout.keys
    blanks = ['null'] * (msdial.LEGACY_META - len(keys))
    with open(out, 'w', encoding='utf-8', newline='') as handle:
        for index, row in enumerate(msdial._rows(normalized)):
            if not any(cell.strip() for cell in row):
                continue
            samples = [row[i] for i in layout.samples]
            if index < header_at:
                lead = row[:msdial.LEGACY_META]
                tail = ['Average', 'Stdev'] if row[msdial.LEGACY_META - 1] == 'Batch ID' \
                    else ['NA', 'NA']
            elif index == header_at:
                lead = row[:msdial.LEGACY_META]
                tail = list(msdial.SUMMARY)
            else:
                lead = [row[i] for i in keys] + blanks
                tail = ['0', '0']
            handle.write('\t'.join(lead + samples + tail) + '\n')


def gz(source, target):
    with open(source, 'rb') as raw, gzip.open(target, 'wb', compresslevel=9) as packed:
        shutil.copyfileobj(raw, packed, 1 << 20)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('out', type=Path)
    parser.add_argument('--dataset', nargs=3, action='append', required=True,
                        metavar=('NAME', 'EXPORT', 'MSP'))
    parser.add_argument('--prefix', default='CHM350')
    parser.add_argument('--full', action='store_true',
                        help='keep the export byte for byte instead of the lean table')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    for name, export, msp in args.dataset:
        export, msp = Path(export), Path(msp)
        stem = f'{args.prefix}_{name}'
        with tempfile.TemporaryDirectory() as scratch:
            work = Path(scratch) / (stem + export.suffix.lower())
            shutil.copy2(export, work)
            if args.full:
                table = work
            else:
                normalized = msdial.normalize(work) or work
                table = Path(scratch) / (stem + '.lean.txt')
                lean_copy(normalized, table)
            table_out = args.out / f'{stem}{".txt" if not args.full else export.suffix.lower()}.gz'
            gz(table, table_out)
        msp_out = args.out / f'{stem}.msp.gz'
        gz(msp, msp_out)
        print(f'{stem}: {table_out.name} {table_out.stat().st_size / 1e6:.1f} MB, '
              f'{msp_out.name} {msp_out.stat().st_size / 1e6:.1f} MB')


if __name__ == '__main__':
    main()
