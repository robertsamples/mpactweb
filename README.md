# MPACT Web

A browser front end for [MPACT](https://github.com/robertsamples/mpact), the
desktop tool for processing and exploring untargeted mass-spectrometry /
natural-products metabolomics data.

Same inputs, same analysis, same outputs — a peak table (Progenesis QI,
MZmine, MS-DIAL or Bruker Metaboscape), a sample list and a metadata file go
in; a filtered, statistically annotated dataset and the full suite of
interactive plots come out. The difference is that nobody has to install
Anaconda first, and results are a link rather than a folder.

**The analysis pipeline is the desktop app's, used unmodified.**
`server/mpact_core/` is a byte-for-byte copy of MPACT's Qt-free modules, and
`scripts/vendor_core.py verify` (run in CI and at image build) fails if a
single one is edited. The web layer builds the pipeline's parameters, runs
it, and turns its output files into JSON for the browser — it never
reimplements the science.

---

## Quick start

```bash
pip install -r server/requirements.txt

# Optional: the Natural Products Atlas table (~33MB) for database matching.
python scripts/fetch_reference_data.py

# Optional: vendor Plotly locally for an air-gapped deployment.
python scripts/fetch_web_vendor.py

cd server && uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>.

### Docker

```bash
docker compose up --build
docker compose run --rm mpactweb python /app/scripts/fetch_reference_data.py
```

---

## Using it

The workflow is the desktop's, tab for tab:

1. **File Selection** — upload a peak table, sample list, metadata file and
   (optionally) an MSP/MGF fragment database, or press **Load demo dataset**
   to fill all four slots with the bundled PTY087I2 example (a four-group
   *Streptomyces* cerium-elicitation study in technical triplicate). Each
   upload is inspected on arrival: the peak-table format is detected,
   required columns are checked, and the biological groups are derived so the
   group pickers can be filled. Uploads stay in a per-session library, so a
   second run with different parameters re-selects them instead of
   re-uploading.
2. **Filtering Settings** — mispicked peaks, blank filtering, CV
   (reproducibility) filtering, in-source fragment deconvolution.
3. **Analysis Settings** — fold change / t-test / FDR, which plots to
   generate, the colour-coded Plot Feature Sets, and the database-search
   window.
4. **Run** — the pipeline runs server-side in its own process, streaming its
   console output to the page so a long run isn't an opaque spinner.
5. **Results** — Data Review, Group Analysis, Dendrogram, Multivariate, m/z
   vs RT, Mass Defect, 3D Fold Change, Volcano, Heatmap, Feature Info,
   Downloads.

Every plot fills the window and carries a pan/zoom/save toolbar. Beside it is
a details rail — click a point and that feature's m/z, retention time, fold
change, CV, per-group abundance and MS2 spectrum appear without leaving the
tab. The rail's sections collapse individually, the boundary between it and
the plot is draggable, and the whole rail folds away for a full-width plot.

The selected feature and injection stay in the status bar along the bottom,
and selecting a feature anywhere — a scatter point, a heatmap cell, a table
row — selects it everywhere, the same cross-plot highlight the desktop has.

Feature Info lists every feature at once (no paging) with the detail pane
below it; the arrow keys walk the list.

Every output file the desktop writes is downloadable, **including the
`.mpct` session file**, so an analysis run in the browser can be opened in
the desktop app and vice versa. The GNPS2 exports (`_filtered_source` and
the re-indexed fragment database) are produced too.

---

## Why the analysis runs on the server

Three options were on the table: run it in the browser (Pyodide/WASM), run
it server-side from a direct upload, or upload files separately and then
select them (GNPS-style).

**Server-side, with a persistent upload library**, for two reasons:

- *In-browser was not tractable without rewriting the pipeline.* It depends
  on `fastcluster` and `epam.indigo` (native, not available in Pyodide), on
  a 33MB reference table, and on a real filesystem — `run_MSFaST` reads and
  rewrites CSVs at every stage by design. Making it run in WASM would mean
  rewriting the code this project is supposed to reuse verbatim.
- *The GNPS-style split is a storage decision, not a workflow.* So the app
  takes both: files upload once into a session library (re-selectable across
  runs, no re-upload for a parameter sweep), while the picker on top of it
  stays the desktop's four labelled slots.

Plot *rendering* is the one layer that could not be reused — `plotting.py`
is matplotlib embedded in Qt widgets. Plots are therefore drawn client-side
with Plotly, from data derived server-side by calling the same Qt-free
modules the desktop's plot classes call (`ordination`, `pvclust`,
`clusterpurity`, `qualityscore`, `treefilters`). Interactivity that needed a
round trip on the desktop — hover, zoom, live thresholds — is now local to
the browser.

See [`docs/architecture.md`](docs/architecture.md) for the details.

---

## Layout

```
server/
  mpact_core/     vendored MPACT modules — byte-identical, never edit here
  app/
    core.py       the single import gateway to the vendored core
    params.py     web equivalent of MainWindow.enumerate_inputs
    runner.py     child process that calls run_MSFaST
    jobs.py       queue + concurrency cap
    results.py    cached readers for a run's output files
    plotdata/     JSON builders — the web half of plotting.py
    routers/      HTTP surface
  tests/
web/              static front end (no build step)
scripts/          vendor sync, reference-data and browser-library fetch
```

---

## Development

```bash
pip install -r server/requirements-dev.txt
cd server && python -m pytest tests/ -q
```

`tests/test_pipeline_e2e.py` drives the real HTTP API through a real
analysis with a real dataset — no mocking of the pipeline. It looks for an
MPACT example dataset (a peak table plus `samplelist.csv` and
`extractmetadata.csv`) at `../mpact/rawdata/PTY087I2`, overridable with
`MPACT_TEST_DATA`, and skips if it isn't there.

### Keeping the core in sync

```bash
python scripts/vendor_core.py verify                    # CI check
python scripts/vendor_core.py sync --src ../mpact       # pull upstream changes
```

Fix bugs in the vendored modules **upstream**, then re-sync. An edit made
here fails `verify` and the Docker build.

---

## Configuration

All optional; every value has a working default.

| Variable | Default | Purpose |
|---|---|---|
| `MPACT_DATA_ROOT` | `server/var` | Sessions, uploads and run outputs |
| `MPACT_NPATLAS_PATH` | `server/data/npatlas.tsv` | NPAtlas table |
| `MPACT_MAX_CONCURRENT_JOBS` | `2` | Analyses running at once |
| `MPACT_JOB_TIMEOUT_SECONDS` | `3600` | Per-run time limit |
| `MPACT_MAX_UPLOAD_BYTES` | `536870912` | Upload ceiling (512MB) |
| `MPACT_SESSION_MAX_AGE_SECONDS` | `2592000` | Sessions pruned at startup (30 days) |
| `MPACT_SESSION_COOKIE_SECURE` | `false` | Turn on behind HTTPS |
| `MPACT_DEV` | `false` | Put exception detail in API responses |

The demo dataset ships in `server/data/demo/`. Remove that directory to hide
the **Load demo dataset** button; the server then reports it as unavailable
and the front end omits it.

### A note on deployment

Sessions are cookie-scoped directories, with no authentication — the same
trust model as running the desktop app on a shared workstation. Anyone who
can reach the server can upload files and start analyses. Put it behind your
own authentication before exposing it beyond a trusted network, and set
`MPACT_SESSION_COOKIE_SECURE=true` behind HTTPS.

---

## Licence

MPACT is GPLv3; this front end inherits that. See the upstream repository for
the full text and citation guidance.
