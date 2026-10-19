#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

install -d -o 1000 -g 1000 -m 2770 /var/lib/ficatlas-restart-bridge
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 \
  "$ROOT/deploy/ficatlas-restart-bridge.py" \
  /usr/local/libexec/ficatlas-restart-bridge.py
install -o root -g root -m 0644 \
  "$ROOT/deploy/ficatlas-restart-bridge.service" \
  /etc/systemd/system/ficatlas-restart-bridge.service
systemctl daemon-reload
systemctl enable ficatlas-restart-bridge.service
systemctl restart ficatlas-restart-bridge.service
systemctl --no-pager --full status ficatlas-restart-bridge.service
