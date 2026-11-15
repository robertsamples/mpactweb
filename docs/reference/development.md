# Development

## Layout

```
server/
  mpact_core/     vendored MPACT pipeline, byte-identical to upstream; never edit here
  app/
    core.py       the single import gateway to the vendored core
    params.py     builds the pipeline's parameters from the web form
    runner.py     child process that runs the pipeline
    jobs.py       queue and concurrency limit
    results.py    cached readers for a run's output files
    plotdata/     JSON builders for each plot
    routers/      HTTP API
  tests/
web/              static front end, ES modules, no build step
docs/             this documentation
deploy/           production compose file, Caddy configuration
scripts/          vendor sync, reference data, browser libraries
```

[Architecture](../architecture.md) explains why it is built this way.

## Running the tests

```bash
pip install -r server/requirements-dev.txt
cd server && python -m pytest tests/ -q
```

`tests/test_pipeline_e2e.py` runs a real analysis through the HTTP API with no
mocking. It looks for the PTY087I2 example dataset at
`../mpact/rawdata/PTY087I2` (override with `MPACT_TEST_DATA`) and is skipped if
the data is not there.

## The vendored pipeline

`server/mpact_core/` is a copy of the desktop application's Qt-free modules,
pinned by hash in `VENDOR_MANIFEST.json`.

```bash
python scripts/vendor_core.py verify                  # run in CI and in the Docker build
python scripts/vendor_core.py sync --src ../mpact     # pull upstream changes
```

Fix bugs in those modules upstream in
[robertsamples/mpact](https://github.com/robertsamples/mpact), then sync. An
edit made here fails `verify` and the Docker build.

## Building the documentation

The documentation is built with [Zensical](https://zensical.org) from
`mkdocs.yml` and `docs/`:

```bash
pip install zensical
zensical serve        # live preview at http://localhost:8000
zensical build        # static site in site/
```

On mpactms.org the site is built into the Caddy image
(`deploy/caddy.Dockerfile`) and served at docs.mpactms.org.

Screenshots are taken from the PTY087I2 demo at a 1440 × 900 window, at 2×
pixel density, and saved at 1800 pixels wide.
