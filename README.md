# SpoolSync

Sync zwischen **SimplyPrint** und **Spoolman** mit kleinem Web-UI und SQLite.
- Verknüpfung per `lot_nr` (Spoolman) ⇄ `uid` (SimplyPrint)
- Verbrauchs-Update (used_weight) aus SP-Längen + Dichte/Ø
- **Konfiguration im Webinterface** (Einstellungen-Seite)
- Betrieb ohne Docker via Install-Script (systemd) – Docker optional

## Schnellstart (ohne Docker)
```bash
sudo ./scripts/install-spoolsync.sh
# UI: http://SERVER:8080   |   Health: /health   |   Einstellungen: /settings
```

## ENV (nur Basis)
| Variable | Beschreibung | Default |
|---|---|---|
| `DB_PATH` | SQLite Pfad | `/var/lib/spoolsync/spoolsync.db` |
| `PORT` | HTTP-Port | `8080` |
| `TZ` | Zeitzone | `Europe/Berlin` |

> Alle anderen Settings (SP/Spoolman, Token, Intervall, Epsilon, Dry-Run) werden **im Web-UI** gesetzt.

## Update
```bash
sudo systemctl stop spoolsync
git pull
sudo -u spoolsync /opt/spoolsync/.venv/bin/pip install -r requirements.txt
sudo systemctl start spoolsync
```

## Backup
```bash
sudo ./scripts/backup-sqlite.sh
```

Lizenz: MIT
