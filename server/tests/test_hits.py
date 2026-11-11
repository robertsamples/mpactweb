"""Hit-filter chemistry: warhead SMARTS and conjugated-system size."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

Chem = pytest.importorskip('rdkit.Chem')

from app.plotdata import hits  # noqa: E402


def labels(smiles):
    molecule = Chem.MolFromSmiles(smiles)
    return {label for label, pattern in hits._patterns() if molecule.HasSubstructMatch(pattern)}


def conjugated(smiles):
    return hits.conjugated_size(Chem.MolFromSmiles(smiles))


@pytest.mark.parametrize('smiles, expected', [
    ('CC1OC1C', 'Epoxide'),
    ('CC1=CCC(CC1=O)C(=C)C', 'Michael acceptor'),          # carvone
    ('C=C1CCOC1=O', 'Michael acceptor'),                   # alpha-methylene lactone
    ('O=C1C=CC(=O)N1C', 'Michael acceptor'),               # maleimide
    ('O=C1C=CC(=O)c2ccccc12', 'Quinone'),                  # 1,4-naphthoquinone
    ('CC1OC(=O)C1C', 'β-Lactone'),
    ('O=C1CCN1', 'β-Lactam'),
    ('CC(=O)CBr', 'α-Halocarbonyl'),
])
def test_warheads(smiles, expected):
    assert expected in labels(smiles)


@pytest.mark.parametrize('smiles', [
    'CCOC(=O)/C=C/C',          # beta-substituted ester: a weak acceptor
    'OC(=O)/C=C/c1ccccc1',     # cinnamic acid
    'CC(C)C1CCC(C)CC1=O',      # menthone
    'CC(=O)Nc1ccccc1',         # acetanilide
])
def test_non_electrophiles(smiles):
    assert not labels(smiles)


@pytest.mark.parametrize('smiles, size', [
    ('c1ccccc1', 6),
    ('c1ccccc1Oc1ccccc1', 6),            # ether oxygen does not join the rings
    ('c1ccccc1-c1ccccc1', 12),
    ('O=C1c2ccccc2C(=O)c2ccccc12', 16),  # anthraquinone
    ('C=CC=CC=CC=CC=CC=CC=C', 14),       # heptaene
    ('CC(=O)NCC(=O)O', 2),
    ('CCCC', 0),
])
def test_conjugated_size(smiles, size):
    assert conjugated(smiles) == size


def test_normalise_drops_empty_criteria():
    assert hits.normalise({'name': ' ', 'taxon': {'level': 'genus', 'value': ''},
                           'substructure': '', 'activity': []}) is None
    spec = hits.normalise({'name': 'Mycin', 'activity': ['siderophore', 'bogus']})
    assert spec['name'] == 'mycin'
    assert spec['activity'] == ['siderophore']


def test_bad_queries_raise_value_error():
    with pytest.raises(ValueError):
        hits.parse_query('((')
    with pytest.raises(ValueError):
        hits.normalise({'taxon': {'level': 'tribe', 'value': 'x'}})


def test_the_atlas_narrows_at_any_rank():
    from app.plotdata import feature
    try:
        everything = feature.load_atlas()
    except FileNotFoundError:
        pytest.skip('npatlas.tsv is not installed')
    family = feature.load_atlas(taxon_level='family', taxon='streptomycetaceae')
    assert 0 < len(family) < len(everything)
    assert (family['origin_type'] == 'Bacterium').all()
    genus = feature.load_atlas(taxon_level='genus', taxon='Streptomyces')
    assert len(genus) <= len(family)
    assert len(feature.load_atlas('Bacterium', 'Streptomyces')) == len(genus)
    species = feature.load_atlas(taxon_level='species', taxon='Streptomyces coelicolor')
    assert len(species) and species['genus'].eq('Streptomyces').all()
    with pytest.raises(ValueError):
        feature.load_atlas(taxon_level='tribe', taxon='x')


def test_set_filter_requires_and_excludes():
    from app.plotdata.feature import SetFilter, _build_filters
    rule = SetFilter(frozenset({'A'}), frozenset({'C'}))
    assert rule.matches('A B')
    assert not rule.matches('A C')
    assert not rule.matches('B')
    built = _build_filters({'groups': {'required': [], 'excluded': ['Blanks']}})
    assert list(built.values())[0].matches('A') and not list(built.values())[0].matches('A Blanks')
    assert _build_filters({'groups': {'required': [], 'excluded': []}}) == {}
