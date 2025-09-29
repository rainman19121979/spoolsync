import math
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .db import get_session, upsert_filament, upsert_spool
from .clients import SpoolmanClient, SimplyPrintClient
from . import settings as S

def EPS(): return float(S.get("EPSILON_GRAMS","0.5"))
_scheduler = None

def grams_per_meter(density_g_cm3: float, diameter_mm: float):
    if not density_g_cm3 or not diameter_mm: return None
    r_cm = (diameter_mm/10)/2
    vol_cm3 = math.pi * r_cm * r_cm * 100
    return round(vol_cm3 * density_g_cm3, 2)

async def run_sync_once():
    spc = SimplyPrintClient()
    smc = SpoolmanClient()

    # 1) Daten laden (robust)
    try:
        sp_resp = await spc.list_filaments()
        sm_spools = await smc.list_spools()
    except Exception as e:
        print("[SYNC] fetch error:", e)
        return

    # Normalize SimplyPrint response -> sp_filaments: list[dict]
    sp_filaments = []
    if isinstance(sp_resp, list):
        sp_filaments = sp_resp
    elif isinstance(sp_resp, dict):
        for key in ("filaments", "data", "items", "result", "results"):
            val = sp_resp.get(key)
            if isinstance(val, list):
                sp_filaments = val
                break
    else:
        sp_filaments = []

    if not isinstance(sp_filaments, list):
        sp_filaments = []

    # 2) Map lot_nr -> Spoolman spool
    lot_map = {}
    if isinstance(sm_spools, list):
        lot_map = {s.get("lot_nr"): s for s in sm_spools if isinstance(s, dict) and s.get("lot_nr")}

    # 3) Durchgehen
    with get_session() as sess:
        for f in sp_filaments:
            if not isinstance(f, dict):
                # Unerwarteter Eintragstyp – überspringen
                # print("[SYNC] skipping non-dict filament:", repr(f))
                continue

            # Keys defensiv lesen
            uid = f.get("uid") or f.get("id") or f.get("uuid")
            if not uid:
                # Ohne UID keine 1:1-Zuordnung
                continue

            dia = f.get("dia") or f.get("diameter") or 1.75
            density = f.get("density") or 1.24

            filament_id = upsert_filament(
                sess,
                {
                    "name": f.get("name", "Unknown"),
                    "brand": f.get("brand"),
                    "material": f.get("material"),
                    "diameter_mm": dia,
                    "density_g_cm3": density,
                    "color_hex": f.get("color"),
                    "nominal_weight_g": f.get("weight_g") or f.get("nominal_weight_g"),
                },
            )

            # Wenn Spule in Spoolman fehlt und nicht Dry-Run: anlegen
            if uid not in lot_map and S.get("DRY_RUN", "false") != "true":
                try:
                    sm_fil = await smc.create_filament(
                        {"name": f.get("name", "Unknown"), "diameter": dia, "density": density}
                    )
                    await smc.create_spool(
                        {
                            "filament_id": sm_fil.get("id"),
                            "lot_nr": uid,
                            "spool_weight": 250,
                            "price": 0,
                            "used_weight": 0,
                            "archived": False,
                        }
                    )
                except Exception as e:
                    print("[SYNC] create in Spoolman failed:", e)

            # Verbrauch berechnen/angleichen
            total = f.get("total")
            left = f.get("left")
            if total is not None and left is not None and uid in lot_map:
                try:
                    length_used_mm = max(0.0, float(total) - float(left))
                except Exception:
                    # falls total/left keine Zahlen sind
                    length_used_mm = 0.0

                gpm = grams_per_meter(density, dia) or 2.98
                used_g = round((length_used_mm / 1000.0) * gpm, 2)

                sm_spool = lot_map[uid]
                cur_used = float(sm_spool.get("used_weight") or 0)

                if abs(used_g - cur_used) > float(S.get("EPSILON_GRAMS", "0.5")) and S.get("DRY_RUN", "false") != "true":
                    try:
                        # defensiv: filament id in Spoolman-Spule kann verschachtelt sein
                        sm_filament_id = (
                            (sm_spool.get("filament") or {}).get("id")
                            if isinstance(sm_spool.get("filament"), dict)
                            else sm_spool.get("filament_id")
                        )
                        await smc.update_spool(
                            sm_spool.get("id"),
                            {
                                "filament_id": sm_filament_id,
                                "price": sm_spool.get("price"),
                                "spool_weight": sm_spool.get("spool_weight"),
                                "archived": sm_spool.get("archived", False),
                                "lot_nr": sm_spool.get("lot_nr"),
                                "used_weight": used_g,
                            },
                        )
                    except Exception as e:
                        print("[SYNC] update used_weight failed:", e)

                # lokale DB updaten
                upsert_spool(
                    sess,
                    {
                        "filament_id": filament_id,
                        "lot_nr": uid,
                        "spool_weight_g": sm_spool.get("spool_weight") if uid in lot_map else None,
                        "price_eur": sm_spool.get("price") if uid in lot_map else None,
                        "used_weight_g": used_g if uid in lot_map else 0,
                        "archived": sm_spool.get("archived", False) if uid in lot_map else 0,
                        "source": "spoolman",
                    },
                )

def start_scheduler():
    global _scheduler
    if _scheduler: return
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(run_sync_once, "interval", seconds=int(S.get("SYNC_INTERVAL_SECONDS","300")))
    _scheduler.start()

def reconfigure_scheduler():
    if not _scheduler: return
    for j in list(_scheduler.get_jobs()): _scheduler.remove_job(j.id)
    _scheduler.add_job(run_sync_once, "interval", seconds=int(S.get("SYNC_INTERVAL_SECONDS","300")))
