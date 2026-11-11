#!/usr/bin/env python3
"""Build the reference files the Feature Info hit filters use.

    server/data/npatlas_taxonomy.tsv   genus -> phylum/class/order/family (NCBI)
    server/data/siderite_inchikeys.txt InChIKey first blocks of every SIDERITE structure

Usage::

    python scripts/build_hit_annotations.py --taxdump taxdump.tar.gz \
        --siderite SIDERITE_unique_structures_20251231.xlsx

``--taxdump`` is NCBI's https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz.
Either step can be run on its own.
"""

import argparse
import sys
import tarfile
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / 'server' / 'data'
RANKS = ('phylum', 'class', 'order', 'family')

# NPAtlas origin_type -> the NCBI lineage node a genus must sit under.
ORIGIN_ROOTS = {
    'Bacterium': ('Bacteria',),
    'Fungus': ('Fungi',),
    'Plant': ('Viridiplantae',),
    'Animal': ('Metazoa',),
}


def atlas_genera():
    frames = []
    for path in (DATA / 'npatlas.tsv', DATA / 'npatlas_supplemental.tsv'):
        if path.exists():
            frames.append(pd.read_csv(path, sep='\t', usecols=['origin_type', 'genus']))
    frame = pd.concat(frames).dropna()
    return frame.drop_duplicates()


def read_dmp(handle):
    for raw in handle:
        yield raw.decode('utf-8').rstrip('\t|\n').split('\t|\t')


def build_taxonomy(taxdump):
    with tarfile.open(taxdump) as archive:
        parent, rank = {}, {}
        for fields in read_dmp(archive.extractfile('nodes.dmp')):
            node = int(fields[0])
            parent[node] = int(fields[1])
            rank[node] = fields[2]
        name = {}
        genus_ids = {}
        synonym_ids = {}
        for fields in read_dmp(archive.extractfile('names.dmp')):
            node = int(fields[0])
            if fields[3] == 'scientific name':
                name[node] = fields[1]
                if rank.get(node) == 'genus':
                    genus_ids.setdefault(fields[1], []).append(node)
            elif fields[3] in ('synonym', 'equivalent name') and rank.get(node) == 'genus':
                synonym_ids.setdefault(fields[1], []).append(node)

    def lineage(node):
        out = {}
        names = set()
        while node in parent and parent[node] != node:
            names.add(name.get(node, ''))
            if rank[node] in RANKS:
                out[rank[node]] = name.get(node, '')
            node = parent[node]
        return out, names

    rows = []
    for origin, genus in atlas_genera().itertuples(index=False):
        roots = ORIGIN_ROOTS.get(origin, ())
        best = None
        for node in genus_ids.get(genus) or synonym_ids.get(genus, []):
            ranks, names = lineage(node)
            if roots and not names.intersection(roots):
                continue
            if best is None or len(ranks) > len(best):
                best = ranks
        best = best or {}
        rows.append({'origin_type': origin, 'genus': genus,
                     **{level: best.get(level, '') for level in RANKS}})
    table = pd.DataFrame(rows).sort_values(['origin_type', 'genus'])
    out = DATA / 'npatlas_taxonomy.tsv'
    table.to_csv(out, sep='\t', index=False)
    resolved = (table['family'] != '') | (table['phylum'] != '')
    print(f'{out}: {len(table)} genera, {int(resolved.sum())} resolved')


def build_siderite(path):
    from rdkit import Chem, RDLogger
    RDLogger.DisableLog('rdApp.*')
    frame = pd.read_excel(path)
    column = 'Canonical SMILES' if 'Canonical SMILES' in frame else 'SMILES'
    keys = set()
    for smiles in frame[column].dropna().astype(str):
        molecule = Chem.MolFromSmiles(smiles)
        if molecule is None:
            continue
        key = Chem.MolToInchiKey(molecule)
        if key:
            keys.add(key.split('-')[0])
    out = DATA / 'siderite_inchikeys.txt'
    out.write_text('\n'.join(sorted(keys)) + '\n', encoding='utf-8')
    print(f'{out}: {len(keys)} structures')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--taxdump', help='NCBI taxdump.tar.gz')
    parser.add_argument('--siderite', help='SIDERITE unique structures .xlsx')
    args = parser.parse_args()
    if not (args.taxdump or args.siderite):
        parser.error('nothing to do; pass --taxdump and/or --siderite')
    if args.taxdump:
        build_taxonomy(args.taxdump)
    if args.siderite:
        build_siderite(args.siderite)
    return 0


if __name__ == '__main__':
    sys.exit(main())
