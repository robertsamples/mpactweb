# Configuration

MPACT is configured with environment variables. Every one is optional and has
a working default. With Docker, set them under `environment:` in
`docker-compose.yml` or in an `env_file`.

## Storage and limits

| Variable | Default | Purpose |
|---|---|---|
| `MPACT_DATA_ROOT` | `server/var` | Workspaces, uploads and run outputs |
| `MPACT_MAX_CONCURRENT_JOBS` | `2` | Analyses running at once; more are queued |
| `MPACT_JOB_TIMEOUT_SECONDS` | `3600` | Time limit per analysis |
| `MPACT_MAX_UPLOAD_BYTES` | `536870912` | Largest upload (512 MB) |
| `MPACT_SESSION_MAX_AGE_SECONDS` | `2592000` | Workspaces untouched this long (30 days) are deleted, at startup and daily |

Each analysis is its own process, and a large peak table can use several GB of
memory. Allow one concurrent job per 2 cores and 4 GB.

## Reference data

| Variable | Default | Purpose |
|---|---|---|
| `MPACT_REFERENCE_DIR` | `server/data` | Directory for the files below |
| `MPACT_NPATLAS_PATH` | `<reference>/npatlas.tsv` | Natural Products Atlas table |
| `MPACT_NPATLAS_SUPPLEMENTAL_PATH` | `server/data/npatlas_supplemental.tsv` | Extra compounds searched with NPAtlas (SIDERITE siderophores, a lab compound list) |
| `MPACT_TAXONOMY_PATH` | `server/data/npatlas_taxonomy.tsv` | Genus lineages for the taxon filter |
| `MPACT_SIDERITE_KEYS_PATH` | `server/data/siderite_inchikeys.txt` | Structures for the siderophore filter |
| `MPACT_MSMS_LIBRARY_PATH` | `<reference>/msdial_msms.npz` | Compiled MS/MS library |
| `MPACT_MSMS_META_PATH` | `<reference>/msdial_msms_meta.tsv.gz` | MS/MS library names and structures |

## Performance

| Variable | Default | Purpose |
|---|---|---|
| `MPACT_MAX_HEATMAP_ROWS` | `1500` | Rows drawn in the heatmap; beyond this, neighbouring rows are averaged |
| `MPACT_PVCLUST_NBOOT` | `1000` | Bootstrap iterations for dendrogram support values |
| `MPACT_CSV_CACHE_MB` | `256` | Memory for parsed output tables |
| `MPACT_PLOT_CACHE_MB` | `64` | Memory for finished plots, so reopening a tab is instant |
| `MPACT_FRAGDB_CACHE_ENTRIES` | `6` | Parsed fragment databases kept in memory |
| `MPACT_PREWARM` | `true` | Parse NPAtlas structures at startup so the first substructure search is fast |

## Security and networking

| Variable | Default | Purpose |
|---|---|---|
| `MPACT_AUTH` | `none` | Sign-in mode: `none`, `code`, `users` or `email`. See [Sign-in and accounts](authentication.md) |
| `MPACT_SECRET_KEY` | generated | Key that signs login cookies; generated into the data root if unset |
| `MPACT_AUTH_MAX_AGE_SECONDS` | `1209600` | How long a sign-in lasts (14 days) |
| `MPACT_SESSION_COOKIE_SECURE` | `false` | Send cookies over HTTPS only; turn on behind TLS |
| `MPACT_TRUST_PROXY` | `false` | Read the client address from `X-Forwarded-For`; only behind your own proxy |
| `MPACT_DEV` | `false` | Include exception details in API errors; never in production |

Variables for each sign-in mode, including email delivery, are listed on
[Sign-in and accounts](authentication.md).

## Demo datasets

Demo datasets live in `server/data/demo/`. A dataset whose files are missing is
left out of the list. Delete the directory to hide the demo panel completely.
