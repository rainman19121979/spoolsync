import os, sqlite3, datetime as dt
DB_PATH = os.getenv("DB_PATH","/var/lib/spoolsync/spoolsync.db")

DEFAULTS = {
  "SPOOLMAN_BASE": "http://127.0.0.1:7912/api/v1",
  "SP_BASE": "https://api.simplyprint.io",
  "SP_COMPANY_ID": "",  # Muss vom Benutzer gesetzt werden
  "SYNC_INTERVAL_SECONDS": "300",
  "EPSILON_GRAMS": "0.5",
  "DRY_RUN": "false",
}

def now(): return dt.datetime.utcnow().isoformat()

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    with c:
        c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS secrets(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    return c

def get(key, default=None):
    with _conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if r: return r["value"]
    return DEFAULTS.get(key, default)

def set(key, value):
    with _conn() as c:
        c.execute("""INSERT INTO settings(key,value,updated_at)
                     VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET
                     value=excluded.value, updated_at=excluded.updated_at""",
                  (key, value, now()))

def get_secret(key, default=""):
    with _conn() as c:
        r = c.execute("SELECT value FROM secrets WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

def set_secret(key, value):
    with _conn() as c:
        c.execute("""INSERT INTO secrets(key,value,updated_at)
                     VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET
                     value=excluded.value, updated_at=excluded.updated_at""",
                  (key, value, now()))