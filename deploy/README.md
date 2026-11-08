# Deploying MPACT Web

The whole thing is one container behind Caddy. Caddy gets a TLS certificate on
its own, so past pointing a DNS name at the machine there is no certificate
work to do.

What this needs from a host: a Linux VM with a public IPv4 address, 2–4 vCPU,
4–8 GB RAM, and 40 GB of disk. Nothing exotic — the analysis is a normal
Python process, not a GPU or MPI job.

---

## The short version

```bash
git clone <this repo> mpactweb && cd mpactweb
cp deploy/mpact.env.example deploy/mpact.env
$EDITOR deploy/mpact.env          # hostname, access code, secret key

docker compose -f deploy/docker-compose.prod.yml up -d --build

# Once: the Natural Products Atlas table (33 MB) for database matching.
docker compose -f deploy/docker-compose.prod.yml run --rm \
    mpactweb python /app/scripts/fetch_reference_data.py
```

Then open `https://<your hostname>`. If it does not come up, `docker compose
-f deploy/docker-compose.prod.yml logs -f` says why; the usual cause is DNS
not yet pointing at the machine, which Caddy reports plainly.

**Before you announce the URL**, set `MPACT_AUTH`. The default is `none`,
which means anyone who finds the address can upload files and start analyses.

---

## Jetstream2 (NSF ACCESS)

Jetstream2 is the cloud resource in ACCESS: persistent VMs with public
addresses, and science gateways are a supported use case rather than
something to be talked into. An `m3.quad` (4 vCPU, 15 GB) is comfortable; an
`m3.medium` (2 vCPU, 7.5 GB) is enough for one analysis at a time.

Running continuously, a 4-core instance is about 35,000 core-hours a year,
which sits inside even an entry-level allocation. Confirm the current
exchange rate when you request it rather than trusting that figure.

1. **Launch** an Ubuntu 22.04 or 24.04 instance from Exosphere. Give it a
   floating IP.
2. **Attach a volume** for the data (say 100 GB) and mount it at `/data`.
   Instance root disks are not the place for months of run outputs, and a
   volume survives rebuilding the instance:
   ```bash
   sudo mkfs.ext4 /dev/sdb && sudo mkdir -p /data && sudo mount /dev/sdb /data
   echo '/dev/sdb /data ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
   ```
   Then point the compose `mpact-data` volume at it, or bind-mount
   `/data/mpact:/data` in `docker-compose.prod.yml`.
3. **Open 80 and 443** in the security group. Nothing else — the app itself
   is not published on a port.
4. **DNS.** A hostname under a domain you control, pointed at the floating
   IP. Without one, set `MPACT_SITE_ADDRESS=:80` and use plain HTTP with
   `MPACT_SESSION_COOKIE_SECURE=false`; get a name before real use.
5. Install Docker, then follow *The short version* above.

Two things to plan for rather than discover: allocations are renewed
annually, and research infrastructure carries no availability guarantee. If a
class depends on the tool at a fixed hour, that is a risk a commercial VM
does not have.

## Hetzner (or any other VPS)

Identical, minus the volume step — the instance disk is fine at these sizes.
A CX32 (4 vCPU, 8 GB, 80 GB) is about €6.80/month with 20 TB of traffic
included, which this app will never approach: browsing a whole analysis
transfers under 2 MB.

Pick a location near your users (Hetzner has Ashburn VA and Hillsboro OR
alongside the German sites). Then:

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo ufw allow 80,443/tcp && sudo ufw enable
```

and *The short version*.

---

## Without Docker

If the host has Python 3.12 and you would rather not run a container, use the
systemd unit in `deploy/mpact.service`:

```bash
sudo useradd --system --home /opt/mpactweb --shell /usr/sbin/nologin mpact
sudo git clone <this repo> /opt/mpactweb && cd /opt/mpactweb
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r server/requirements.txt
sudo mkdir -p /var/lib/mpact && sudo chown mpact:mpact /var/lib/mpact
sudo cp deploy/mpact.service /etc/systemd/system/
sudo $EDITOR /etc/systemd/system/mpact.service     # the Environment= lines
sudo systemctl enable --now mpact
```

Caddy then proxies to `127.0.0.1:8000` instead of a container name.

---

## Running it

**Accounts.** With `MPACT_AUTH=users`:

```bash
docker compose -f deploy/docker-compose.prod.yml exec mpactweb \
    python /app/scripts/mpact_users.py add rsamples
```

Each account gets a workspace derived from its username, so someone's uploads
and results follow them between machines and no account can see another's.

**Disk.** Sessions untouched for `MPACT_SESSION_MAX_AGE_SECONDS` are deleted
at startup and once a day after that. Budget roughly 25–60 MB per analysis
plus the uploads. Check with:

```bash
docker compose -f deploy/docker-compose.prod.yml exec mpactweb du -sh /data
```

With named accounts, raise that age: a workspace is someone's ongoing work,
not a scratch session, and the default 30 days will delete the results of
anyone who takes a summer off.

**Backups.** The data volume holds uploads, run outputs and — if you did not
set `MPACT_SECRET_KEY` — the cookie-signing key. The accounts file lives
there too.

```bash
docker run --rm -v mpactweb_mpact-data:/data -v "$PWD:/backup" alpine \
    tar czf /backup/mpact-$(date +%F).tar.gz -C /data .
```

The NPAtlas volume needs no backup; re-fetch it instead.

**Moving to another host** (Hetzner now, Jetstream2 later — the setup is the
same on both). Restore the data volume and copy `deploy/mpact.env`, then
bring the stack up. One thing to get right: with named accounts, each
workspace directory is derived from the username *and the signing key*, so a
different `MPACT_SECRET_KEY` on the new host means everyone signs in
successfully and finds an empty library. Either set the key explicitly in
`mpact.env` on both hosts, or make sure the restored volume includes the
`secret.key` file the server generated.

**Upgrading.**

```bash
git pull
docker compose -f deploy/docker-compose.prod.yml up -d --build
```

The build fails rather than deploys if a vendored pipeline file was edited in
place — that check is the point of `scripts/vendor_core.py verify`.

**Institutional single sign-on.** MPACT is not an identity provider. For
Smith credentials, put an authenticating proxy in front (Caddy
`forward_auth`, oauth2-proxy, Shibboleth), run MPACT with `MPACT_AUTH=none`,
and make sure the app is reachable *only* through that proxy.
