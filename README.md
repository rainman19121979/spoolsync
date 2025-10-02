# SpoolSync

<div align="center">

**Automatische Synchronisierung zwischen SimplyPrint und Spoolman**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

[Features](#-features) • [Installation](#-schnellstart) • [Konfiguration](#️-konfiguration) • [FAQ](#-fehlerbehebung)

</div>

---

## ✨ Features

### 🔄 Intelligente Synchronisation
- **Bidirektionale Sync**: SimplyPrint ⇄ Spoolman
- **Material-Matching**: Automatische Erkennung aus SimplyPrint Types API
- **Farb-basiertes Matching**: Verschiedene Farben = verschiedene Filamente
- **Smart Weight Rounding**: 988g → 1000g, 1088g → 1100g (JAYO)
- **Verbrauchsberechnung**: Automatisch aus Länge + Dichte

### 📊 NFC-Waagen Support ✅
- **Timestamp-basiert**: Erkennt manuelle Waagen-Messungen automatisch
- **Bidirektional**: Waagen-Updates werden zu SimplyPrint synchronisiert
- **Automatische Priorität**: Neueste Messung gewinnt (Spoolman oder SimplyPrint)
- **Korrekte Prozentberechnung**: Verwendet `initial_weight` aus Spoolman
- **Status**: ✅ Vollständig implementiert und getestet

### 🎨 Filament-Verwaltung
- **Vendor-Management**: Automatische Erstellung fehlender Hersteller
- **Material-Typen**: Unterstützt PLA, PLA+, PETG, ABS, TPU, WOOD, PA-CF, etc.
- **Farberkennung**: Hex-Farben werden korrekt übernommen
- **Name-Normalisierung**: Keine doppelten Herstellernamen

### 🗑️ Automatisches Cleanup
- **Hybrid-Modus**:
  - Spulen mit Verbrauch → Archivieren
  - Unbenutzte Spulen → Löschen
- **Sync-Historie**: Timestamp-Tracking für alle Syncs

### 🖥️ Modernes Web-Interface
- **Dashboard**: Übersicht über alle Filamente und Spulen
- **Live-Stats**: Echtzeit-Counter (✓synced ✚created ↻updated ⬇archived ✗errors)
- **Progress-Bar**: Animierte Fortschrittsanzeige während Sync
- **Live-Logs**: Server-Sent Events (SSE) für Echtzeit-Log-Streaming
- **Statistiken**: Verbrauch, Anzahl, letzter Sync
- **Karten-Layout**: Moderne visuelle Darstellung mit Farbvorschau
- **Settings**: Intuitive Konfiguration mit Live-Test
- **Responsive**: Mobile-optimiert

### 🔒 Sicherheit & Zuverlässigkeit
- **Dry-Run Modus**: Testen ohne Änderungen
- **Error-Handling**: Robuste Fehlerbehandlung
- **Logging**: Detaillierte Debug-Informationen
- **PATCH statt PUT**: Korrekte Spoolman API-Nutzung

---

## 🚀 Schnellstart

### Installation als systemd Service

```bash
# Repository klonen
git clone https://github.com/rainman19121979/SpoolSync.git
cd SpoolSync

# Installer ausführbar machen
sudo chmod +x scripts/install-spoolsync.sh

# Installation (braucht sudo!)
sudo ./scripts/install-spoolsync.sh

# Web-UI öffnen
# http://SERVER-IP:8080
```

---

## ⚙️ Konfiguration

**Alle Einstellungen werden im Web-UI unter `/settings` vorgenommen!**

### 1. SimplyPrint einrichten

#### Company/Organization ID finden
1. Öffne [SimplyPrint Panel](https://simplyprint.io/panel)
2. URL ansehen: `https://simplyprint.io/panel/[DEINE_ID]/...`
3. Die Zahl nach `/panel/` ist deine ID (z.B. `123`)

#### API Token erstellen
1. Gehe zu [API Einstellungen](https://simplyprint.io/panel/user_settings/api)
2. "Create new API key" → Token kopieren

### 2. Settings konfigurieren

Öffne `http://SERVER-IP:8080/settings`:

| Einstellung | Beispiel | Beschreibung |
|-------------|----------|--------------|
| **Spoolman API** | `http://127.0.0.1:7912/api/v1` | Deine Spoolman-Installation |
| **SimplyPrint API** | `https://api.simplyprint.io` | Standard-URL |
| **Company ID** | `123` | Deine SimplyPrint Company ID |
| **API Token** | `sp_abc123...` | Dein API Key |
| **Sync-Intervall** | `300` | Sekunden zwischen Syncs |
| **Epsilon** | `0.5` | Minimale Gewichtsdifferenz (Gramm) |
| **Dry-Run** | ☐ | Testmodus |

### 3. Verbindung testen

Klicke auf **"Test starten"** um die Verbindung zu prüfen:
- ✓ Spoolman: Verbindung erfolgreich
- ✓ SimplyPrint: Verbindung erfolgreich

---

## 🔄 Wie funktioniert die Synchronisierung?

### Standard-Sync (SimplyPrint → Spoolman)

1. **Filament-Types laden**: Material, Dichte, Durchmesser aus SimplyPrint Types API
2. **Filamente matchen**: Nach Material + Durchmesser + Marke + **Farbe**
3. **Spulen synchronisieren**: `uid` (SimplyPrint) → `lot_nr` (Spoolman)
4. **Verbrauch berechnen**:
   ```
   used_weight = (total_length - left_length) / 1000 × gramm_pro_meter
   gramm_pro_meter = π × (diameter/20)² × 100 × density
   ```
5. **Initial Weight runden**: 988g → 1000g, 1088g → 1100g (nur JAYO)

### Bidirektionale Sync (bei NFC-Waage)

1. **Timestamp-Vergleich**: Ist Spoolman neuer als letzter Sync?
2. **Falls ja**:
   - Behalte Spoolman-Wert (Waagen-Messung)
   - Berechne verbleibende Länge zurück
   - Aktualisiere SimplyPrint mit korrigiertem Wert
3. **Log**: `"Spoolman-Wert ist neuer (Waagen-Messung?)"`

### Cleanup (gelöschte Filamente)

- **Mit Verbrauch** (`used_weight > 0`): Archivieren in Spoolman
- **Ohne Verbrauch** (`used_weight == 0`): Löschen aus Spoolman

---

## 📊 Dashboard-Features

### Statistiken
- 📦 Anzahl Filamente
- 🎨 Anzahl Spulen (aktiv/archiviert)
- ⚖️ Gesamtverbrauch in Gramm
- 🔄 Letzter Sync (relative Zeit: "5 Min", "2 Std")

### Live-Sync-Status
- **Aktueller Status**: ✅ Bereit / 🔄 Läuft / ❌ Fehler
- **Progress-Bar**: Animierte Anzeige während Sync läuft
- **Aktueller Schritt**: Was gerade synchronisiert wird
- **Nächster Sync**: Countdown bis zum nächsten automatischen Sync
- **Live-Stats** (mit Tooltip-Erklärungen):
  - ✓ Synchronisiert - Erfolgreich synchronisierte Filamente
  - ✚ Erstellt - Neu erstellte Spulen in Spoolman
  - ↻ Aktualisiert - Aktualisierte existierende Spulen
  - ⬇ Archiviert - Archivierte Spulen
  - ✗ Fehler - Fehler während Synchronisierung

### Filamente-Ansicht
- Große Farbvorschau (48×48px Kreis)
- Material, Marke, Durchmesser, Dichte
- Hex-Farbcode
- Hover-Effekte mit Border-Highlight

### Spulen-Ansicht
- Lot-Nr. mit Filament-Name
- Material, Marke, Farbe
- Verbrauch und Spulengewicht
- Status-Badges (Aktiv/Archiviert)
- Hover-Effekte

### Logs-Seite
- **Live-Stream**: Server-Sent Events für Echtzeit-Updates
- **Filter**: Nach Log-Level (Error, Warning, Info, Debug)
- **Farbcodierung**: Fehler rot, Warnings gelb, etc.
- **Auto-Scroll**: Automatisches Scrollen zu neuen Einträgen

---

## 🔧 Verwaltung

### Service-Befehle

```bash
# Status prüfen
sudo systemctl status spoolsync

# Logs ansehen (Live)
sudo journalctl -u spoolsync -f

# Logs ansehen (letzte 100 Zeilen)
sudo journalctl -u spoolsync -n 100

# Service neu starten
sudo systemctl restart spoolsync

# Service stoppen/starten
sudo systemctl stop spoolsync
sudo systemctl start spoolsync
```

### Logs

Logs befinden sich in:
- **systemd**: `sudo journalctl -u spoolsync`
- **Datei**: `/var/log/spoolsync/app.log`
- **Fehler**: `/var/log/spoolsync/app.err`

### Manueller Sync

```bash
# Via Web-UI
curl -X POST http://localhost:8080/sync

# Via Python
cd /opt/spoolsync
sudo -u spoolsync .venv/bin/python -m app.sync
```

---

## 🆙 Update

```bash
# Ins Repository-Verzeichnis wechseln
cd ~/SpoolSync

# Neueste Änderungen holen
git pull origin main

# Service stoppen
sudo systemctl stop spoolsync

# Dependencies updaten (falls requirements.txt geändert)
sudo -u spoolsync /opt/spoolsync/.venv/bin/pip install -r spoolsync/requirements.txt

# Code kopieren
sudo cp -r spoolsync/app /opt/spoolsync/

# Service starten
sudo systemctl start spoolsync

# Status prüfen
sudo systemctl status spoolsync

# Erste Logs nach Update ansehen
sudo journalctl -u spoolsync -n 50
```

### ⚠️ Nach dem Update

- Dashboard öffnen: `http://SERVER-IP:8080`
- Stats überprüfen (sollten sich nach erstem Sync füllen)
- Logs auf Fehler prüfen: `sudo journalctl -u spoolsync -f`

---

## 💾 Backup

```bash
# Datenbank sichern
sudo cp /opt/spoolsync/spoolsync.db ~/spoolsync-backup-$(date +%Y%m%d).db

# Mit Script
sudo ./scripts/backup-sqlite.sh
```

---

## 🔍 Fehlerbehebung

### SimplyPrint-Fehler

**"No API key provided"**
- API Token fehlt oder ungültig
- In Settings prüfen und neu eingeben

**"Company ID falsch"**
- Nur Zahlen erlaubt (z.B. `123`)
- Browser-URL prüfen: `/panel/[ID]/`

**"Verbindung fehlgeschlagen"**
- Firewall blockiert?
- API Token abgelaufen?
- Test-Button verwenden!

### Spoolman-Fehler

**"405 Method Not Allowed"**
- ✅ **Gefixt!** (PATCH statt PUT)
- Update auf neueste Version

**"Verbindung fehlgeschlagen"**
- Spoolman läuft nicht
- URL falsch (muss `/api/v1` enthalten)
- Port geschlossen

### Sync-Probleme

**Keine Synchronisierung**
- Dry-Run Modus aktiv? → Ausschalten
- Logs prüfen: `sudo journalctl -u spoolsync -f`
- Test-Button in Settings verwenden

**Stats zeigen alle 0**
- Service neu starten: `sudo systemctl restart spoolsync`
- Nach erstem Sync sollten Stats erscheinen
- Dashboard aktualisiert alle 3 Sekunden

**Prozent-Anzeige >100% in SimplyPrint**
- ✅ **Gefixt!** Verwendet jetzt `initial_weight` aus Spoolman
- Bei alten Spulen: Einmal manuell in Spoolman Gewicht setzen

**"Database is locked" Fehler**
- ✅ **Gefixt!** DB-Connection wird pro Filament geöffnet/geschlossen
- Sollte nicht mehr auftreten ab Commit 34b05f5

**SimplyPrint Felder verschwinden (brand_id, etc.)**
- ✅ **Gefixt!** Alle Felder werden jetzt übernommen
- Update auf neueste Version

**Material wird nicht erkannt**
- Types API wird automatisch geladen
- Material-Typ kommt aus `material_type_name`
- Unterstützt: PLA, PLA+, PETG, ABS, TPU, WOOD, PA-CF, etc.

**Doppelte Herstellernamen** ("JAYO JAYO PETG")
- ✅ **Gefixt!** Name enthält nur Material + Farbe
- Brand ist separates Feld

**Farben werden nicht unterschieden**
- ✅ **Gefixt!** Farb-basiertes Matching aktiv
- JAYO PLA Black ≠ JAYO PLA Red

---

## 📚 API-Dokumentation

- **SimplyPrint**: https://apidocs.simplyprint.io/
- **Spoolman**: https://github.com/Donkie/Spoolman
- **SpoolSync Endpoints**:
  - `GET /` - Dashboard
  - `GET /logs` - Live-Logs Viewer
  - `GET /settings` - Einstellungen
  - `GET /status` - Sync-Status (JSON)
  - `GET /api/logs` - Log-Einträge abrufen
  - `GET /api/logs/stream` - Live-Log-Stream (SSE)
  - `POST /sync` - Manueller Sync
  - `POST /settings/test` - Verbindungstest
  - `GET /health` - Health Check

---

## 🤝 Mitwirken

Fehler gefunden? Feature-Wunsch?

1. Issue erstellen: [GitHub Issues](https://github.com/rainman19121979/SpoolSync/issues)
2. Pull Request einreichen
3. Dokumentation verbessern

---

## 📄 Lizenz

MIT License - siehe [LICENSE](LICENSE)

---

## 🙏 Credits

- **SimplyPrint**: https://simplyprint.io/
- **Spoolman**: https://github.com/Donkie/Spoolman
- **FastAPI**: https://fastapi.tiangolo.com/
- **Entwickelt mit Unterstützung von**: [Claude Code](https://claude.com/claude-code) (Anthropic)

---

<div align="center">

**Made with ❤️ for the 3D printing community**

[⬆ Nach oben](#spoolsync)

</div>