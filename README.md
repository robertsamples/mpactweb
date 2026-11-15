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

# Optional: vendor Plotly and JSME locally for an air-gapped deployment.
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
   (optionally) an MSP/MGF fragment database, or pick a **demo dataset**: the
   PTY087I2 example fills all four slots (a four-group *Streptomyces*
   cerium-elicitation study in technical triplicate), and the CHM350 class
   alignments fill the peak table and fragment slots, leaving the sample list
   and metadata to be built with **Create…**. Each upload is inspected on
   arrival: the peak-table format is detected, required columns are checked,
   and the biological groups are derived so the group pickers can be filled.
   Uploads stay in a per-session library, so a second run with different
   parameters re-selects them instead of re-uploading.

   MS-DIAL alignment exports are read as `.txt` or `.csv`, from the 5.4 layout
   and the 5.5 one (five more annotation columns, and a `.csv` that leaves
   quoted compound names half-escaped); `server/app/msdial.py` rewrites them
   into the layout the vendored reader expects. A sample list and metadata
   sheet may cover more samples than the peak table — a class's composite
   sheets, say: each run uses only the rows its peak table needs, and repeated
   rows are merged rather than stopping the run.
2. **Filtering Settings** — mispicked peaks, blank filtering, CV
   (reproducibility) filtering, in-source fragment deconvolution.
3. **Analysis Settings** — the two groups the statistics compare, the
   colour-coded Plot Feature Sets, and the database-search window. Fold
   change, the t-test and FDR correction have no toggles: they are hidden on
   the desktop too, and always run.
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

Feature Info lists every feature at once (no paging) with the details rail
beside it; the arrow keys walk the list.

Selection is multiple wherever features are shown. In the table, click
selects one feature, Ctrl-click adds or removes one, Shift-click (or Shift
with the arrow keys) selects a range and Ctrl+A everything listed. On the
feature plots, Ctrl-click adds a point and the box and lasso tools select a
region (hold Shift to add to the selection). Every selected feature is marked
on every plot, so a set picked in the table can be shown on the volcano plot
for a figure. With more than one selected, the top of the details rail lists
the selection and exports it.

Two narrow columns lead the feature table. **Sel** ticks the selected
features (clicking a tick adds or removes that row), and **Flag** shows a
feature's flag: a lasting mark in a colour of your choosing, kept with the run.
Pick a colour and press **Flag** to mark the selected features, or click a
row's flag cell to mark just that one; clicking either column heading lists
those features first (flags grouped by colour), with the column sort still
applying within each part. With **Flags in plots** on, flagged features are
marked on the feature plots in their flag colours, the same way the selection
is marked in yellow -- several colours of highlight at once. Flags are also a
column in the export.

**Export CSV** on Feature Info saves the table as listed -- its filters, its
order -- and **Export selected** saves just the selection. Both carry every
per-feature column the run produced plus each feature's best NPAtlas and
MS/MS library match.

A run can be given a label on the Run view (or renamed later from **Runs**),
and **Reuse settings** in the run list puts a previous run's files, filters
and feature sets back in the form to change and run again. In the Plot
Feature Set editor, groups are picked the same way as table rows (Shift and
Ctrl for several) and moved together by dragging or with the buttons below
the lists.

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
| `MPACT_NPATLAS_SUPPLEMENTAL_PATH` | `server/data/npatlas_supplemental.tsv` | Compounds NPAtlas lacks (SIDERITE, lab compound list), searched together with NPAtlas |
| `MPACT_TAXONOMY_PATH` | `server/data/npatlas_taxonomy.tsv` | Genus lineage (NCBI) for the Feature Info taxon filter; rebuild with `scripts/build_hit_annotations.py --taxdump` |
| `MPACT_SIDERITE_KEYS_PATH` | `server/data/siderite_inchikeys.txt` | SIDERITE structures for the siderophore filter; rebuild with `scripts/build_hit_annotations.py --siderite` |
| `MPACT_MAX_CONCURRENT_JOBS` | `2` | Analyses running at once |
| `MPACT_JOB_TIMEOUT_SECONDS` | `3600` | Per-run time limit |
| `MPACT_MAX_UPLOAD_BYTES` | `536870912` | Upload ceiling (512MB) |
| `MPACT_SESSION_MAX_AGE_SECONDS` | `2592000` | Sessions pruned at startup and daily (30 days) |
| `MPACT_MAX_HEATMAP_ROWS` | `1500` | Rows drawn in the heatmap; past it, neighbouring features in the clustered order are averaged for display |
| `MPACT_CSV_CACHE_MB` | `256` | Memory for parsed output tables (least recently used are dropped) |
| `MPACT_PLOT_CACHE_MB` | `64` | Memory for finished plot payloads, so reopening a tab does not recompute it |
| `MPACT_FRAGDB_CACHE_ENTRIES` | `6` | Parsed fragment databases kept in memory, one per recently viewed run |
| `MPACT_SESSION_COOKIE_SECURE` | `false` | Turn on behind HTTPS |
| `MPACT_AUTH` | `none` | `none`, `code` or `users` (see below) |
| `MPACT_ACCESS_CODE` | | The shared code(s), comma-separated, when `MPACT_AUTH=code` (not case-sensitive) |
| `MPACT_USERS_FILE` | `<data root>/users.txt` | Accounts, when `MPACT_AUTH=users` |
| `MPACT_AUTH_MAX_AGE_SECONDS` | `1209600` | How long a sign-in lasts (14 days) |
| `MPACT_SECRET_KEY` | generated | HMAC key for login cookies |
| `MPACT_TRUST_PROXY` | `false` | Read `X-Forwarded-For` (only behind your own proxy) |
| `MPACT_DEV` | `false` | Put exception detail in API responses |

The demo datasets ship in `server/data/demo/` (the CHM350 ones as gzipped
lean copies under `chm350/`, built by `scripts/build_demo_msdial.py`). A
dataset whose files are missing is left out of the picker; remove the
directory to hide the demo panel altogether.

## Authentication

Off by default, which is right for `localhost` and wrong for anything with a
public address: uploading a peak table and starting an analysis both spend
real CPU and disk. Three modes:

**`none`** (default). No sign-in. Sessions are cookie-scoped directories, the
same trust model as the desktop app on a shared workstation.

**`code`** One shared access code, everyone gets their own workspace. What a
teaching lab wants: tell the room one code.

```bash
export MPACT_AUTH=code
export MPACT_ACCESS_CODE='pick-something-long'
```

**`users`** Named accounts. Each account's uploads and runs live in a
workspace derived from the username, so they follow the person to another
browser or machine, and one account cannot see another's data.

```bash
export MPACT_AUTH=users
python scripts/mpact_users.py add rsamples      # prompts for a password
python scripts/mpact_users.py list
```

Passwords are hashed with scrypt; the file holds hashes, never passwords.
Login cookies are HMAC-signed under `MPACT_SECRET_KEY`, which is generated
into the data root on first start if you don't set one. Set it explicitly if
you run more than one instance, or everyone is signed out when they move
between them.

This is not an identity provider. For institutional single sign-on, put an
authenticating reverse proxy in front (Caddy `forward_auth`, oauth2-proxy,
Shibboleth) and run MPACT in `none` mode bound to `127.0.0.1`, so the proxy
is the only way in.

## Deploying on a VM (Jetstream2, Hetzner, or any Linux box)

The analysis is a spawned subprocess that outlives the request that started
it, and sessions are directories on local disk — so this wants a plain VM,
not request-scoped serverless. One small instance is enough: the bundled demo
(4,959 features × 30 injections) runs in about 25 seconds, and browsing a
completed analysis transfers under 2 MB.

On **Jetstream2** (NSF ACCESS), science gateways are a supported use case, so
a persistent VM with a public IP is the intended shape. An `m3.quad` (4 vCPU,
15 GB) running continuously is roughly 35,000 core-hours a year, well inside
an entry-level allocation.

```bash
# 1. the app, as a service
export MPACT_DATA_ROOT=/data/mpact
export MPACT_AUTH=users
export MPACT_SESSION_COOKIE_SECURE=true    # you are behind HTTPS
export MPACT_TRUST_PROXY=true              # Caddy is the only way in
export MPACT_SECRET_KEY="$(openssl rand -hex 32)"
uvicorn app.main:app --host 127.0.0.1 --port 8000    # loopback only

# 2. Caddy in front, which gets you TLS automatically
#    /etc/caddy/Caddyfile:
#        mpact.example.edu {
#            reverse_proxy 127.0.0.1:8000
#            request_body { max_size 512MB }
#        }
```

Bind uvicorn to `127.0.0.1`, not `0.0.0.0`: with the proxy on the public
interface, the app itself should not be reachable directly, or
`MPACT_TRUST_PROXY` becomes a way to spoof addresses past the sign-in
throttle.

Two things to plan for rather than discover: allocations are renewed
annually, and research infrastructure carries no availability guarantee — if
a class depends on it at a fixed time, that is a risk a commercial VM does
not have.

---

## Licence

MPACT is GPLv3; this front end inherits that. See the upstream repository for
the full text and citation guidance.
