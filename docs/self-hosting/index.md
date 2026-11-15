# Installation

MPACT is a Python web server plus a static front end. Run it on your own
computer to keep data local or work offline, or on a shared server for a lab or
a class. Either way it is the same program as [app.mpactms.org](https://app.mpactms.org).

There are two ways to install it: with Docker, or directly with Python.

## With Docker

Docker is the simplest option on any operating system. Install
[Docker Desktop](https://docs.docker.com/get-docker/), then:

```bash
git clone https://github.com/robertsamples/mpactweb.git
cd mpactweb
docker compose up -d --build
```

Open <http://localhost:8000>. Uploads and results are kept in a Docker volume,
so they survive restarts and upgrades.

## With Python

Requires Python 3.12.

```bash
git clone https://github.com/robertsamples/mpactweb.git
cd mpactweb
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r server/requirements.txt
cd server
uvicorn app.main:app
```

Open <http://127.0.0.1:8000>. Data is stored in `server/var/` unless you set
`MPACT_DATA_ROOT`.

## Reference data

The analysis itself needs nothing else. Two optional reference sets enable the
database searches in [Feature Info](../results/feature-info.md#database-searches).
Without them the server still runs and hides the search controls.

**Natural Products Atlas** (about 33 MB), for MS1 matching:

```bash
python scripts/fetch_reference_data.py
# Docker:
docker compose run --rm mpactweb python /app/scripts/fetch_reference_data.py
```

**MS-DIAL MS/MS library**, for spectral matching. Download a positive-mode MSP
library from the [MS-DIAL website](https://systemsomicslab.github.io/compms/msdial/main.html#MSP)
and compile it:

```bash
python scripts/build_msms_library.py MSMS-Public_experimentspectra-pos-VS19.msp
```

This writes `msdial_msms.npz` and `msdial_msms_meta.tsv.gz` to the reference
directory (`server/data`, or `/reference` in Docker).

## Working offline

The front end loads Plotly and the JSME structure editor from public CDNs. For
a machine with no internet access, download local copies once:

```bash
python scripts/fetch_web_vendor.py
```

## Upgrading

```bash
git pull
docker compose up -d --build              # Docker
pip install -r server/requirements.txt    # Python, then restart uvicorn
```

## Next steps

- [Configuration](configuration.md) lists every setting.
- Before anyone else can reach your server, set up
  [sign-in](authentication.md). The default is no sign-in at all.
- [Deploying on a server](deployment.md) covers HTTPS and a public hostname.
