"""Read access to a completed run's outputs.

Every plot and table in the web UI is derived from the same files the desktop
app reads after a run (``iondict.csv``, ``<name>_filtered.csv``,
``<name>_summarydata.csv``, ``<name>_groupaverages.csv``, ``analysisinfo.txt``)
plus the two small JSON sidecars ``runner.py`` writes for state the pipeline
only returned in memory.

Reads go through ``load_csv``, an mtime-keyed cache: a plot tab switch or a
control change re-reads the same 3-header-row peak table repeatedly, and
parsing it is far more expensive than the plot maths on top. This is separate
from the vendored ``csvcache`` (which the pipeline uses and invalidates
wholesale); keying on mtime means a file rewritten by a re-run is picked up
without anyone having to remember to invalidate.
"""

import collections
import contextlib
import json
import threading
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, storage


class Memo:
    """A small thread-safe LRU of computed values.

    ``get(key, build)`` runs ``build`` at most once per key at a time: two
    requests that want the same expensive thing (a parsed peak table, an NMDS
    solution) share one computation instead of each doing it, which is what a
    tab switch during a slow load otherwise produces. ``size_of`` turns the
    cache into a byte budget on top of the entry limit (``max_bytes`` may be
    a callable, read at each insert, so ``config.reload()`` is respected); the
    newest entry is always kept, so one oversized value still caches.
    """

    def __init__(self, limit, max_bytes=None, size_of=None):
        self.limit = limit
        self.max_bytes = max_bytes
        self._size_of = size_of
        self._entries = collections.OrderedDict()     # key -> (value, size)
        self._building = {}                           # key -> lock held by the builder
        self._lock = threading.Lock()
        self._bytes = 0

    def get(self, key, build):
        while True:
            with self._lock:
                hit = self._entries.get(key)
                if hit is not None:
                    self._entries.move_to_end(key)
                    return hit[0]
                pending = self._building.get(key)
                if pending is None:
                    mine = self._building[key] = threading.Lock()
                    mine.acquire()
                    break
            # Someone else is building it: wait for them, then look again.
            with pending:
                pass
        try:
            value = build()
            self.put(key, value)
            return value
        finally:
            with self._lock:
                self._building.pop(key, None)
            mine.release()

    def put(self, key, value):
        size = int(self._size_of(value)) if self._size_of else 0
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._bytes -= old[1]
            self._entries[key] = (value, size)
            self._bytes += size
            budget = self.max_bytes() if callable(self.max_bytes) else self.max_bytes
            while len(self._entries) > 1 and (
                    len(self._entries) > self.limit
                    or (budget is not None and self._bytes > budget)):
                _, (_, dropped) = self._entries.popitem(last=False)
                self._bytes -= dropped

    def peek(self, key):
        with self._lock:
            hit = self._entries.get(key)
            if hit is None:
                return None
            self._entries.move_to_end(key)
            return hit[0]

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def __len__(self):
        return len(self._entries)


def _frame_bytes(frame):
    """Approximate memory held by a parsed table.

    ``memory_usage(deep=False)`` counts only the 8-byte pointer for each text
    cell, which undercounts a table read as text (``header=None``) about
    sevenfold, so the cache held several times its byte budget. Text columns
    are measured on a sample of about 200 rows and scaled up; counting every
    cell would add seconds to each parse of a large table.
    """
    try:
        total = int(frame.memory_usage(index=True, deep=False).sum())
        rows = len(frame)
        if rows:
            sample = frame.iloc[::max(1, rows // 200)].select_dtypes(include='object')
            if sample.shape[1]:
                extra = (sample.memory_usage(index=False, deep=True).sum()
                         - sample.memory_usage(index=False, deep=False).sum())
                total += int(extra * rows / len(sample))
        return total
    except Exception:  # noqa: BLE001 - sizing is advisory
        return 0


# Parsed output tables. Least-recently-used, with a byte budget: a class of
# students each browsing their own run touches seven or eight parses apiece
# (the filtered table alone is read under four header shapes), and a fixed
# handful of slots evicted in arrival order made them re-parse each other's
# files on every click.
_CACHE = Memo(limit=96, size_of=_frame_bytes,
              max_bytes=lambda: config.CSV_CACHE_BYTES)

# Files being rewritten in place by a request (the database search rewrites
# iondict.csv). Readers wait for the writer instead of parsing half a file.
_WRITERS = {}
_WRITERS_GUARD = threading.Lock()


@contextlib.contextmanager
def exclusive(*paths):
    """Hold these paths for rewriting: ``load_csv`` waits until it is done,
    and a second writer of the same file queues behind the first."""
    keys = sorted(str(Path(p)) for p in paths)
    locks = []
    with _WRITERS_GUARD:
        if len(_WRITERS) > 256:
            # One entry per file ever rewritten; drop the ones nobody holds
            # so a server that stays up for a term does not keep them all.
            for key, lock in list(_WRITERS.items()):
                if lock.acquire(blocking=False):
                    lock.release()
                    del _WRITERS[key]
        for key in keys:
            locks.append(_WRITERS.setdefault(key, threading.RLock()))
    for lock in locks:
        lock.acquire()
    try:
        yield
    finally:
        for lock in reversed(locks):
            lock.release()


def _wait_for_writer(path):
    with _WRITERS_GUARD:
        lock = _WRITERS.get(str(path))
    if lock is not None:
        with lock:
            pass


def file_key(path):
    """``(path, mtime, size)``: changes whenever the file is rewritten."""
    stat = Path(path).stat()
    return (str(path), stat.st_mtime_ns, stat.st_size)


def load_csv(path, copy=True, **kwargs):
    """Cached ``pd.read_csv``, keyed by (path, mtime, size, read options).

    Returns a copy, so a caller mutating the frame (adding a ``logfc``
    column, filtering rows) can't corrupt the cached original. A caller that
    only reads can pass ``copy=False`` and skip duplicating the whole table.
    """
    path = Path(path)
    options = tuple(sorted((k, str(v)) for k, v in kwargs.items()))
    frame = None
    for _ in range(3):
        _wait_for_writer(path)
        identity = file_key(path)
        frame = _CACHE.get(identity + (options,), lambda: pd.read_csv(path, **kwargs))
        # A file replaced while it was being parsed gives a frame that matches
        # neither version: read it again under its new identity.
        try:
            if file_key(path) == identity:
                break
        except OSError:
            break
    return frame.copy() if copy else frame


def clear_cache():
    _CACHE.clear()
    _DERIVED.clear()


# Small values derived from a run's files (id lists, solved ordinations),
# keyed by the identity of the file they came from.
_DERIVED = Memo(limit=64)


def derived(key, build):
    return _DERIVED.get(key, build)


# ``ordination.load_ordination_matrix(..., collapse_replicates=True)`` writes
# an ``averagepca.csv`` scratch file next to the peak table and reads it back.
# On the desktop only one plot is ever built at a time; here two requests for
# the same run (a tab switch mid-render, say) can interleave and one can read
# that file while the other is rewriting it. The vendored function cannot be
# changed, so every call that might write it is serialised instead.
ORDINATION_LOCK = threading.Lock()


class MissingResults(Exception):
    """Raised when a run's outputs aren't on disk (never ran, or wiped)."""


class RunResults:
    """Path resolution and typed readers for one run's output directory."""

    def __init__(self, run):
        self.run = run
        self.outputdir = run.outputdir()
        if self.outputdir is None or not self.outputdir.is_dir():
            raise MissingResults(f'no output directory for run {run.id}')
        peaktable = run.peaktable_path()
        request = run.read_request() or {}
        resolved = (request.get('resolved') or {}).get('peaktable_name')
        # The stem drives every output filename. Prefer the *converted* name
        # recorded by the runner (MS-DIAL .txt inputs become .csv), and fall
        # back to whatever is in rawdata/ for runs predating that record.
        self.stem = Path(resolved).stem if resolved else Path(peaktable).stem
        self.settings = (request.get('settings') or {})

    # --- paths ------------------------------------------------------------

    @property
    def iondict_path(self):
        return self.outputdir / 'iondict.csv'

    @property
    def filtered_path(self):
        return self.outputdir / f'{self.stem}_filtered.csv'

    @property
    def formatted_path(self):
        return self.outputdir / f'{self.stem}_formatted.csv'

    @property
    def summary_path(self):
        return self.outputdir / f'{self.stem}_summarydata.csv'

    @property
    def groupaverages_path(self):
        return self.outputdir / f'{self.stem}_groupaverages.csv'

    @property
    def analysisinfo_path(self):
        return self.outputdir / 'analysisinfo.txt'

    @property
    def mpct_path(self):
        return self.outputdir / f'{self.stem}.mpct'

    def require(self, path):
        path = Path(path)
        if not path.exists():
            raise MissingResults(f'{path.name} not found for this run')
        return path

    # --- sidecars ---------------------------------------------------------

    def _json(self, name, default):
        path = self.outputdir / name
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return default

    def meta(self):
        return self._json('runmeta.json', {})

    @property
    def flags_path(self):
        """Beside the run's request, not among its outputs: flags are the
        user's marks on a run, not something the pipeline wrote."""
        return self.run.root / 'flags.json'

    def flags(self):
        """``{compound id: '#rrggbb'}`` -- the features flagged in this run."""
        flags = storage.read_json(self.flags_path, default={})
        return flags if isinstance(flags, dict) else {}

    def groupsets(self):
        """The Plot Feature Sets used by this run, with their ion lists."""
        return self._json('groupsets.json', [])

    def params(self):
        path = self.run.root / 'params.json'
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def maxval(self):
        """``analysis_params.maxval``: the opacity denominator for
        abundance-tied point alpha on the m/z-vs-RT and mass-defect plots."""
        value = self.meta().get('maxval')
        if value:
            return float(value)
        iondict = self.iondict()
        return float(iondict['logmax'].max()) if 'logmax' in iondict else 1.0

    # --- tables -----------------------------------------------------------

    def iondict(self, copy=True):
        """Per-feature table for *all* features, filtered or not.

        ``copy=False`` hands back the cached frame itself, for callers that
        only read it."""
        return load_csv(self.require(self.iondict_path), copy=copy,
                        sep=',', header=[0], index_col=None)

    def feature_positions(self):
        """``{Compound id: row position in iondict}``, so one feature's row is
        a lookup rather than a scan of every id on each click."""
        path = self.require(self.iondict_path)

        def build():
            ids = self.iondict(copy=False)['Compound'].astype(str).tolist()
            positions = {}
            for position, compound in enumerate(ids):
                positions.setdefault(compound, position)    # first row wins
            return positions
        return derived(('feature-positions',) + file_key(path), build)

    def load_iondict_indexed(self):
        """``iondict`` indexed by Compound.

        ``qualityscore.compute_cv_quality`` is vendored unmodified and its
        ``reset_index()`` bookkeeping assumes the Compound-indexed shape the
        desktop hands it, so this reader exists to produce exactly that.
        """
        return load_csv(self.require(self.iondict_path), sep=',', header=0, index_col=0)

    def filtered(self, header=(0, 1, 2), index_col=0, copy=True):
        """The post-filter peak table.

        ``header=[0,1,2]`` gives the Biolgroup/Sample/Injection MultiIndex
        columns; ``header=[2]`` gives injection names only (what the heatmap
        and dendrogram use).
        """
        return load_csv(self.require(self.filtered_path), copy=copy, sep=',',
                        header=list(header), index_col=index_col)

    def filtered_ids(self):
        """Compound ids that survived filtering, in peak-table order.

        Asked for by the feature table on every filter keystroke, by the hit
        index and by the MS/MS search; kept as a tuple so none of them has to
        copy the whole filtered table just to read its index.
        """
        path = self.require(self.filtered_path)
        return derived(('filtered-ids',) + file_key(path), lambda: tuple(
            load_csv(path, copy=False, sep=',', header=[2], index_col=0).index.astype(str)))

    def surviving(self):
        """``filtered_ids`` as a set, for membership tests."""
        path = self.require(self.filtered_path)
        return derived(('surviving',) + file_key(path),
                       lambda: frozenset(self.filtered_ids()))

    def raw_header(self):
        """The peak table's 3 header rows, read raw and transposed.

        This exact shape (``header=None, index_col=[0,1,2]).iloc[:3].transpose()``)
        is what ``ordination.load_ordination_matrix`` and
        ``ordination.replicate_label_components`` expect -- they are vendored
        unmodified, so the caller has to hand them the shape they were
        written for.
        """
        header = load_csv(self.require(self.filtered_path), sep=',', header=None,
                          index_col=[0, 1, 2]).iloc[:3, :].transpose()
        header.columns = ['Biolgroup', 'Sample', 'Injection']
        return header

    def summary(self, copy=True):
        """Wide-format group statistics (average / RSD / combASD / neff)."""
        return load_csv(self.require(self.summary_path), copy=copy,
                        sep=',', header=[0, 1], index_col=[0])

    def groupaverages(self):
        return load_csv(self.require(self.groupaverages_path), sep=',', header=0, index_col=None)

    def analysisinfo(self):
        path = self.analysisinfo_path
        if not path.exists():
            return ''
        return path.read_text(errors='replace')

    # --- derived helpers used by several plots ---------------------------

    def biolgroups(self):
        """Biological groups in peak-table column order, de-duplicated.

        Matches how ``plot_upset`` derives them (first-seen order off the
        header row), not sorted -- plot legends and set order should follow
        the dataset, not the alphabet.
        """
        header = self.raw_header()
        seen = []
        for value in header['Biolgroup']:
            if value not in seen:
                seen.append(value)
        return seen

    def blank_group(self):
        params = self.params()
        if not params.get('blnkfltr'):
            return None
        return params.get('blnkgrp') or None

    def feature_sets(self, apply_blank_filter=True):
        """``[(set_meta, member_rows_of_iondict), ...]``.

        The desktop's ``filtereddfs``: each Plot Feature Set's rows of
        ``iondict``, optionally minus blank-filtered features -- every
        scatter plot drops those when blank filtering is on (see
        ``plot_mzrt``/``kendrick``/``plot_volcano``).
        """
        iondict = self.iondict()
        indexed = iondict.set_index('Compound', drop=False)
        result = []
        for entry in self.groupsets():
            ions = [ion for ion in entry.get('ions', []) if ion in indexed.index]
            rows = indexed.loc[ions] if ions else indexed.iloc[0:0]
            if apply_blank_filter and 'pass_blnkfil' in rows.columns:
                rows = rows[rows['pass_blnkfil'].astype(bool)]
            result.append((entry, rows.reset_index(drop=True)))
        return result

    def opacities(self, rows, maxval=None):
        """Abundance-tied point opacity: ``logmax / maxval``, clipped to [0,1].

        Same mapping the desktop bakes into per-point RGBA on the m/z-vs-RT
        and mass-defect plots; sent to the browser as a plain alpha array so
        the client can colour the points itself.
        """
        if maxval is None:
            maxval = self.maxval()
        if 'logmax' not in rows.columns or not maxval:
            return np.ones(len(rows))
        values = rows['logmax'].to_numpy(dtype=float) / float(maxval)
        return np.clip(np.nan_to_num(values, nan=0.0), 0.0, 1.0)


def clean(value):
    """JSON-safe scalar: NaN/inf become None (JSON has no literal for them)."""
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return None if (np.isnan(value) or np.isinf(value)) else value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, str):
        return value
    if pd.isna(value):
        return None
    return value


def column(rows, name, dtype=float):
    """A JSON-safe list for ``rows[name]``, or None if the column is absent."""
    if name not in rows.columns:
        return None
    series = rows[name]
    if dtype is float:
        values = pd.to_numeric(series, errors='coerce').to_numpy(dtype=float)
        return [None if (np.isnan(v) or np.isinf(v)) else float(v) for v in values]
    if dtype is bool:
        return [bool(v) for v in series.fillna(False).to_numpy()]
    return [clean(v) for v in series.to_numpy()]
