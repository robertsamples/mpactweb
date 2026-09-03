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

import json
import threading
from pathlib import Path

import numpy as np
import pandas as pd

_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_LIMIT = 24


def load_csv(path, **kwargs):
    """Cached ``pd.read_csv``, keyed by (path, mtime, size, read options).

    Returns a copy, so a caller mutating the frame (adding a ``logfc``
    column, filtering rows) can't corrupt the cached original.
    """
    path = Path(path)
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size,
           tuple(sorted((k, str(v)) for k, v in kwargs.items())))
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit is None:
        hit = pd.read_csv(path, **kwargs)
        with _CACHE_LOCK:
            if len(_CACHE) >= _CACHE_LIMIT:
                # Plain FIFO eviction: the access pattern here is "the current
                # run's handful of files", so recency ranking buys nothing.
                for stale in list(_CACHE)[:len(_CACHE) - _CACHE_LIMIT + 1]:
                    _CACHE.pop(stale, None)
            _CACHE[key] = hit
    return hit.copy()


def clear_cache():
    with _CACHE_LOCK:
        _CACHE.clear()


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

    def iondict(self):
        """Per-feature table for *all* features, filtered or not."""
        return load_csv(self.require(self.iondict_path), sep=',', header=[0], index_col=None)

    def load_iondict_indexed(self):
        """``iondict`` indexed by Compound.

        ``qualityscore.compute_cv_quality`` is vendored unmodified and its
        ``reset_index()`` bookkeeping assumes the Compound-indexed shape the
        desktop hands it, so this reader exists to produce exactly that.
        """
        return load_csv(self.require(self.iondict_path), sep=',', header=0, index_col=0)

    def filtered(self, header=(0, 1, 2), index_col=0):
        """The post-filter peak table.

        ``header=[0,1,2]`` gives the Biolgroup/Sample/Injection MultiIndex
        columns; ``header=[2]`` gives injection names only (what the heatmap
        and dendrogram use).
        """
        return load_csv(self.require(self.filtered_path), sep=',',
                        header=list(header), index_col=index_col)

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

    def summary(self):
        """Wide-format group statistics (average / RSD / combASD / neff)."""
        return load_csv(self.require(self.summary_path), sep=',', header=[0, 1], index_col=[0])

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
