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

    Mirrors ``MainWindow.getgroups``: the first group whose name contains
    "blank" becomes the blank group, and the first two non-blank groups become
    the experimental and control groups.
    """
    blank, experimental, control = '', '', ''
    for group in groups:
        name = str(group)
        if 'blank' in name.lower():
            if not blank:
                blank = name
        elif not experimental:
            experimental = name
        elif not control:
            control = name
    return {'blank': blank, 'experimental': experimental, 'control': control}
