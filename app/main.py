import os, asyncio, datetime as dt
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from .db import init_db, get_session, Session
from .web import templates
from .sync import start_scheduler, reconfigure_scheduler, run_sync_once
from . import settings as S
from .clients import SpoolmanClient, SimplyPrintClient

app = FastAPI(title="SpoolSync")
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

@app.on_event("startup")
async def startup():
    init_db()
    start_scheduler()

@app.get("/health")
def health(): return {"ok": True, "time": dt.datetime.utcnow().isoformat()}

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = get_session()):
    with session as db:
        filaments = db.execute("SELECT id,name,brand,material,diameter_mm FROM filament ORDER BY updated_at DESC LIMIT 50").fetchall()
        spools = db.execute("SELECT id,lot_nr,used_weight_g,archived FROM spool ORDER BY updated_at DESC LIMIT 50").fetchall()
    return templates.TemplateResponse("dashboard.html", {"request": request, "filaments": filaments, "spools": spools})

@app.get("/settings", response_class=HTMLResponse)
def settings_view(request: Request):
    data = {
        "SPOOLMAN_BASE": S.get("SPOOLMAN_BASE"),
        "SP_BASE": S.get("SP_BASE"),
        "SYNC_INTERVAL_SECONDS": S.get("SYNC_INTERVAL_SECONDS"),
        "EPSILON_GRAMS": S.get("EPSILON_GRAMS"),
        "DRY_RUN": S.get("DRY_RUN","false"),
        "SP_TOKEN_SET": bool(S.get_secret("SP_TOKEN")),
    }
    return templates.TemplateResponse("settings.html", {"request": request, "cfg": data})

@app.post("/settings")
def settings_save(
    SPOOLMAN_BASE: str = Form(...),
    SP_BASE: str = Form(...),
    SYNC_INTERVAL_SECONDS: int = Form(...),
    EPSILON_GRAMS: float = Form(...),
    DRY_RUN: str = Form("false"),
    SP_TOKEN: str = Form(""),
):
    S.set("SPOOLMAN_BASE", SPOOLMAN_BASE.strip())
    S.set("SP_BASE", SP_BASE.strip())
    S.set("SYNC_INTERVAL_SECONDS", str(max(30, int(SYNC_INTERVAL_SECONDS))))
    S.set("EPSILON_GRAMS", f"{max(0.01, float(EPSILON_GRAMS)):.2f}")
    S.set("DRY_RUN", "true" if DRY_RUN=="true" else "false")
    if SP_TOKEN.strip(): S.set_secret("SP_TOKEN", SP_TOKEN.strip())
    reconfigure_scheduler()
    return RedirectResponse("/settings?saved=1", status_code=303)

@app.post("/settings/test")
async def settings_test():
    ok = {"spoolman": False, "simplyprint": False, "msg": ""}
    try:
        await SpoolmanClient().list_spools(); ok["spoolman"] = True
    except Exception as e: ok["msg"] += f"Spoolman: {e} "
    try:
        await SimplyPrintClient().list_filaments(); ok["simplyprint"] = True
    except Exception as e: ok["msg"] += f"SimplyPrint: {e}"
    return ok
