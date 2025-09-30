#!/usr/bin/env bash
set -euo pipefail

APP_USER="spoolsync"
APP_DIR="/opt/spoolsync"
DATA_DIR="/var/lib/spoolsync"
LOG_DIR="/var/log/spoolsync"
ENV_FILE="$APP_DIR/.env"
SERVICE_NAME="spoolsync"

if [[ $EUID -ne 0 ]]; then echo "Bitte als root ausführen (sudo)"; exit 1; fi

echo ">>> Abhängigkeiten installieren…"
if command -v apt >/dev/null 2>&1; then
  apt update -y && apt install -y python3 python3-venv python3-pip rsync
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 python3-pip rsync
elif command -v yum >/dev/null 2>&1; then
  yum install -y python3 python3-pip rsync
elif command -v pacman >/dev/null 2>&1; then
  pacman -Sy --noconfirm python python-pip rsync
else
  echo "Bitte Python3/venv/pip manuell installieren."; exit 1
fi

echo ">>> Benutzer & Verzeichnisse…"
id -u "$APP_USER" >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR" "$DATA_DIR" "$LOG_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR" "$DATA_DIR" "$LOG_DIR"

echo ">>> Dateien kopieren…"
SRC_DIR="$(pwd)"
rsync -a "$SRC_DIR/app" "$SRC_DIR/requirements.txt" "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo ">>> venv…"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"

# pip Cache-Verzeichnis für User erstellen
CACHE_DIR="/home/$APP_USER/.cache/pip"
mkdir -p "$CACHE_DIR"
chown -R "$APP_USER:$APP_USER" "$CACHE_DIR"

sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

TZ_DEFAULT="$(timedatectl show -p Timezone --value 2>/dev/null || echo Europe/Berlin)"
read -rp "HTTP Port [8080]: " PORT; PORT=${PORT:-8080}
read -rp "DB Pfad [$DATA_DIR/spoolsync.db]: " DBP; DBP=${DBP:-$DATA_DIR/spoolsync.db}
read -rp "Timezone [$TZ_DEFAULT]: " TZ; TZ=${TZ:-$TZ_DEFAULT}

cat > "$ENV_FILE" <<ENV
DB_PATH=$DBP
PORT=$PORT
TZ=$TZ
ENV
chown "$APP_USER:$APP_USER" "$ENV_FILE"; chmod 640 "$ENV_FILE"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<UNIT
[Unit]
Description=SpoolSync FastAPI
After=network-online.target
Wants=network-online.target

[Service]
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=-${ENV_FILE}
ExecStart=${APP_DIR}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port \${PORT}
Restart=on-failure
StandardOutput=append:${LOG_DIR}/app.log
StandardError=append:${LOG_DIR}/app.err
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
sleep 1
systemctl --no-pager --full status "${SERVICE_NAME}" || true

echo "Fertig. UI: http://$(hostname -I | awk '{print $1}'):${PORT}  |  /settings"
