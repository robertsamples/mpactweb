"""The bundled demo datasets.

PTY087I2: a *Streptomyces* elicitor study -- a solvent blank, a media
control, and two cerium-treated conditions, in technical triplicate. It is
the dataset MPACT's own documentation and tests use, and it comes with its
sample list and metadata, so a first-time visitor can run a real analysis
(real filters, real group separation, real volcano) straight away.

CHM350: the course's MS-DIAL alignments, one per student: the peak table,
its MS/MS spectra, and the sample list and metadata that File Selection >
Create... makes from the injection names (scripts/build_demo_sheets.mjs runs
the editor's own rules). Loading one also sets the analysis up the way the
course reads it -- an Eu condition against its growth control, and two Plot
Feature Sets: "All features" (anything not in the solvent or media blank)
and "Elicitor only" (also not in a growth control). The peak tables are
lean copies made by scripts/build_demo_msdial.py -- identical analysis,
half the size -- and are stored gzipped.

Loading a dataset copies its files into the caller's session library
exactly as an upload would, so from that point on the demo behaves like any
other dataset: re-selectable, re-runnable with different parameters,
deletable.
"""

import csv
import gzip
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import config


def _dir():
    """Resolved at call time, not import time, so config.reload() (and a
    test pointing somewhere else) is respected."""
    return Path(config.DEMO_DIR)


@dataclass(frozen=True)
class Dataset:
    id: str
    name: str
    description: str
    # (library kind, path under the demo directory). A ``.gz`` file is
    # stored compressed and lands in the library without the suffix.
    files: tuple
    summary: str = ''
    groups: tuple = ()
    suggested: dict = field(default_factory=dict)
    # groups -> {'settings': {...}, 'groupsets': [...]} applied on load.
    preset: object = None
    # 'public': anyone. 'internal': internal accounts only (see accounts.py);
    # without email accounts everyone is internal.
    audience: str = 'public'

    def visible_to(self, role):
        return self.audience == 'public' or role == 'internal'

    def missing(self):
        return [name for _, name in self.files if not (_dir() / name).is_file()]

    def available(self):
        return not self.missing()

    def kinds(self):
        return [kind for kind, _ in self.files]

    def groups_in_metadata(self):
        """Biological groups in the dataset's metadata file, first-seen order."""
        name = next((name for kind, name in self.files if kind == 'metadata'), None)
        if name is None or not (_dir() / name).is_file():
            return []
        with open(_dir() / name, newline='', encoding='utf-8-sig') as handle:
            groups = [row.get('Biological_Group', '').strip() for row in csv.DictReader(handle)]
        return list(dict.fromkeys(group for group in groups if group))

    def describe(self):
        preset = self.preset(self.groups_in_metadata()) if self.preset and self.available() else None
        return {
            'id': self.id, 'name': self.name, 'description': self.description,
            'summary': self.summary, 'kinds': self.kinds(),
            'groups': list(self.groups), 'suggested': dict(self.suggested),
            'available': self.available(), 'missing': self.missing(),
            'peaktable': next((library_name(name) for kind, name in self.files
                               if kind == 'peaktable'), None),
            'preset': preset,
        }


PTY087I2 = Dataset(
    id='pty087i2',
    name='PTY087I2',
    description='Cerium elicitation',
    # The peak table is listed first because it is the slow one.
    files=(
        ('peaktable', '200826_PTY087I2codingdataset.csv'),
        ('samplelist', 'samplelist.csv'),
        ('metadata', 'extractmetadata.csv'),
        ('fragments', '200826_PTY087I2codingdataset.msp'),
    ),
    summary=('A four-group Streptomyces elicitor study in technical triplicate: '
             'solvent blank, media control, 0 µM and 250 µM cerium. ~4,950 '
             'features across 30 injections, with an MS2 fragment database.'),
    groups=('Blanks', '0um_Ce', '250um_Ce', 'Media'),
    suggested={'blank': 'Blanks', 'experimental': '0um_Ce', 'control': '250um_Ce'},
)


BLACK = '#000000'
RED = '#d62728'      # the editor's first feature-set colour


def _before(group, condition):
    """The part of a group name before its ``condition`` token ('CA' for
    CA_Eu_high and 'eu'; '' for Eu_high), or None if it has no such token."""
    tokens = [t for t in re.split(r'[_\-\s]+', group) if t]
    lowered = [t.lower() for t in tokens]
    if condition not in lowered:
        return None
    return '_'.join(tokens[:lowered.index(condition)])


def chm350_preset(groups):
    """How the course reads a dataset, from its group names.

    Experimental: the first Eu condition; control: the growth control (GC)
    of the same culture. "All features" leaves out only the solvent blank
    and the media blank; "Elicitor only" also leaves out the growth
    controls. A dataset run without blanks gets blank filtering off. Media
    blanks are left out of sample normalization if that is turned on.
    """
    blank = next((g for g in groups if 'blank' in g.lower()), '')
    media = [g for g in groups if 'media' in g.lower()]
    controls = [g for g in groups if _before(g, 'gc') is not None]
    europium = [g for g in groups if _before(g, 'eu') is not None]
    experimental = europium[0] if europium else ''
    culture = _before(experimental, 'eu') if experimental else None
    control = next((g for g in controls if _before(g, 'gc') == culture),
                   controls[0] if controls else '')
    background = [g for g in groups if g == blank or g in media]
    keep = [g for g in groups if g not in background]
    return {
        'settings': {'blnkfltr': bool(blank), 'blnkgrp': blank,
                     'expgrp': experimental, 'ctrgrp': control,
                     # Off unless sample normalization is turned on: the
                     # media blank is not a diluted culture.
                     'samplenorm_skip': media},
        'groupsets': [
            {'name': 'All features', 'colour': BLACK,
             'src': keep, 'incl': [], 'excl': background},
            {'name': 'Elicitor only', 'colour': RED,
             'src': [g for g in keep if g not in controls], 'incl': [],
             'excl': [g for g in groups if g in background or g in controls]},
        ],
    }


def _chm350(initials):
    stem = f'chm350/CHM350_{initials}'
    return Dataset(
        id=f'chm350-{initials.lower()}',
        name=initials,
        description='CHM350 Dataset',
        files=(('peaktable', f'{stem}.txt.gz'),
               ('samplelist', f'{stem}_samplelist.csv'),
               ('metadata', f'{stem}_metadata.csv'),
               ('fragments', f'{stem}.msp.gz')),
        summary='CHM350 MS-DIAL dataset',
        preset=chm350_preset,
        # The class's own data.
        audience='internal',
    )


DATASETS = (PTY087I2,) + tuple(_chm350(x) for x in ('AS', 'CP', 'JM', 'MJS', 'ML', 'PK', 'SAS'))
DEFAULT = PTY087I2.id


def get(dataset_id=None):
    """A dataset by id (the default one for ``None``); ``KeyError`` if unknown."""
    wanted = dataset_id or DEFAULT
    for dataset in DATASETS:
        if dataset.id == wanted:
            return dataset
    raise KeyError(wanted)


def library_name(filename):
    """The name a demo file has once it is in a library."""
    name = Path(filename).name
    return name[:-3] if name.endswith('.gz') else name


def source_path(filename):
    """Resolve a demo filename to its path, refusing anything outside the
    demo directory (these names come from a fixed table, but the check keeps
    that guarantee local rather than assumed)."""
    root = _dir().resolve()
    path = (root / filename).resolve()
    if not path.is_file() or root not in path.parents:
        raise FileNotFoundError(f'{filename} is not part of the demo data')
    return path


def copy_into(filename, target):
    """Copy a demo file to ``target``, decompressing a ``.gz`` one."""
    source = source_path(filename)
    if source.name.endswith('.gz'):
        with gzip.open(source, 'rb') as packed, open(target, 'wb') as out:
            shutil.copyfileobj(packed, out, 1 << 20)
    else:
        shutil.copy2(source, target)
