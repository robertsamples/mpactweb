# Deploying on a server

For a lab or class server with a public hostname, the repository's `deploy/`
directory runs MPACT behind [Caddy](https://caddyserver.com), which obtains and
renews a TLS certificate by itself.

## Requirements

- A Linux VM with a public IPv4 address. 2 vCPU and 4 GB of RAM run one
  analysis at a time; 4 vCPU and 8 GB run two.
- 40 GB of disk. Budget 25 to 60 MB per analysis plus uploads.
- A DNS name pointed at the machine.
- Ports 80 and 443 open. The app itself is not exposed.

A plain VM fits better than serverless hosting: each analysis is a long-running
process, and workspaces are directories on local disk. The demo dataset runs in
about 25 seconds on a small instance, and browsing a finished analysis
transfers under 2 MB.

## Setup

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo ufw allow 80,443/tcp && sudo ufw enable

git clone https://github.com/robertsamples/mpactweb.git && cd mpactweb
cp deploy/mpact.env.example deploy/mpact.env
nano deploy/mpact.env        # hostname, sign-in mode, secret key

docker compose -f deploy/docker-compose.prod.yml up -d --build
docker compose -f deploy/docker-compose.prod.yml run --rm \
    mpactweb python /app/scripts/fetch_reference_data.py
```

In `mpact.env`, set at least:

```bash
MPACT_SITE_ADDRESS=mpact.example.edu     # the hostname Caddy gets a certificate for
MPACT_AUTH=code                          # or users / email; never none on a public server
MPACT_SECRET_KEY=...                     # openssl rand -hex 32
```

`MPACT_SESSION_COOKIE_SECURE=true` and `MPACT_TRUST_PROXY=true` are already set
in the example file, because the app sits behind Caddy.

Then open `https://<your hostname>`. If it does not come up,
`docker compose -f deploy/docker-compose.prod.yml logs -f` says why. The usual
cause is DNS not yet pointing at the machine.

## Extra sites

`deploy/Caddyfile` serves the app at `MPACT_SITE_ADDRESS` and imports any
`deploy/sites/*.caddy` files for other hostnames. mpactms.org uses this for the
documentation and the landing page, which are built into the Caddy image by
`deploy/caddy.Dockerfile`. Delete `deploy/sites/mpactms.org.caddy` on your own
server, or replace it with your own hostnames.

## Operating it

**Upgrading**

```bash
git pull
docker compose -f deploy/docker-compose.prod.yml up -d --build
```

The build fails rather than deploys if a vendored pipeline file was edited.
Before restarting during a class, check that no analysis is running.

**Backups.** The `mpact-data` volume holds uploads, results, the accounts file
and (if you did not set one) the signing key.

```bash
docker run --rm -v mpactweb_mpact-data:/data -v "$PWD:/backup" alpine \
    tar czf /backup/mpact-$(date +%F).tar.gz -C /data .
```

The reference volume needs no backup; fetch it again instead.

**Disk.** Workspaces untouched for `MPACT_SESSION_MAX_AGE_SECONDS` are deleted
automatically. With named accounts, raise it well above the 30-day default, or
someone who takes a summer off loses their results.

```bash
docker compose -f deploy/docker-compose.prod.yml exec mpactweb du -sh /data
```

**Moving to another host.** Restore the data volume, copy `deploy/mpact.env`,
and start the stack. Keep the same `MPACT_SECRET_KEY` (or restore the
generated `secret.key` with the volume), or accounts will find empty
workspaces.

## Without Docker

`deploy/mpact.service` is a systemd unit for running uvicorn directly with
Python 3.12. Bind it to `127.0.0.1` and put Caddy or another reverse proxy in
front; with the app reachable only through the proxy, `MPACT_TRUST_PROXY=true`
is safe.

## Cloud options

- **Hetzner** (or any VPS): a CX32 (4 vCPU, 8 GB) costs a few euros a month,
  with far more traffic included than MPACT uses.
- **Jetstream2** (NSF ACCESS): science gateways are a supported use. A
  4-core instance running all year is about 35,000 core-hours, inside an
  entry-level allocation. Allocations renew yearly and research
  infrastructure has no availability guarantee, so plan for that if a class
  depends on it.
