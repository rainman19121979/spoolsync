import os, datetime as dt
import json
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
import asyncio
from pathlib import Path

from .db import init_db, get_session
from .web import templates
from .sync import start_scheduler, reconfigure_scheduler, run_sync_once, sync_status
from . import settings as S
from .clients import SpoolmanClient, SimplyPrintClient

app = FastAPI(title="SpoolSync")

# Static dir sicherstellen
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# -------------------------------------------------------------------
# Helpers / Dependencies
# -------------------------------------------------------------------

def get_db():
    """FastAPI Dependency: gibt eine DB-Connection, schließt nach Request."""
    with get_session() as db:
        yield db


# -------------------------------------------------------------------
# Events
# -------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    init_db()
    start_scheduler()


# -------------------------------------------------------------------
# Routes
# -------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True, "time": dt.datetime.utcnow().isoformat()}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # Hole Live-Daten von Spoolman für korrekte Statistiken
    try:
        smc = SpoolmanClient()
        spoolman_spools = await smc.list_spools()

        # Zähle aktive und archivierte Spulen direkt von Spoolman
        active_count = sum(1 for s in spoolman_spools if not s.get("archived", False))
        archived_count = sum(1 for s in spoolman_spools if s.get("archived", False))
        total_used = sum(s.get("used_weight", 0) for s in spoolman_spools)
        total_spools = len(spoolman_spools)
        spoolman_ok = True
    except:
        # Fallback: Wird später aus DB geholt
        spoolman_ok = False
        active_count = 0
        archived_count = 0
        total_used = 0
        total_spools = 0

    # Hole DB-Daten NACH dem await (neuer Thread-Context)
    with get_session() as db:
        # Filamente aus Cache (für Performance)
        filaments = db.execute(
            "SELECT id, name, brand, material, diameter_mm, density_g_cm3, color_hex, created_at, updated_at "
            "FROM filament "
            "ORDER BY updated_at DESC LIMIT 50"
        ).fetchall()

        # Spulen aus Cache (für Performance)
        spools = db.execute(
            """
            SELECT
                s.id, s.lot_nr, s.used_weight_g, s.spool_weight_g,
                s.price_eur, s.archived, s.source, s.created_at, s.updated_at,
                f.name as filament_name, f.brand, f.material, f.color_hex
            FROM spool s
            LEFT JOIN filament f ON s.filament_id = f.id
            ORDER BY s.updated_at DESC LIMIT 50
            """
        ).fetchall()

        # Fallback auf Cache wenn Spoolman nicht erreichbar
        if not spoolman_ok:
            active_count = db.execute("SELECT COUNT(*) as count FROM spool WHERE archived = 0").fetchone()["count"]
            archived_count = db.execute("SELECT COUNT(*) as count FROM spool WHERE archived = 1").fetchone()["count"]
            total_used = db.execute("SELECT COALESCE(SUM(used_weight_g), 0) as total FROM spool").fetchone()["total"]
            total_spools = db.execute("SELECT COUNT(*) as count FROM spool").fetchone()["count"]

        # Statistiken mit Live-Daten von Spoolman (oder Fallback Cache)
        stats = {
            "total_filaments": db.execute("SELECT COUNT(*) as count FROM filament").fetchone()["count"],
            "total_spools": total_spools,
            "active_spools": active_count,
            "archived_spools": archived_count,
            "total_used_weight": total_used,
            "last_sync": S.get("LAST_SYNC_TIME", "0"),
        }

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "filaments": filaments,
            "spools": spools,
            "stats": stats,
        },
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_view(request: Request):
    data = {
        "SPOOLMAN_BASE": S.get("SPOOLMAN_BASE"),
        "SP_BASE": S.get("SP_BASE"),
        "SP_COMPANY_ID": S.get("SP_COMPANY_ID"),
        "SYNC_INTERVAL_SECONDS": S.get("SYNC_INTERVAL_SECONDS"),
        "EPSILON_GRAMS": S.get("EPSILON_GRAMS"),
        "DRY_RUN": S.get("DRY_RUN", "false"),
        "SP_TOKEN_SET": bool(S.get_secret("SP_TOKEN")),
    }
    return templates.TemplateResponse("settings.html", {"request": request, "cfg": data})


@app.post("/settings")
def settings_save(
    SPOOLMAN_BASE: str = Form(...),
    SP_BASE: str = Form(...),
    SP_COMPANY_ID: str = Form(...),
    SYNC_INTERVAL_SECONDS: int = Form(...),
    EPSILON_GRAMS: float = Form(...),
    DRY_RUN: str = Form("false"),
    SP_TOKEN: str = Form(""),
):
    S.set("SPOOLMAN_BASE", SPOOLMAN_BASE.strip())
    S.set("SP_BASE", SP_BASE.strip())
    S.set("SP_COMPANY_ID", SP_COMPANY_ID.strip())
    S.set("SYNC_INTERVAL_SECONDS", str(max(30, int(SYNC_INTERVAL_SECONDS))))
    S.set("EPSILON_GRAMS", f"{max(0.01, float(EPSILON_GRAMS)):.2f}")
    S.set("DRY_RUN", "true" if DRY_RUN == "true" else "false")
    if SP_TOKEN.strip():
        S.set_secret("SP_TOKEN", SP_TOKEN.strip())
    reconfigure_scheduler()
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/settings/test")
async def settings_test():
    ok = {"spoolman": False, "simplyprint": False, "msg": ""}

    # Test Spoolman
    try:
        smc = SpoolmanClient()
        await smc.list_spools()
        ok["spoolman"] = True
    except Exception as e:
        ok["msg"] += f"Spoolman Fehler: {str(e)[:100]}... "

    # Test SimplyPrint
    try:
        spc = SimplyPrintClient()
        result = await spc.test_connection()
        ok["simplyprint"] = result
        if not result:
            ok["msg"] += "SimplyPrint: API-Key ungültig oder Company ID falsch. "
    except Exception as e:
        ok["msg"] += f"SimplyPrint Fehler: {str(e)[:100]}... "

    return ok


@app.post("/sync")
async def sync_now():
    """Manueller Sync-Trigger (z. B. Button im UI)."""
    await run_sync_once()
    return {"ok": True, "ts": dt.datetime.utcnow().isoformat()}


@app.get("/favicon.ico")
def favicon():
    # verhindert 500/404 Spam bei Browser-Icon-Anfragen
    return Response(status_code=204)


@app.get("/status")
async def get_status():
    """Gibt aktuellen Sync-Status zurück."""
    return sync_status.get_status()


@app.get("/logs", response_class=HTMLResponse)
async def logs_view(request: Request):
    """Zeigt Log-Viewer Seite."""
    return templates.TemplateResponse("logs.html", {"request": request})


@app.get("/api/logs")
async def get_logs(lines: int = 200, level: str = "all"):
    """
    Holt die letzten N Zeilen aus den Log-Dateien.

    Args:
        lines: Anzahl Zeilen (default: 200)
        level: Filter (all, error, warning, info, debug)
    """
    log_entries = []

    # Log-Dateien (Reihenfolge: erst app.err, dann app.log)
    log_files = [
        Path("/var/log/spoolsync/app.err"),
        Path("/var/log/spoolsync/app.log"),
    ]

    # Fallback für Development (wenn /var/log nicht existiert)
    if not log_files[0].exists():
        log_files = [
            Path("app.err"),
            Path("app.log"),
        ]

    for log_file in log_files:
        if not log_file.exists():
            continue

        try:
            # Lese letzte N Zeilen
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                file_lines = f.readlines()

            # Nimm die letzten 'lines' Zeilen
            recent_lines = file_lines[-lines:] if len(file_lines) > lines else file_lines

            for line in recent_lines:
                line = line.strip()
                if not line:
                    continue

                # Parse Log-Level
                log_level = "info"
                if " - ERROR - " in line or "ERROR:" in line:
                    log_level = "error"
                elif " - WARNING - " in line or "WARNING:" in line:
                    log_level = "warning"
                elif " - DEBUG - " in line or "DEBUG:" in line:
                    log_level = "debug"

                # Filter nach Level
                if level != "all" and log_level != level.lower():
                    continue

                log_entries.append({
                    "timestamp": line.split(" - ")[0] if " - " in line else "",
                    "level": log_level,
                    "message": line,
                })
        except Exception as e:
            log_entries.append({
                "timestamp": dt.datetime.now().isoformat(),
                "level": "error",
                "message": f"Fehler beim Lesen von {log_file}: {str(e)}",
            })

    # Sortiere nach Timestamp (neueste zuerst)
    log_entries.reverse()

    return {
        "logs": log_entries[:lines],
        "total": len(log_entries),
    }


@app.get("/api/logs/stream")
async def stream_logs(request: Request):
    """
    Server-Sent Events für Live-Log-Updates.
    """
    async def event_generator():
        log_file = Path("/var/log/spoolsync/app.err")

        # Fallback für Development
        if not log_file.exists():
            log_file = Path("app.log")

        # Starte am Ende der Datei
        if log_file.exists():
            with open(log_file, "r") as f:
                f.seek(0, 2)  # Gehe zum Ende
                position = f.tell()
        else:
            position = 0

        while True:
            # Check ob Client noch verbunden ist
            if await request.is_disconnected():
                break

            try:
                if log_file.exists():
                    with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(position)
                        new_lines = f.readlines()
                        position = f.tell()

                        for line in new_lines:
                            line = line.strip()
                            if line:
                                # Parse Log-Level
                                log_level = "info"
                                if " - ERROR - " in line:
                                    log_level = "error"
                                elif " - WARNING - " in line:
                                    log_level = "warning"
                                elif " - DEBUG - " in line:
                                    log_level = "debug"

                                data = json.dumps({"level": log_level, "message": line})
                                yield f"data: {data}\n\n"

                # Sende Heartbeat alle 15 Sekunden
                yield f": heartbeat\n\n"

            except Exception as e:
                data = json.dumps({"level": "error", "message": f"Stream error: {str(e)}"})
                yield f"data: {data}\n\n"

            await asyncio.sleep(2)  # Check alle 2 Sekunden

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )