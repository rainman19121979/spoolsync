import os, sqlite3, datetime as dt
from contextlib import contextmanager
DB_PATH = os.getenv("DB_PATH","/var/lib/spoolsync/spoolsync.db")

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS filament(
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          brand TEXT, material TEXT,
          diameter_mm REAL, density_g_cm3 REAL,
          color_hex TEXT, nominal_weight_g INTEGER,
          created_at TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS spool(
          id INTEGER PRIMARY KEY,
          filament_id INTEGER NOT NULL,
          lot_nr TEXT UNIQUE,
          spool_weight_g REAL, price_eur REAL,
          used_weight_g REAL DEFAULT 0,
          archived INTEGER DEFAULT 0,
          source TEXT,
          created_at TEXT, updated_at TEXT,
          FOREIGN KEY(filament_id) REFERENCES filament(id)
        );
        CREATE TABLE IF NOT EXISTS external_link(
          id INTEGER PRIMARY KEY,
          local_type TEXT NOT NULL, local_id INTEGER NOT NULL,
          system TEXT NOT NULL, external_id TEXT NOT NULL,
          etag TEXT, last_seen TEXT,
          UNIQUE(local_type, local_id, system),
          UNIQUE(system, external_id)
        );
        CREATE TABLE IF NOT EXISTS change_log(
          id INTEGER PRIMARY KEY,
          entity TEXT, entity_id INTEGER,
          field TEXT, old_value TEXT, new_value TEXT,
          source TEXT, ts TEXT
        );
        """)

def now(): return dt.datetime.utcnow().isoformat()

@contextmanager
def get_session():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

Session = get_session

def upsert_filament(conn, f):
    row = conn.execute("SELECT id FROM filament WHERE name=? AND IFNULL(material,'')=? AND IFNULL(diameter_mm,0)=?",
                       (f["name"], f.get("material",""), f.get("diameter_mm",0))).fetchone()
    ts = now()
    if row:
        conn.execute("""UPDATE filament SET brand=?, density_g_cm3=?, color_hex=?, nominal_weight_g=?, updated_at=? WHERE id=?""",
                     (f.get("brand"), f.get("density_g_cm3"), f.get("color_hex"), f.get("nominal_weight_g"), ts, row["id"]))
        return row["id"]
    cur = conn.execute("""INSERT INTO filament(name,brand,material,diameter_mm,density_g_cm3,color_hex,nominal_weight_g,created_at,updated_at)
                          VALUES(?,?,?,?,?,?,?, ?, ?)""",
                       (f["name"], f.get("brand"), f.get("material"), f.get("diameter_mm"), f.get("density_g_cm3"),
                        f.get("color_hex"), f.get("nominal_weight_g"), ts, ts))
    return cur.lastrowid

def upsert_spool(conn, s):
    row = conn.execute("SELECT id FROM spool WHERE lot_nr=?", (s.get("lot_nr"),)).fetchone() if s.get("lot_nr") else None
    ts = now()
    if row:
        conn.execute("""UPDATE spool SET filament_id=?, spool_weight_g=?, price_eur=?, used_weight_g=?, archived=?, source=?, updated_at=? WHERE id=?""",
                     (s["filament_id"], s.get("spool_weight_g"), s.get("price_eur"),
                      s.get("used_weight_g",0), int(bool(s.get("archived",0))), s.get("source"), ts, row["id"]))
        return row["id"]
    cur = conn.execute("""INSERT INTO spool(filament_id,lot_nr,spool_weight_g,price_eur,used_weight_g,archived,source,created_at,updated_at)
                          VALUES(?,?,?,?,?,?,?, ?, ?)""",
                       (s["filament_id"], s.get("lot_nr"), s.get("spool_weight_g"), s.get("price_eur"),
                        s.get("used_weight_g",0), int(bool(s.get("archived",0))), s.get("source"), ts, ts))
    return cur.lastrowid
