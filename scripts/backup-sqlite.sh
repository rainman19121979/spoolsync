#!/usr/bin/env bash
set -euo pipefail
DEST="${1:-$PWD}"
STAMP="$(date +%F_%H%M%S)"
systemctl stop spoolsync
cp -v /var/lib/spoolsync/spoolsync.db "$DEST/spoolsync-$STAMP.db" || true
systemctl start spoolsync
echo "Backup: $DEST/spoolsync-$STAMP.db"
