# MPACT Web

MPACT (Metabolomics Peak Analysis Computational Tool) in the browser. It
filters untargeted LC-MS metabolomics peak tables from Progenesis QI, MZmine,
MS-DIAL or Metaboscape, runs the statistics and database searches, and shows
the results as interactive plots.

- App: <https://app.mpactms.org>
- Documentation: <https://docs.mpactms.org>
- Analysis core: [robertsamples/mpact](https://github.com/robertsamples/mpact)

## Running it locally

```bash
pip install -r server/requirements.txt
python scripts/fetch_reference_data.py   # optional: NPAtlas, for database search
cd server && uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000>. With Docker, run `docker compose up --build`.

To run a server for others, see the documentation on
[installation](https://docs.mpactms.org/self-hosting/),
[configuration](https://docs.mpactms.org/self-hosting/configuration/),
[sign-in and accounts](https://docs.mpactms.org/self-hosting/authentication/) and
[deployment](https://docs.mpactms.org/self-hosting/deployment/).

## Development

```bash
pip install -r server/requirements-dev.txt
cd server && python -m pytest tests -q
```

The analysis itself is the MPACT core, copied into `server/mpact_core/` from
the mpact repository. Do not edit it here: change it in mpact, then run
`python scripts/vendor_core.py sync --src ../mpact`. CI and the Docker build
run `vendor_core.py verify`, which fails if the copy has been edited.

More in [Development](https://docs.mpactms.org/reference/development/) and
[Architecture](https://docs.mpactms.org/architecture/). The documentation is
built from `docs/` and `mkdocs.yml` with [Zensical](https://zensical.org)
(`zensical serve` to preview).

## Citation

If you use MPACT, please cite:

Samples, R. M.; Puckett, S. P.; Balunas, M. J. Metabolomics Peak Analysis
Computational Tool (MPACT): An Advanced Informatics Tool for Metabolomics and
Data Visualization of Molecules from Complex Biological Samples. *Anal. Chem.*
**2023**, *95* (23), 8770–8779.
[doi:10.1021/acs.analchem.2c04632](https://doi.org/10.1021/acs.analchem.2c04632)

## License

MPACT Web is released under the [GNU General Public License version 3](LICENSE).

The MPACT core modules in `server/mpact_core/` come from
[robertsamples/mpact](https://github.com/robertsamples/mpact):

- Copyright 2022, Robert M. Samples, Sara P. Puckett, and Marcy J. Balunas
- Modifications Copyright 2026, Robert M. Samples and Yilu Chen

Each module's header says which applies to it. MPACT was first released in
2022 as [BalunasLab/mpact](https://github.com/BalunasLab/mpact), labelled
GPLv2. It depended on PyQt5, which is GPLv3, and the core repository has
corrected the label to GPLv3.

Plotly.js (MIT) and JSME (BSD) are downloaded by `scripts/fetch_web_vendor.py`
rather than kept in this repository, and carry their own license notices.
