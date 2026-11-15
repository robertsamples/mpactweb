# Architecture

MPACT Web is a FastAPI server that runs the MPACT analysis pipeline, plus a
static JavaScript front end that draws the results with Plotly. The pipeline
itself is the desktop application's code, copied without changes.

## The vendored pipeline

The desktop repository's `code/` directory has two kinds of modules: those
that depend on Qt and those that do not.

| Qt-free (copied) | Qt-dependent (replaced) |
|---|---|
| `MSFaST.py`, `filter.py`, `stats.py`, `groupsets.py` | `main.py`, `ui_*.py` |
| `ordination.py`, `pvclust.py`, `clusterpurity.py`, `qualityscore.py`, `treefilters.py`, `biogroups.py`, `dbsearch.py` | `plotting.py` |
| `translators.py`, `getfragdb.py`, `mspwriter.py`, `csvcache.py`, `mzmineimport.py` | `dialogs.py`, `searchtree.py`, `svgloader.py`, `files*.py` |

The Qt-free modules are copied into `server/mpact_core/` and pinned by hash in
`VENDOR_MANIFEST.json`. `scripts/vendor_core.py verify` checks the hashes in CI
and during the Docker build, so a local edit to a vendored file fails the
build. Changes go upstream first and are then synced.

`run_MSFaST(params)` uses only pandas, NumPy and the filesystem, and the
compute parts of several plots (`ordination`, `clusterpurity`,
`qualityscore`) were already separate modules. Only the Qt-dependent modules
in the right-hand column needed new code.

## Where the analysis runs

The analysis runs on the server. Running it in the browser with Pyodide was
not practical:

- `fastcluster` and `epam.indigo` are native extensions with no Pyodide builds.
- The NPAtlas reference table is about 33 MB.
- `run_MSFaST` writes and re-reads CSV files between stages, so it needs a
  real filesystem.

Uploaded files go into a per-workspace library and are copied into each run.
A user can run the same peak table again with different settings without
uploading it again, and deleting a library file does not affect runs that
used it.

## Request flow

```
browser ── upload ──▶ library/<file id>/          (checked on arrival)
        ── run ─────▶ runs/<run id>/rawdata/      (copies, converted in place)
                          │
                          ▼  child process
                      run_MSFaST(params)          (vendored)
                          │
                          ▼
                      output/<peak table stem>/   (same layout as the desktop)
                          │
        ◀── JSON ────  app/plotdata/*             (reads those files)
        ◀── files ───  downloads, including .mpct
```

## Run processes

Each analysis runs in its own spawned process:

- `MSFaST`, `filter` and `stats` share parameters through a module-level
  `global analysis_params`, so two runs in one interpreter would overwrite each
  other's settings.
- The work is CPU-bound and must stay off the server's event loop.
- `pvclust` starts its own worker pool for bootstrap support, which a
  daemonic worker process cannot do.

The child process writes `status.json` and copies its stdout and stderr to
`log.txt`. The Run view streams that log, and a status request still works
after the server restarts. `app/jobs.py` queues runs and limits how many run
at once (`MPACT_MAX_CONCURRENT_JOBS`).

## Parameters

`app/params.py` builds the pipeline's parameter object from the web form. It
follows `MainWindow.enumerate_inputs` in the desktop app line by line, in the
same order. `app/schemas.py` holds the desktop's default widget values. Points
that are easy to get wrong, all covered by `tests/test_params.py`:

- With no Plot Feature Sets defined, one is created through `GroupSetModel`:
  "Features not in blanks" when blank filtering is on, otherwise
  "All features".
- The enabled filters are added to every feature set's exclusion list as
  graph filters, in the order `cv`, `rel`, `insource`.
- The output directory is `<output>/<peak table stem>`, with a `plots/`
  subdirectory. Every path written by the pipeline or read by the results
  layer is derived from it.
- The isotope window sets both `isopeakwin` and `dimerpeakwin`. `grpave` and
  `prperr` are always on, since the desktop has no control for either.

## Plots

`plotting.py` draws matplotlib figures into Qt widgets and cannot be reused.
Its work is split in two:

- `app/plotdata/` builds each plot's data from the run's output files, calling
  the vendored modules: `ordination` (`load_ordination_matrix`, `run_pca`,
  `run_nmds`, `run_plsda`, `similarity_matrix`, `top_loadings`),
  `pvclust.PvClust`, `clusterpurity` (`purity_link_color_func`,
  `purity_summary`), `qualityscore.compute_cv_quality`, and `treefilters` for
  the feature table's column filters.
- `web/src/plots/` draws the data with Plotly.

The browser only reimplements code that came from plotting libraries on the
desktop: seaborn's row scaling (`standard_scale=0`), the squarified treemap
layout, and the UpSet matrix. `tests/test_vendor.py` checks that every vendored
function the plot layer calls still exists.

## Differences from the desktop

| Desktop | Web | Reason |
|---|---|---|
| Fixed axis ranges on m/z vs RT (0 to 11.5 min, 0 to 1850 m/z) | Ranges fit the data | The fixed ranges cut off late-eluting and heavy features. The desktop values are still sent as `desktop_range`. |
| Volcano thresholds applied by redrawing | Applied in the browser | No server round trip when a threshold changes. |
| `PvClust(..., parallel=True)` | `parallel=False` | Runs from different users already compete for cores. The result is the same. |
| NPAtlas search when Feature Info first opens | The same, plus **Re-search MS1** | Not run at the end of every analysis, since it loads a 33 MB table and many runs never use it. |
| Feature table held in a Qt model | Filtered and sorted on the server; rows fetched as they scroll into view, and only visible rows rendered | Large datasets have tens of thousands of rows. The whole list is still reachable with the arrow keys and range selection. |
| One highlighted feature | A multiple selection with one focused feature | Needed to mark several features on a plot and to export a chosen set. |
| Heatmap draws every row | Above `MPACT_MAX_HEATMAP_ROWS`, adjacent rows are averaged for display | More rows than pixels only slows drawing. The feature list and row dendrogram still include every feature. |
| "Collapse Replicates" checkbox on the ordination | **Points:** Individual Injections or Biological Replicates | Same two states, named for what they show. |
| Labels in the main window plus a Details dialog | A details panel beside every plot | Shows a picked feature without changing tabs. |
| Fixed-size plot canvases | Plots resize with the window | |

## Demo dataset

`server/data/demo/` holds the PTY087I2 study: peak table, sample list,
metadata and MS2 fragment database. Loading it copies the files into the
user's library, after which they behave like any upload. Loading the same
dataset twice does not create duplicates.

## Front end

The front end is plain ES modules served as static files, with no build step.
Plotly loads from a CDN and the JSME structure editor from the JSME site,
unless `scripts/fetch_web_vendor.py` has put local copies in `web/vendor/`.

State is held in one store with subscriptions (`web/src/state.js`). Selecting
a feature sets one key in the store, and the table, the plots and the details
panel each update from it. This replaces the desktop's `highlight_feature`
signal.

The colours match the desktop's Qt stylesheets: shell `rgb(35,35,35)`, panels
`rgb(40,40,40)`, controls `rgb(75,75,75)`, text `rgb(212,212,212)`.

## Tests

Server tests live in `server/tests/`:

- `test_pipeline_e2e.py` uploads a real dataset through the HTTP API, runs the
  pipeline, and checks that every plot payload builds and is consistent:
  array lengths match, matrix shapes match their labels, and dendrogram
  colours match their segments. Nothing is mocked.
- `test_params.py` covers the parameter mapping described above.
- `test_vendor.py` checks the vendored hashes, that no Qt import reaches the
  server, that only `app/core.py` imports vendored modules by their flat names,
  and that the vendored functions the web layer calls keep their signatures.

There are no automated browser tests. Front-end changes are checked by running
the app in a browser.
