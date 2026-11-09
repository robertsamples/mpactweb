"""Single import gateway to the vendored MPACT desktop core.

The modules in ``server/mpact_core`` are copied byte-for-byte from the
desktop repo (see ``scripts/vendor_core.py``), and they import each other
*flat* -- ``import filter``, ``from csvcache import cached_read_csv``,
``from groupsets import normalize_graphfilters``. Making them importable
without editing a single line therefore means putting that directory on
``sys.path`` rather than turning it into a package.

That happens exactly once, here, and only for the duration of the eager
import block below: every vendored module is imported up front, which binds
all of them in ``sys.modules``, and then the path entry is removed again. A
lazy ``import translators`` inside a vendored function (mzmineimport.py does
this) still resolves, because ``sys.modules`` is consulted before the path
is ever searched -- so the flat names stay working for the vendored code
without leaving generic names like ``filter``/``stats`` shadowing anything
for the rest of the process.

Import the core through this module (``from app.core import MSFaST``), never
by re-adding the vendor directory somewhere else.
"""

import os
import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent.parent / 'mpact_core'

# matplotlib is pulled in transitively (pvclust imports pyplot at module
# scope). Force the headless backend before that happens -- on a server
# there is no display, and the default backend selection can otherwise try
# to load a GUI toolkit and fail at import time.
os.environ.setdefault('MPLBACKEND', 'Agg')

if not VENDOR_DIR.is_dir():  # pragma: no cover - deployment sanity check
    raise RuntimeError(
        f'vendored MPACT core not found at {VENDOR_DIR}; '
        'run "python scripts/vendor_core.py sync --src <path to mpact repo>"')

_inserted = str(VENDOR_DIR)
sys.path.insert(0, _inserted)
try:
    # --- the analysis pipeline itself, called unmodified -------------------
    import MSFaST                      # noqa: E402  run_MSFaST, analysis_parameters
    import filter as ionfilter         # noqa: E402  aliased: shadows the builtin name
    import stats as mpact_stats        # noqa: E402  aliased: reads as scipy.stats otherwise
    import groupsets                   # noqa: E402  GroupSet, GroupSetModel, build_query_dict

    # --- Qt-free compute the desktop app's plot layer calls ---------------
    import ordination                   # noqa: E402
    import pvclust                      # noqa: E402
    import clusterpurity                # noqa: E402
    import qualityscore                 # noqa: E402
    import treefilters                  # noqa: E402
    import biogroups                    # noqa: E402
    import dbsearch                     # noqa: E402

    # --- import/export translators and IO helpers -------------------------
    import translators                  # noqa: E402
    import getfragdb                    # noqa: E402
    import mspwriter                    # noqa: E402
    import csvcache                     # noqa: E402
    import mzmineimport                 # noqa: E402
    import npatlasupdate                # noqa: E402  atlas download (stdlib only)
finally:
    # Remove our entry (not sys.path[0] by index -- a plugin/debugger could
    # have inserted ahead of us in the meantime).
    try:
        sys.path.remove(_inserted)
    except ValueError:  # pragma: no cover
        pass

# npatlassearch imports epam.indigo for 2D structure rendering, which is an
# optional server dependency (it ships native libraries). Structure images
# degrade to "unavailable" rather than breaking the whole app.
indigo_available = False
try:  # pragma: no cover - depends on optional dependency
    from indigo import Indigo
    from indigo.renderer import IndigoRenderer
    indigo_available = True
except Exception:  # noqa: BLE001 - any import/native-load failure is "unavailable"
    Indigo = IndigoRenderer = None

# RDKit is the preferred structure renderer: its CoordGen layout draws
# macrocycles as polygons at ~120 degree bond angles, where Indigo (and
# RDKit's own default depictor) inflate a large ring into a near-circle.
rdkit_available = False
try:  # pragma: no cover - depends on optional dependency
    from rdkit import Chem as _Chem  # noqa: F401
    from rdkit.Chem import rdCoordGen as _rdCoordGen  # noqa: F401
    from rdkit.Chem.Draw import rdMolDraw2D as _rdMolDraw2D  # noqa: F401
    rdkit_available = True
except Exception:  # noqa: BLE001
    pass

structures_available = rdkit_available or indigo_available


__all__ = [
    'MSFaST', 'ionfilter', 'mpact_stats', 'groupsets',
    'ordination', 'pvclust', 'clusterpurity', 'qualityscore', 'treefilters',
    'biogroups', 'dbsearch',
    'translators', 'getfragdb', 'mspwriter', 'csvcache', 'mzmineimport',
    'npatlasupdate',
    'Indigo', 'IndigoRenderer', 'indigo_available',
    'rdkit_available', 'structures_available',
    'VENDOR_DIR',
]
