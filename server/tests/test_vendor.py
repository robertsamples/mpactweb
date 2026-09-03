"""Guards on the vendored-core boundary.

The project's central claim is that the desktop pipeline runs here
unmodified. These tests fail if that stops being true -- either because a
vendored file was edited in place, or because the web layer started
reimplementing something the core already does.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = REPO_ROOT / 'server'
VENDOR_DIR = SERVER_DIR / 'mpact_core'
APP_DIR = SERVER_DIR / 'app'

sys.path.insert(0, str(SERVER_DIR))


def test_vendored_files_match_the_manifest():
    """No vendored file has been edited since it was copied from upstream."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / 'scripts' / 'vendor_core.py'), 'verify'],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_core_imports_cleanly():
    """Every vendored module imports, and the flat names it needs are gone
    from sys.path afterwards (app.core removes its entry once loaded)."""
    from app import core

    for name in ('MSFaST', 'ionfilter', 'mpact_stats', 'groupsets', 'ordination',
                 'pvclust', 'clusterpurity', 'qualityscore', 'treefilters',
                 'biogroups', 'dbsearch', 'translators', 'getfragdb',
                 'mspwriter', 'csvcache', 'mzmineimport', 'npatlasupdate'):
        assert hasattr(core, name), f'app.core does not expose {name}'

    assert str(core.VENDOR_DIR) not in sys.path, \
        'the vendor directory was left on sys.path, shadowing generic module names'


def test_pipeline_entry_points_have_the_expected_shape():
    """The web layer calls these by name; a signature change upstream should
    surface here rather than mid-run."""
    import inspect

    from app.core import MSFaST, biogroups, groupsets, qualityscore

    assert list(inspect.signature(MSFaST.run_MSFaST).parameters) == ['params']
    assert list(inspect.signature(groupsets.build_query_dict).parameters) == \
        ['model', 'graphfilters']
    assert list(inspect.signature(biogroups.compute_biological_groups).parameters) == \
        ['extractmetadatafilename', 'samplelistfilename', 'filename']
    assert list(inspect.signature(qualityscore.compute_cv_quality).parameters) == \
        ['iondict', 'average_n']


def test_no_qt_dependency_anywhere_in_the_server():
    """A PyQt import would mean rendering logic leaked into the server, which
    can't work headlessly and isn't the plan."""
    offenders = []
    for path in list(VENDOR_DIR.rglob('*.py')) + list(APP_DIR.rglob('*.py')):
        text = path.read_text(encoding='utf-8')
        if 'PyQt5' in text or 'QtWidgets' in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f'Qt imports found in: {offenders}'


def _imported_names(path):
    """Top-level module names imported by a file."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split('.')[0])
    return names


VENDORED_MODULE_NAMES = {p.stem for p in VENDOR_DIR.glob('*.py')}


def test_app_reaches_the_core_only_through_app_core():
    """Vendored modules are importable by flat name only while app.core is
    loading. Anywhere else, a bare ``import filter`` would either fail or --
    worse -- pick up an unrelated module of the same name."""
    offenders = []
    for path in APP_DIR.rglob('*.py'):
        if path.name == 'core.py':
            continue
        leaked = _imported_names(path) & VENDORED_MODULE_NAMES
        if leaked:
            offenders.append(f'{path.relative_to(REPO_ROOT)}: {sorted(leaked)}')
    assert not offenders, ('these modules import vendored code directly instead of '
                           f'through app.core: {offenders}')


@pytest.mark.parametrize('module,attribute', [
    ('ordination', 'load_ordination_matrix'),
    ('ordination', 'run_pca'),
    ('ordination', 'run_nmds'),
    ('ordination', 'run_plsda'),
    ('ordination', 'similarity_matrix'),
    ('ordination', 'top_loadings'),
    ('ordination', 'replicate_label_components'),
    ('clusterpurity', 'purity_link_color_func'),
    ('clusterpurity', 'purity_summary'),
    ('qualityscore', 'compute_cv_quality'),
    ('treefilters', 'row_passes'),
    ('treefilters', 'distinct_category_tokens'),
    ('dbsearch', 'search_npatlas'),
    ('translators', 'detect_peaktable_format'),
    ('translators', 'filter_source_peaktable'),
    ('translators', 'reindex_fragments'),
    ('pvclust', 'PvClust'),
])
def test_functions_the_plot_layer_depends_on_exist(module, attribute):
    """Each of these is called instead of being reimplemented in the web
    layer; losing one silently would mean a plot quietly diverging from the
    desktop's numbers."""
    from app import core
    assert hasattr(getattr(core, module), attribute)
