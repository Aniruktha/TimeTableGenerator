# Deploying Timetable

One process serves everything: the pages, the API, and the solver. Its whole
state is one SQLite file plus the saved setups, both under `TTGEN_HOME`.

## What the server needs

- Linux x86-64 or arm64 with **4+ cores** and **2 GB RAM**. CP-SAT uses every
  core while generating. A department-sized solve takes 1–3 minutes; reading
  timetables costs almost nothing.
- Python 3.12 with [uv](https://docs.astral.sh/uv/), **or** Docker.
- HTTPS in front of it before students use it: the college's reverse proxy,
  nginx, or Caddy.

## Option A: Docker

```bash
cp .env.example .env          # set TTGEN_ORG_NAME; optionally the first admin
docker compose up -d --build
docker compose logs -f        # "Timetable 1.0.0 on http://localhost:8000"
```

Data lives in the `timetable-data` volume (`/var/lib/timetable` inside the
container). Upgrading means `git pull && docker compose up -d --build`; the
volume carries over.

## Option B: directly with uv

```bash
uv sync --no-dev
export TTGEN_HOME=/var/lib/timetable TTGEN_ORG_NAME="PSG College of Technology"
uv run tt serve --port 8000 --proxy
```

As a systemd service (`/etc/systemd/system/timetable.service`):

```ini
[Unit]
Description=Timetable
After=network.target

[Service]
User=timetable
WorkingDirectory=/opt/timetable
Environment=TTGEN_HOME=/var/lib/timetable
Environment=TTGEN_SECURE_COOKIES=1
ExecStart=/opt/timetable/.venv/bin/tt serve --host 127.0.0.1 --port 8000 --proxy
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## HTTPS

Caddy gets and renews a certificate by itself:

```
timetable.example.edu {
    reverse_proxy 127.0.0.1:8000
}
```

With nginx, pass the usual headers and allow long requests, because a
generate call waits for the solver:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 400s;
}
```

Once HTTPS works, set `TTGEN_SECURE_COOKIES=1` so the sign-in cookie is never
sent over plain HTTP. Run with `--proxy` behind a proxy so the sign-in brake
counts failed attempts per real client address, not per proxy.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `TTGEN_HOME` | `data/` in the checkout | database (`state/ttgen.db`) and saved setups (`saved/`) |
| `TTGEN_ORG_NAME` | PSG College of Technology | shown on every page |
| `TTGEN_SESSION_HOURS` | 12 | how long a sign-in lasts |
| `TTGEN_SECURE_COOKIES` | off | `1` once served over HTTPS |
| `TTGEN_ADMIN_USERNAME`, `TTGEN_ADMIN_PASSWORD` | unset | create this admin at start-up if there are no accounts yet |
| `TTGEN_DATA_DIR` | `data/` | where the bundled example setups are read from |
| `TTGEN_MAX_SOLVES` | 2 | timetables generated at once; another request is asked to try again. Each uses every core and up to ~2 GB for a large department |

## The first administrator

Either:

- open the site: with no accounts yet it shows **Set up your site**; or
- set `TTGEN_ADMIN_USERNAME` / `TTGEN_ADMIN_PASSWORD` before the first start;
  or
- `tt user add office --role admin --name "Timetable Office"` on the server.

Do the first-run step before the site is reachable from outside, so nobody
else can claim the administrator account.

**Locked out?** On the server, `tt user passwd <username>` sets a new password
(and re-enables a disabled account). `tt user list` shows everyone.

## Backups

Everything worth keeping is under `TTGEN_HOME`. The database is safe to copy
while the site runs if you use SQLite's own backup:

```bash
sqlite3 /var/lib/timetable/state/ttgen.db ".backup /backups/timetable-$(date +%F).db"
cp -r /var/lib/timetable/saved /backups/saved-$(date +%F)
```

To restore, stop the service, put the files back, and start it.

## Things to know

- **Run a single process.** Generated drafts stay in memory until they are
  published, so do not run several workers behind a load balancer. One
  process serves a whole college's reads comfortably; the solver already uses
  every core.
- A draft that is not published is lost on restart. Published versions and
  saved setups are not.
- Passwords are stored as scrypt hashes and sessions as SHA-256 digests; the
  database never holds anything that can be used to sign in directly.
- Failed sign-ins are slowed: 5 per username or 30 per address in 15 minutes,
  after which the site asks the person to wait.
- `GET /healthz` returns `{"ok": true}` when the app and its database are up.
  Point your monitoring at it.
