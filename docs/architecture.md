# Architecture

## The constraint that shaped everything

MPACT's analysis pipeline had to be reused **as-is**. That single constraint
decided most of what follows, so it is worth being precise about what "the
pipeline" is.

The desktop repository's `code/` directory splits cleanly in two:

| Qt-free (reused verbatim) | Qt-bound (not reusable) |
|---|---|
| `MSFaST.py`, `filter.py`, `stats.py`, `groupsets.py` | `main.py`, `ui_*.py` |
| `ordination.py`, `pvclust.py`, `clusterpurity.py`, `qualityscore.py`, `treefilters.py`, `biogroups.py`, `dbsearch.py` | `plotting.py` |
| `translators.py`, `getfragdb.py`, `mspwriter.py`, `csvcache.py`, `mzmineimport.py` | `dialogs.py`, `searchtree.py`, `svgloader.py`, `files*.py` |

Upstream had already done most of this separation — `run_MSFaST(params)` is
a pure pandas/numpy/file-IO function with no Qt anywhere, and several plot
computations (`ordination`, `clusterpurity`, `qualityscore`) had been pulled
out into testable Qt-free modules. That is what made a faithful port
possible rather than a rewrite.

Everything in the left column is copied into `server/mpact_core/` unchanged
and hash-pinned in `VENDOR_MANIFEST.json`. Only the right column had to be
replaced.

## Where the analysis runs, and why

Three designs were possible.

### In the browser (Pyodide / WASM) — rejected

The pipeline depends on `fastcluster` and `epam.indigo` (native extensions
with no Pyodide builds), on a 33MB reference table, and on a real
filesystem: `run_MSFaST` deliberately round-trips through CSVs between
stages, with a comment explaining that re-deriving one of those reshapes in
memory is exactly the class of subtle bug it is avoiding. Running it in WASM
would mean rewriting the code the project exists to reuse.

### Server-side, direct upload — chosen

The pipeline runs unmodified against a real filesystem. The user's browser
does no analysis, so a 4,000-feature study behaves the same on a laptop as
on a workstation.

### Upload-then-select (GNPS-style) — folded in

This is a storage decision rather than a different workflow, so it is layered
underneath rather than chosen instead: uploads land in a per-session library
and are *copied* into each run. A parameter sweep re-selects an already
uploaded peak table, and deleting a library file can't disturb a run in
flight. The picker on top stays the desktop's four labelled slots.

## Request flow

```
browser ── upload ──▶ library/<file id>/          (inspected on arrival)
        ── run ─────▶ runs/<run id>/rawdata/      (copies, converted in place)
                          │
                          ▼  spawned process
                      run_MSFaST(params)          ← vendored, unmodified
                          │
                          ▼
                      output/<peak table stem>/   ← the desktop's outputdir
                          │
        ◀── JSON ────  plotdata/*                 ← reads those same files
        ◀── files ───  downloads (incl. .mpct)
```

### Why each run gets its own process

Three reasons, any one of which would be sufficient:

1. `MSFaST`, `filter` and `stats` communicate through a module-level
   `global analysis_params`. Two concurrent runs in one interpreter would
   overwrite each other's parameters mid-pipeline. Process isolation makes
   that impossible without touching the vendored code.
2. The work is CPU-bound pandas/numpy and must not sit on the event loop.
3. `pvclust` starts its own worker pool for bootstrap dendrograms, which a
   daemonic worker could not do.

The child writes its own `status.json` and tees stdout/stderr to `log.txt`,
which is what lets the browser show real progress (the pipeline reports by
printing) and what lets a status poll still work after a server restart.

## Parameter mapping

`app/params.py` is the web equivalent of `MainWindow.enumerate_inputs`, and
is written to be read side by side with it: same order, same derived values,
same implicit defaults. `app/schemas.py` carries the desktop's Qt Designer
widget defaults, so a fresh web form and a fresh desktop launch agree.

The subtle parts, all pinned by `tests/test_params.py`:

- **The implicit default feature set.** A user who never opens the groupset
  editor gets "Features not in blanks" (blank filtering on) or "All features"
  (off) — reconstructed here through `GroupSetModel`, exactly as the desktop
  builds it.
- **Graph filters.** `['cv', 'rel', 'insource']`, in that order, merged into
  every feature set's exclusion list.
- **The output directory.** `<chosen>/<peak table stem>`, with a `plots/`
  subdirectory. Every path the pipeline writes, and every path the results
  layer later reads, derives from it.
- **Derived values.** One isotope-window control feeds both `isopeakwin` and
  `dimerpeakwin`; `grpave` and `prperr` are forced on (the desktop has no
  widget for either).

## Replacing the plot layer

`plotting.py` is matplotlib drawn into Qt widgets — the one part that could
not come across. It is replaced by two halves:

- `app/plotdata/` derives each plot's **data** from the run's output files,
  calling the vendored compute modules rather than reimplementing them:
  `ordination.load_ordination_matrix` / `run_pca` / `run_nmds` / `run_plsda`
  / `similarity_matrix` / `top_loadings`, `pvclust.PvClust`,
  `clusterpurity.purity_link_color_func` / `purity_summary`,
  `qualityscore.compute_cv_quality`, `treefilters` for the feature table's
  column filters.
- `web/src/plots/` **draws** it with Plotly.

Only genuinely presentational maths is reimplemented client-side, and only
where the original was a rendering library rather than analysis: seaborn's
`standard_scale=0` row scaling, the squarified treemap layout, and the UpSet
matrix. `tests/test_vendor.py` asserts that every core function the plot
layer leans on still exists, so losing one is a test failure rather than a
plot that quietly diverges.

### Deliberate differences from the desktop

| Desktop | Here | Why |
|---|---|---|
| Fixed axis ranges on m/z-vs-RT (0–11.5 min, 0–1850 m/z) and the quality view | Data-driven ranges | The fixed limits silently clip later-eluting or heavier features. The desktop values are still sent as `desktop_range`. |
| Volcano thresholds re-render server-side | Re-classified in the browser | Same rule, no round trip — dragging a threshold is instant. |
| `PvClust(..., parallel=True)` | `parallel=False` | Each request already competes for cores with other sessions; the bootstrap result is identical. |
| Hit counts filled when the Feature Info tab first opens | Explicit "Search NPAtlas" button | It costs seconds and loads a 33MB table; plenty of runs never need it. Same `dbsearch.search_npatlas` call. |
| Feature table held entirely in a Qt model | Unpaged, but windowed: filtering/sorting server-side, only the visible rows in the DOM | The whole list has to be reachable by arrow key, as it is on the desktop, but a full DOM table of tens of thousands of rows makes every keystroke stutter. Filtering still uses the vendored `treefilters` rules. |
| "Collapse Replicates" checkbox on the ordination tab | A named **Points** choice (Individual Injections / Biological Replicates) | Same two states and the same `load_ordination_matrix` call; a checkbox only says what it does if you already know what it collapses. The point count is printed under the plot so the state is verifiable. |
| Feature identity in main-window labels plus a Details dialog | A details rail beside every plot | It fills on click with identity, statistics and a per-group abundance chart, so a picked point is interpretable without changing tabs. |
| Fixed-size plot canvases | Plots fill the window and reflow on resize | A browser window is not a fixed-size Qt frame, and a fixed slab leaves dead space on a tall screen. |

## The demo dataset

`server/data/demo/` holds the PTY087I2 study (peak table, sample list,
metadata, MS2 fragment database) — the dataset MPACT's own documentation and
tests use. Loading it *copies* the files into the caller's session library,
so from that point it behaves exactly like an upload: re-selectable,
re-runnable with different parameters, deletable, and converted per-run
without touching the bundled originals. The endpoint is idempotent, so a
double click cannot fill the library with duplicates.

## Front end

No build step: ES modules served statically, Plotly from a CDN with a
local-vendor fallback that the page prefers when present. The UI is a fixed
set of panels mirroring the desktop's tabs, so a framework and a bundler
would cost more than they save.

State lives in one store with topic subscriptions (`web/src/state.js`). That
is what makes the cross-plot selection work: picking a feature on any plot
updates one store key, and every subscribed view — Feature Info, the
highlight marker on other tabs — reacts without knowing about each other.
It is the desktop's `highlight_feature` broadcast, expressed as a
subscription.

The palette is taken from the desktop's Qt stylesheets (shell `rgb(35,35,35)`,
panels `rgb(40,40,40)`, controls `rgb(75,75,75)`, text `rgb(212,212,212)`),
so the two apps read as the same application rather than as lookalikes.

## Testing

- `tests/test_pipeline_e2e.py` — uploads a real dataset over the real HTTP
  API, runs the real pipeline, and asserts every plot payload builds and is
  internally consistent (array lengths aligned, matrix shape matching its
  labels, dendrogram colours matching its segments). Nothing is mocked.
- `tests/test_params.py` — pins the `enumerate_inputs` mapping.
- `tests/test_vendor.py` — hash-verifies the vendored core, checks no Qt
  import leaked into the server, checks nothing outside `app/core.py`
  imports a vendored module by flat name, and checks the core entry points
  still have the shapes the web layer calls.

The UI itself was verified by driving a real browser through the whole
workflow — upload, run, every result tab, every switcher, cross-tab
selection — asserting no console errors, no HTTP errors, and no clipped
layout.
