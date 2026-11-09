"""Pre-run inspection of uploaded files.

The desktop can inspect a peak table cheaply because the user picked it off
their own disk and it is already in place. On the web the same information --
what format is this, which columns did we find, what biological groups will
the analysis see -- has to come back from the server before the user
configures anything, since the blank/experimental/control group pickers and
the Plot Feature Set editor are all populated from it.

Group derivation calls ``biogroups.compute_biological_groups`` unmodified.
That function expects the *canonical* three-header-row layout, so a non-
Progenesis peak table is converted into a cached sidecar copy first, using
the same ``mzmineimport.format_check`` the analysis run uses. The cached copy
is a preview artifact only: a run still converts its own copy in ``rawdata/``,
exactly as the desktop converts in place.
"""

import csv
import shutil
import traceback
from pathlib import Path

import pandas as pd

from .core import biogroups, mzmineimport, translators

MAX_PREVIEW_ROWS = 8


class _FormatCheckShim:
    """Duck-typed ``MainWindow`` for ``mzmineimport.format_check`` (see
    ``runner._FormatCheckShim`` -- same contract, kept separate so the
    preview path can collect its warnings independently)."""

    def __init__(self, filename):
        self.filename = Path(filename)
        self.messages = []

    def error(self, message):
        self.messages.append(str(message))


def _is_msdial_csv(path):
    """True for an MS-DIAL alignment export saved as comma-separated text."""
    try:
        with open(path, newline='', encoding='utf-8-sig', errors='replace') as handle:
            for index, row in enumerate(csv.reader(handle)):
                if any(cell.strip() == 'Alignment ID' for cell in row):
                    return True
                if index >= 8:
                    return False
    except (OSError, csv.Error):
        return False
    return False


def normalize_peaktable(path):
    """Rewrite an MS-DIAL ``.csv`` export as the tab-separated ``.txt`` the
    vendored ``reformat_msdial`` reads.

    MS-DIAL writes the same alignment table as .txt (tab) or .csv (comma); the
    desktop pipeline only reads the tab form, and the vendored core can't be
    edited, so the conversion happens here, once, at upload. Returns the new
    path, or ``None`` if the file needed nothing.
    """
    path = Path(path)
    if path.suffix.lower() != '.csv' or not _is_msdial_csv(path):
        return None
    target = path.with_suffix('.txt')
    with open(path, newline='', encoding='utf-8-sig', errors='replace') as source, \
            open(target, 'w', newline='', encoding='utf-8') as out:
        for row in csv.reader(source):
            out.write('\t'.join(cell.replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')
                                for cell in row))
            out.write('\n')
    path.unlink()
    return target


def canonical_peaktable(session, record):
    """A canonical-format copy of an uploaded peak table, converted on first
    use and cached beside the original.

    Returns ``(path, detected_format, warnings)``.
    """
    source = session.path_for(record)
    cache_dir = session.library_dir / record.dirname / 'canonical'
    detected = translators.detect_peaktable_format(source)

    if detected == translators.PROGENESIS:
        # Already the internal layout -- but format_check still normalises
        # duplicate compound ids for .csv inputs, which the analysis will do
        # too, so run it on a copy rather than reading the original directly.
        pass

    marker = cache_dir / '.format'
    if marker.exists():
        cached = next((p for p in cache_dir.glob('*') if p.is_file()
                       and p.name != '.format'), None)
        if cached is not None:
            return cached, marker.read_text().strip(), []

    cache_dir.mkdir(parents=True, exist_ok=True)
    working = cache_dir / source.name
    shutil.copy2(source, working)
    shim = _FormatCheckShim(working)
    mzmineimport.format_check(shim)
    marker.write_text(detected)
    return Path(shim.filename), detected, shim.messages


def peaktable_summary(session, record):
    """Format, dimensions and injection names for an uploaded peak table."""
    try:
        path, detected, warnings = canonical_peaktable(session, record)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the upload
        traceback.print_exc()
        return {'format': 'unknown', 'error': f'{type(exc).__name__}: {exc}'}

    try:
        header = pd.read_csv(path, sep=',', header=None, index_col=[0, 1, 2],
                             nrows=3, low_memory=False)
        injections = [str(v) for v in header.iloc[2]]
        # Row count without loading the abundance block: count lines and
        # subtract the three header rows.
        with path.open('rb') as handle:
            lines = sum(1 for _ in handle)
        return {
            'format': detected,
            'features': max(0, lines - 3),
            'injections': len(injections),
            'injection_names': injections[:MAX_PREVIEW_ROWS],
            'warnings': warnings,
        }
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return {'format': detected, 'error': f'could not read peak table: {exc}'}


def peaktable_injections(session, record):
    """Every injection (sample column) name in a peak table, in file order."""
    path, _, _ = canonical_peaktable(session, record)
    header = pd.read_csv(path, sep=',', header=None, index_col=[0, 1, 2],
                         nrows=3, low_memory=False)
    return [str(v) for v in header.iloc[2]]


REQUIRED_COLUMNS = {
    'samplelist': ('Sample_Code', 'Injection'),
    'metadata': ('Sample_Code', 'Biological_Group'),
}


def read_table(path):
    """``(columns, rows)`` of a small CSV, every cell as a string."""
    with open(path, newline='', encoding='utf-8-sig', errors='replace') as handle:
        rows = [row for row in csv.reader(handle)]
    while rows and not any(cell.strip() for cell in rows[-1]):
        rows.pop()
    if not rows:
        return [], []
    columns = rows[0]
    width = len(columns)
    body = [(row + [''] * width)[:width] for row in rows[1:] if any(c.strip() for c in row)]
    return columns, body


def write_table(path, kind, columns, rows):
    """Validate an edited sample list / metadata table and write it as CSV.

    Raises ``ValueError`` with a user-facing message. The checks are the
    ones whose failure the pipeline would otherwise report as an opaque
    "Data read failure": a missing key column, or a duplicated key (which the
    Sample_Code join turns into duplicated injections).
    """
    columns = [str(c).strip() for c in columns]
    if len(set(columns)) != len(columns):
        raise ValueError('column names must be unique')
    missing = [c for c in REQUIRED_COLUMNS[kind] if c not in columns]
    if missing:
        raise ValueError(f'missing required column(s): {", ".join(missing)}')
    width = len(columns)
    cleaned = []
    for row in rows:
        row = [str(cell).replace('\r', ' ').replace('\n', ' ').strip()[:1000]
               for cell in row]
        row = (row + [''] * width)[:width]
        if any(row):
            cleaned.append(row)

    key = 'Injection' if kind == 'samplelist' else 'Sample_Code'
    required = REQUIRED_COLUMNS[kind]
    seen = set()
    for row in cleaned:
        record = dict(zip(columns, row))
        for column in required:
            if not record[column]:
                raise ValueError(f'every row needs a {column}')
        if record[key] in seen:
            raise ValueError(f'{key} "{record[key]}" appears more than once')
        seen.add(record[key])

    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        writer.writerow(columns)
        writer.writerows(cleaned)
    return len(cleaned)


def table_summary(path, required_columns):
    """Columns, row count and a small preview for a sample list / metadata CSV."""
    try:
        frame = pd.read_csv(path, sep=',', header=[0], index_col=None)
    except Exception as exc:  # noqa: BLE001
        return {'error': f'could not read CSV: {exc}'}
    columns = [str(c) for c in frame.columns]
    missing = [name for name in required_columns if name not in columns]
    preview = frame.head(MAX_PREVIEW_ROWS).astype(str).to_dict('records')
    return {
        'columns': columns,
        'rows': int(len(frame)),
        'missing_columns': missing,
        'preview': preview,
    }


def fragments_summary(path):
    """Entry count and detected flavour of an MSP/MGF fragment database."""
    try:
        entries = translators.parse_fragments(path)
    except Exception as exc:  # noqa: BLE001
        return {'error': f'could not parse fragment file: {exc}'}
    return {'entries': len(entries), 'kind': Path(path).suffix.lower().lstrip('.')}


def inspect_file(session, record):
    """Per-kind inspection summary, stored on the library record."""
    path = session.path_for(record)
    if record.kind == 'peaktable':
        return peaktable_summary(session, record)
    if record.kind == 'samplelist':
        return table_summary(path, ('Sample_Code', 'Injection'))
    if record.kind == 'metadata':
        return table_summary(path, ('Sample_Code', 'Biological_Group'))
    if record.kind == 'fragments':
        return fragments_summary(path)
    return {}


def biological_groups(session, peaktable, samplelist, metadata):
    """The biological groups an analysis of these three files would see.

    Returns ``(groups, unresolved, warnings)``. Raises ``ValueError`` with the
    pipeline's own message when the metadata/sample-list join fails, which is
    the single most common input mistake.
    """
    canonical, _, warnings = canonical_peaktable(session, peaktable)
    groups, unresolved = biogroups.compute_biological_groups(
        session.path_for(metadata), session.path_for(samplelist), canonical)
    return groups, [str(u) for u in unresolved], warnings


def suggest_groups(groups):
    """Default blank / experimental / control selections.

    A faithful port of ``MainWindow.getgroups``'s combo-box seeding, which is
    subtler than it first looks because it assigns by *overwriting* as it
    walks the group list:

    - blank: the **last** group whose name contains "blank". When no group
      matches, the desktop's combo box is simply left at index 0, so the
      first group becomes the blank group -- reproduced here rather than
      leaving it empty, which would silently disable blank filtering on a
      dataset whose blank group is named something else.
    - experimental: the **first** non-blank group (it is only assigned once).
    - control: the **last** non-blank group (reassigned on every later pass).
    """
    names = [str(group) for group in groups]
    if not names:
        return {'blank': '', 'experimental': '', 'control': ''}

    blank = ''
    experimental = ''
    control = ''
    for name in names:
        if 'blank' in name.lower():
            blank = name
        elif not experimental:
            experimental = name
        else:
            control = name

    if not blank:
        blank = names[0]
    # A single non-blank group leaves control unset; fall back to the
    # experimental group so the pickers are never half-empty.
    if not control:
        control = experimental
    return {'blank': blank, 'experimental': experimental, 'control': control}
