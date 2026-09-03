"""The bundled demo dataset.

PTY087I2: a *Streptomyces* elicitor study — a solvent blank, a media
control, and two cerium-treated conditions, in technical triplicate. It is
the dataset MPACT's own documentation and tests use, so a first-time visitor
can see a real analysis (real filters, real group separation, real volcano)
before preparing their own files.

Loading it copies the files into the caller's session library exactly as an
upload would, so from that point on the demo behaves like any other dataset:
re-selectable, re-runnable with different parameters, deletable.
"""

from pathlib import Path

from . import config

def _dir():
    """Resolved at call time, not import time, so config.reload() (and a
    test pointing somewhere else) is respected."""
    return Path(config.DEMO_DIR)

# (library kind, filename). Order matters only for the progress the caller
# reports; the peak table is listed first because it is the slow one.
DEMO_FILES = (
    ('peaktable', '200826_PTY087I2codingdataset.csv'),
    ('samplelist', 'samplelist.csv'),
    ('metadata', 'extractmetadata.csv'),
    ('fragments', '200826_PTY087I2codingdataset.msp'),
)

DESCRIPTION = {
    'name': 'PTY087I2 — cerium elicitation',
    'summary': ('A four-group Streptomyces elicitor study in technical '
                'triplicate: solvent blank, media control, 0 µM and 250 µM '
                'cerium. ~4,950 features across 30 injections, with an MS2 '
                'fragment database.'),
    'groups': ['Blanks', '0um_Ce', '250um_Ce', 'Media'],
    'suggested': {'blank': 'Blanks', 'experimental': '0um_Ce', 'control': '250um_Ce'},
}


def available():
    """True if every demo file is present on this server."""
    return all((_dir() / name).is_file() for _, name in DEMO_FILES)


def missing():
    return [name for _, name in DEMO_FILES if not (_dir() / name).is_file()]


def source_path(filename):
    """Resolve a demo filename to its path, refusing anything outside the
    demo directory (these names come from a fixed table, but the check keeps
    that guarantee local rather than assumed)."""
    root = _dir().resolve()
    path = (root / filename).resolve()
    if not path.is_file() or root not in path.parents:
        raise FileNotFoundError(f'{filename} is not part of the demo dataset')
    return path
