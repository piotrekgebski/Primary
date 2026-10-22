#!/usr/bin/env bash
set -euo pipefail

# Run: sudo bash dynatrace-registry/deploy/install_debian.sh
if [[ "$EUID" -ne 0 ]]; then
  echo "Run with sudo/root" >&2
  exit 1
fi

SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd -- "${SELF_DIR}/.." && pwd)"
APP_DIR=/opt/dynatrace-services-registry
STATE_DIR=/var/lib/dynatrace-services-registry
CONFIG=/etc/dynatrace-services-registry.env

if ! id dynatrace-registry >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin dynatrace-registry
fi

install -d -m 0755 -o root -g root "$APP_DIR"
install -d -m 0700 -o dynatrace-registry -g dynatrace-registry "$STATE_DIR"
install -m 0644 -o root -g root "$SOURCE_DIR/sync_services.py" "$APP_DIR/sync_services.py"
install -m 0644 -o root -g root "$SOURCE_DIR/requirements.txt" "$APP_DIR/requirements.txt"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Install python3 and python3-venv on Debian first" >&2
  exit 1
fi

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$APP_DIR/.venv" || {
    echo "Could not create venv. Install python3-venv and retry." >&2
    exit 1
  }
fi
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

install -m 0644 -o root -g root \
  "$SOURCE_DIR/deploy/dynatrace-services-registry.service" \
  /etc/systemd/system/dynatrace-services-registry.service
install -m 0644 -o root -g root \
  "$SOURCE_DIR/deploy/dynatrace-services-registry.timer" \
  /etc/systemd/system/dynatrace-services-registry.timer

if [[ ! -e "$CONFIG" ]]; then
  install -m 0600 -o root -g root "$SOURCE_DIR/.env.example" "$CONFIG"
  echo "Created template: $CONFIG (edit with sudo before running)"
else
  echo "Existing token configuration preserved: $CONFIG"
fi

systemctl daemon-reload
printf '\nInstalled registry; no timer enabled automatically.\n'
printf 'Next: sudoedit %s\n' "$CONFIG"
printf 'Then: sudo systemctl start dynatrace-services-registry.service\n'
printf 'Finally: sudo systemctl enable --now dynatrace-services-registry.timer\n'
