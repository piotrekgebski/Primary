# Primary / Dynatrace Services Registry add-on

This add-on **reads the Dynatrace Monitored Entities API** to maintain an independent SQLite and CSV inventory of Services on your AWS Lightsail host. It does not create or change Dynatrace entities. 

Environment URL already set in `.env.example`: `https://aao56575.live.dynatrace.com` (public identifier, no secret).

Create a **separate** Dynatrace API token with **Read entities** (`entities.read`). Do **not** reuse an InstallerDownload / PaaS Integration token created for OneAgent installation.

## Integrate with the existing Primary repository (on Debian)

Unpack the archive *into* the existing repository `~/Git/Primary`; it contains only:

- `dynatrace-registry/`
- `.github/workflows/dynatrace-registry-ci.yml`

Review changes on a feature branch; it does not overwrite `ci.yml`, `main.py`, `Dockerfile` or `requirements.txt`.

Run tests and Ruff locally before pushing. For example:

```bash
cd ~/Git/Primary
python -m pip install -r dynatrace-registry/requirements.txt pytest
python -m pytest -q dynatrace-registry/tests
ruff check dynatrace-registry
```

## Install only the registry on the Ubuntu Lightsail host

Copy the folder from Debian:

```bash
scp -r ~/Git/Primary/dynatrace-registry staging:~/
```

Then:

```bash
ssh staging
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip
sudo bash ~/dynatrace-registry/deploy/install_debian.sh
sudoedit /etc/dynatrace-services-registry.env
```

In the root-only config set `DT_API_TOKEN=...` to your new **Read entities** token. The URL is prefilled with `aao56575` from the example. Do not paste token into this chat or commit it to Git.

Manually test:

```bash
sudo systemctl start dynatrace-services-registry.service
sudo journalctl -u dynatrace-services-registry.service -n 30 --no-pager
```

After you confirm a successful scan and CSV outputs, enable daily schedule:

```bash
sudo systemctl enable --now dynatrace-services-registry.timer
```

Results are stored under `/var/lib/dynatrace-services-registry/` and retained outside Docker. For details see `dynatrace-registry/README.md`.

Notes: the Lightsail instance is **Ubuntu**, even though the upstream installer name uses `install_debian.sh`; it relies on standard Debian/Ubuntu tools. Initial scan should have at least one SERVICE entity; on an empty environment it intentionally refuses to modify the database.
