# Dynatrace Classic Services Registry — Debian / AWS Lightsail

Add-on for an existing Python/Docker/GitHub Actions project. It deliberately runs on the Debian HOST, outside your primary application container, so an image refresh does not erase its SQLite history.

## Repository layout

```text
existing-repo/
├── .github/workflows/dynatrace-registry-ci.yml  # New independent, offline CI job
└── dynatrace-registry/
    ├── sync_services.py
    ├── requirements.txt
    ├── .env.example
    ├── .gitignore
    ├── tests/test_sync_services.py
    └── deploy/
        ├── install_debian.sh
        ├── dynatrace-services-registry.service
        └── dynatrace-services-registry.timer
```

## Requirements

- Debian host (Lightsail), with Python >= 3.9 and `python3-venv` installed.
- Outbound HTTPS access from Lightsail to Dynatrace environment URL.
- Dynatrace Classic API token with **`entities.read`**, no write permissions.
- HTTPS Dynatrace API URL such as `https://YOUR-ENV.live.dynatrace.com`.
- SSH to the host with sudo for the initial installation.

`GET /api/v2/entities`, with `entitySelector=type(SERVICE)`, scans entities observed in the configured lookback window. Pagination uses `nextPageKey` alone after the first page. The script makes no write calls to Dynatrace.

## First deployment — Debian host

1. Place the `dynatrace-registry/` folder inside your existing project repository. Do not replace the existing `ci.yml` or Dockerfile. Copy `.github/workflows/dynatrace-registry-ci.yml` into your repo too.
2. Clone/pull the updated repo onto the Lightsail Debian server or transfer the folder over SSH. Run from the repo root:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip
sudo bash dynatrace-registry/deploy/install_debian.sh
```

3. Configure the API URL and token on the host (the installer creates a placeholder only on first installation):

```bash
sudoedit /etc/dynatrace-services-registry.env
sudo stat -c '%a %U:%G %n' /etc/dynatrace-services-registry.env
# Should show 600 root:root
```

4. Test one manual synchronization and inspect the logs:

```bash
sudo systemctl start dynatrace-services-registry.service
sudo systemctl status dynatrace-services-registry.service --no-pager
sudo journalctl -u dynatrace-services-registry.service -n 50 --no-pager
```

5. Enable the timer after successful testing:

```bash
sudo systemctl enable --now dynatrace-services-registry.timer
systemctl list-timers --all | grep dynatrace-services-registry
```

The timer runs at **03:15 server local time**, with up to 10 minutes of jitter (and catches up after server downtime). Change `OnCalendar` if needed and run `sudo systemctl daemon-reload && sudo systemctl restart dynatrace-services-registry.timer`.

## Where data lives

- Code / venv: `/opt/dynatrace-services-registry/`
- Token configuration: `/etc/dynatrace-services-registry.env` (root:root, 0600)
- SQLite and reports: `/var/lib/dynatrace-services-registry/`
- `services.sqlite`: full registry, history and run log.
- `services_current.csv`: services with current observation status.
- `services_changes.csv`: events (`NEW`, `RENAMED`, `RETURNED`, `NOT_OBSERVED`).
- `services_not_observed.csv`: services currently in `PENDING` or `NOT_OBSERVED` status.
- `sync_history.csv`: daily scan totals.

All CSV files use UTF-8 BOM and semicolon separators for easy Excel import. Default state folder permissions are private to the service user.

For a one-time secure copy of the report into your own home folder (replace the destination as needed):

```bash
sudo install -m 0600 -o "$USER" -g "$(id -gn)" \
  /var/lib/dynatrace-services-registry/services_current.csv \
  "$HOME/services_current.csv"
```

Do not expose this directory via a public web server or commit its database/CSV contents to GitHub.

## Updating code / CI integration

- `dynatrace-registry-ci.yml` tests the collector on pushes/PRs without a real Dynatrace token.
- Current application deployment workflow is unchanged.
- Once changes are merged, update on Lightsail by pulling/transferring the updated folder and rerunning `sudo bash dynatrace-registry/deploy/install_debian.sh`. The installer **preserves existing environment secrets and database**.
- When ready, a separate CD job can automate host-side updates using the existing SSH access; check the actual current deployment workflow and sudo policy before enabling this. Never put `DT_API_TOKEN` in GitHub Actions output.

## Safety / interpretation

- `DT_LOOKBACK_DAYS=30`: entities observed during the last 30 days. This is NOT an immediate liveness probe. A service removed yesterday may remain in API results until it falls outside the lookback window. Use shorter windows only if occasional inactivity won't mislead you.
- `DT_MISSING_RUNS=3`: after three consecutive **successful** API scans without the service, status becomes `NOT_OBSERVED` (not definitively deleted).
- `DT_MIN_RESULT_RATIO=0.5`: refuse to update the database if a scan drops below 50% of the previous scan count; protects against accidental filtering/scoping/permissions changes. If a real environment change legitimately affects over half of services, temporarily adjust this threshold after checking the data.
- The script rejects empty/incomplete/duplicated API results and fails before modifying the inventory in these cases.
- Important: a changed Dynatrace token's permissions or a different environment URL can make Services disappear from the response. Keep scope and token consistent.
- Existing information is stored in SQLite outside the app container, so back up `/var/lib/dynatrace-services-registry/services.sqlite` (for online backup, use SQLite backup API or `sqlite3 .backup`).

## Simple verification without Dynatrace

```bash
python3 -m pip install -r dynatrace-registry/requirements.txt pytest
python3 -m pytest -q dynatrace-registry/tests
```

## Known migration consideration

Dynatrace has announced migration from the Classic entity API to Smartscape on Grail. This collector is intended for environments that still support the Classic API and can later be switched to the new source.
