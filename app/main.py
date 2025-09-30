import os, datetime as dt
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .db import init_db, get_session
from .web import templates
from .sync import start_scheduler, reconfigure_scheduler, run_sync_once
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
def dashboard(request: Request, db=Depends(get_db)):
    # Filamente mit mehr Details
    filaments = db.execute(
        "SELECT id, name, brand, material, diameter_mm, density_g_cm3, color_hex, created_at, updated_at "
        "FROM filament "
        "ORDER BY updated_at DESC LIMIT 50"
    ).fetchall()

    # Spulen mit Filament-Namen und mehr Details
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

    # Statistiken berechnen
    stats = {
        "total_filaments": db.execute("SELECT COUNT(*) as count FROM filament").fetchone()["count"],
        "total_spools": db.execute("SELECT COUNT(*) as count FROM spool").fetchone()["count"],
        "active_spools": db.execute("SELECT COUNT(*) as count FROM spool WHERE archived = 0").fetchone()["count"],
        "archived_spools": db.execute("SELECT COUNT(*) as count FROM spool WHERE archived = 1").fetchone()["count"],
        "total_used_weight": db.execute("SELECT COALESCE(SUM(used_weight_g), 0) as total FROM spool").fetchone()["total"],
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