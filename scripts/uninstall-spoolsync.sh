#!/usr/bin/env bash
set -euo pipefail
SERVICE="spoolsync"
read -rp "Wirklich SpoolSync entfernen? [yes/NO] " C; [[ "${C:-}" == "yes" ]] || { echo "Abbruch."; exit 1; }
systemctl disable --now "$SERVICE" || true
rm -f "/etc/systemd/system/${SERVICE}.service"
systemctl daemon-reload
rm -rf /opt/spoolsync /var/lib/spoolsync /var/log/spoolsync
id -u spoolsync >/dev/null 2>&1 && userdel spoolsync || true
echo "Removed."
