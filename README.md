# SpoolSync

Automatische Synchronisierung zwischen **SimplyPrint** und **Spoolman**.

## ✨ Features

- 🔄 Automatische bidirektionale Synchronisierung
- 🔗 Verknüpfung per `lot_nr` (Spoolman) ⇄ `uid` (SimplyPrint)
- 📊 Verbrauchs-Update (used_weight) aus SimplyPrint-Längen + Dichte/Durchmesser
- 🖥️ Web-UI für einfache Konfiguration
- 💾 SQLite-Datenbank als lokaler Cache
- 🐳 Betrieb mit oder ohne Docker

## 🚀 Schnellstart (ohne Docker)

```bash
# Repository klonen
git clone https://github.com/DEINUSER/SpoolSync.git
cd SpoolSync

# Installation als systemd Service
sudo ./scripts/install-spoolsync.sh

# Web-UI öffnen
# http://SERVER-IP:8080
```

## ⚙️ Konfiguration

**WICHTIG:** Alle Einstellungen werden im Web-UI unter `/settings` vorgenommen!

### 1. SimplyPrint einrichten

Du benötigst zwei Dinge von SimplyPrint:

#### Company/Organization ID
1. Öffne dein [SimplyPrint Panel](https://simplyprint.io/panel)
2. Schau in die Browser-URL: `https://simplyprint.io/panel/[DEINE_ID]/...`
3. Die Zahl nach `/panel/` ist deine Company ID (z.B. `123`)

#### API Token
1. Gehe zu [API Einstellungen](https://simplyprint.io/panel/user_settings/api)
2. Klicke auf "Create new API key"
3. Kopiere den generierten Token

### 2. Einstellungen im Web-UI

Öffne `http://SERVER-IP:8080/settings` und trage ein:

| Einstellung | Beispiel | Beschreibung |
|-------------|----------|--------------|
| **Spoolman API Base** | `http://127.0.0.1:7912/api/v1` | Deine Spoolman-Installation |
| **SimplyPrint API Base** | `https://api.simplyprint.io` | Standard-URL (normalerweise nicht ändern) |
| **Company ID** | `123` | Deine SimplyPrint Company ID |
| **API Token** | `sp_abc123...` | Dein SimplyPrint API Key |
| **Sync-Intervall** | `300` | Wie oft synchronisiert wird (in Sekunden) |
| **Epsilon** | `0.5` | Minimale Gewichtsdifferenz für Updates (in Gramm) |
| **Dry-Run** | ☐ | Testmodus: Nur lesen, keine Änderungen schreiben |

### 3. Verbindung testen

Klicke im Web-UI auf "Test starten" um zu prüfen, ob alles funktioniert.

## 🔄 Wie funktioniert die Synchronisierung?

1. **Filamente aus SimplyPrint laden**
   - Jedes Filament hat eine eindeutige 4-Zeichen `uid` (z.B. "PL23")
   
2. **Spulen in Spoolman abgleichen**
   - Die `uid` wird als `lot_nr` in Spoolman verwendet
   - Fehlende Spulen werden automatisch angelegt
   
3. **Verbrauch berechnen**
   - SimplyPrint speichert: `total` (Gesamtlänge) und `left` (verbleibend) in mm
   - SpoolSync berechnet: `used_weight = (total - left) / 1000 * gramm_pro_meter`
   - `gramm_pro_meter = π × (Durchmesser/20)² × 100 × Dichte`
   
4. **Updates nur bei signifikanten Änderungen**
   - Nur wenn Differenz > Epsilon-Schwellenwert
   - Verhindert unnötige API-Calls

## 📂 Umgebungsvariablen (optional)

Basis-Konfiguration kann über ENV gesetzt werden:

| Variable | Beschreibung | Default |
|----------|--------------|---------|
| `DB_PATH` | SQLite Pfad | `/var/lib/spoolsync/spoolsync.db` |
| `PORT` | HTTP-Port | `8080` |
| `TZ` | Zeitzone | `Europe/Berlin` |

**Hinweis:** API-Keys, URLs und andere Einstellungen werden im Web-UI verwaltet, nicht über ENV!

## 🔧 Verwaltung

```bash
# Status prüfen
sudo systemctl status spoolsync

# Logs ansehen
sudo journalctl -u spoolsync -f

# Service neu starten
sudo systemctl restart spoolsync

# Service stoppen
sudo systemctl stop spoolsync
```

## 🆙 Update

```bash
cd SpoolSync
git pull
sudo systemctl stop spoolsync
sudo -u spoolsync /opt/spoolsync/.venv/bin/pip install -r requirements.txt
sudo systemctl start spoolsync
```

## 💾 Backup

```bash
# Datenbank sichern
sudo ./scripts/backup-sqlite.sh

# Backup wird erstellt als: spoolsync-DATUM_UHRZEIT.db
```

## 🐳 Docker (Alternative)

```bash
cd deploy
docker-compose up -d
```

Dann über `http://localhost:8080/settings` konfigurieren.

## 🔍 Fehlerbehebung

### "SimplyPrint API Fehler: No API key provided"
- Stelle sicher, dass du einen API Token in den Einstellungen eingetragen hast
- Prüfe, ob der Token korrekt kopiert wurde (keine Leerzeichen am Anfang/Ende)

### "SimplyPrint: Verbindung fehlgeschlagen"
- Überprüfe deine Company ID - sie muss eine Zahl sein
- Teste die Verbindung mit dem "Test starten" Button
- Prüfe die Logs: `sudo journalctl -u spoolsync -f`

### "Spoolman: Verbindung fehlgeschlagen"
- Stelle sicher, dass Spoolman läuft
- Prüfe die URL (muss `/api/v1` am Ende haben)
- Bei lokalem Spoolman: `http://127.0.0.1:7912/api/v1`

### Keine Synchronisierung
- Prüfe ob Dry-Run aktiviert ist (dann werden keine Änderungen geschrieben)
- Schau in die Logs nach Fehlermeldungen
- Stelle sicher, dass die `uid` in SimplyPrint als `lot_nr` in Spoolman existiert

## 📚 API-Dokumentation

- **SimplyPrint API:** https://apidocs.simplyprint.io/
- **Spoolman API:** Siehe Spoolman-Dokumentation

## 🤝 Mitwirken

Fehler gefunden oder Verbesserungsvorschlag? Erstelle ein Issue oder Pull Request!

## 📄 Lizenz

MIT License - siehe [LICENSE](LICENSE)

---

**Hinweis für Anfänger:**
- Die Installation ist für Linux-Server gedacht
- Alle wichtigen Einstellungen werden im Web-UI gemacht
- Bei Problemen: Logs mit `sudo journalctl -u spoolsync -f` ansehen
- Die SimplyPrint Company ID ist wichtig - ohne sie funktioniert nichts!