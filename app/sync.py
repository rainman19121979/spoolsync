import math
import logging
from typing import Optional, Dict, List, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .db import get_session, upsert_filament, upsert_spool
from .clients import SpoolmanClient, SimplyPrintClient
from . import settings as S

# Logging konfigurieren
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_scheduler = None


def EPS() -> float:
    """Epsilon-Schwellenwert für Gewichtsvergleiche."""
    return float(S.get("EPSILON_GRAMS", "0.5"))


def grams_per_meter(density_g_cm3: float, diameter_mm: float) -> Optional[float]:
    """Berechnet Gramm pro Meter Filament."""
    if not density_g_cm3 or not diameter_mm:
        return None
    r_cm = (diameter_mm / 10) / 2
    vol_cm3 = math.pi * r_cm * r_cm * 100
    return round(vol_cm3 * density_g_cm3, 2)


def normalize_color(color: Any) -> Optional[str]:
    """Normalisiert Farbangaben zu Hex-Format."""
    if not color:
        return None
    color_str = str(color).strip()
    if color_str.startswith('#'):
        return color_str
    if len(color_str) == 6:
        return f"#{color_str}"
    return None


def extract_filament_data(sp_filament: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extrahiert und normalisiert Filament-Daten aus SimplyPrint.
    
    SimplyPrint API Struktur:
    - uid: 4-Zeichen Code (z.B. "PL23")
    - type: {id: int, name: str} oder einfach string
    - dia: Durchmesser (diameter)
    - density: Dichte
    - total: Gesamtlänge in mm
    - left: Verbleibende Länge in mm
    """
    # Type kann ein Objekt oder String sein
    material_type = sp_filament.get("type")
    if isinstance(material_type, dict):
        material = material_type.get("name", "Unknown")
    else:
        material = str(material_type) if material_type else "Unknown"
    
    return {
        "uid": sp_filament.get("uid"),  # 4-Zeichen Code
        "name": f"{sp_filament.get('brand', 'Unknown')} {material} {sp_filament.get('colorName', '')}".strip(),
        "brand": sp_filament.get("brand"),
        "material": material,
        "diameter_mm": float(sp_filament.get("dia", 1.75)),
        "density_g_cm3": float(sp_filament.get("density", 1.24)),
        "color_hex": normalize_color(sp_filament.get("colorHex")),
        "nominal_weight_g": None,  # SimplyPrint hat kein Gewicht direkt
        "total_length_mm": sp_filament.get("total"),  # Gesamtlänge in mm
        "left_length_mm": sp_filament.get("left"),   # Verbleibend in mm
    }


async def ensure_spoolman_spool(
    smc: SpoolmanClient,
    uid: str,
    filament_data: Dict[str, Any],
    lot_map: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Stellt sicher, dass eine Spule in Spoolman existiert.
    Erstellt sie bei Bedarf, wenn nicht im Dry-Run-Modus.
    """
    if uid in lot_map:
        return lot_map[uid]
    
    if S.get("DRY_RUN", "false") == "true":
        logger.info(f"[DRY-RUN] Würde Spule mit lot_nr={uid} in Spoolman erstellen")
        return None
    
    try:
        # Filament in Spoolman erstellen
        sm_fil_payload = {
            "name": filament_data["name"],
            "diameter": filament_data["diameter_mm"],
            "density": filament_data["density_g_cm3"],
        }
        if filament_data.get("material"):
            sm_fil_payload["material"] = filament_data["material"]
        if filament_data.get("brand"):
            sm_fil_payload["vendor"] = {"name": filament_data["brand"]}
        if filament_data["color_hex"]:
            sm_fil_payload["color_hex"] = filament_data["color_hex"]
        
        sm_fil = await smc.create_filament(sm_fil_payload)
        logger.info(f"Filament erstellt in Spoolman: {sm_fil.get('id')} - {filament_data['name']}")
        
        # Spule in Spoolman erstellen
        sm_spool = await smc.create_spool({
            "filament_id": sm_fil.get("id"),
            "lot_nr": uid,
            "spool_weight": 250,  # Standard Spulengewicht
            "price": 0,
            "used_weight": 0,
            "archived": False,
        })
        logger.info(f"Spule erstellt in Spoolman: {sm_spool.get('id')} (lot_nr={uid})")
        return sm_spool
        
    except Exception as e:
        logger.error(f"Fehler beim Erstellen der Spule in Spoolman (lot_nr={uid}): {e}")
        return None


async def calculate_and_sync_usage(
    smc: SpoolmanClient,
    filament_data: Dict[str, Any],
    sm_spool: Dict[str, Any]
) -> float:
    """
    Berechnet den Verbrauch aus SimplyPrint-Längen und synchronisiert zu Spoolman.
    Gibt das berechnete used_weight zurück.
    """
    total = filament_data.get("total_length_mm")
    left = filament_data.get("left_length_mm")
    
    if total is None or left is None:
        logger.debug(f"Keine Längenangaben für lot_nr={filament_data['uid']}")
        return float(sm_spool.get("used_weight") or 0)
    
    try:
        length_used_mm = max(0.0, float(total) - float(left))
    except (ValueError, TypeError):
        logger.warning(f"Ungültige Längenangaben für lot_nr={filament_data['uid']}")
        return float(sm_spool.get("used_weight") or 0)
    
    # Gramm pro Meter berechnen
    gpm = grams_per_meter(
        filament_data["density_g_cm3"],
        filament_data["diameter_mm"]
    ) or 2.98  # Fallback für PLA 1.75mm
    
    used_g = round((length_used_mm / 1000.0) * gpm, 2)
    cur_used = float(sm_spool.get("used_weight") or 0)
    
    # Prüfen ob Update nötig ist
    if abs(used_g - cur_used) <= EPS():
        logger.debug(f"Kein Update nötig für lot_nr={filament_data['uid']} (Δ={abs(used_g - cur_used):.2f}g)")
        return used_g
    
    if S.get("DRY_RUN", "false") == "true":
        logger.info(f"[DRY-RUN] Würde used_weight aktualisieren: {cur_used}g → {used_g}g (Δ={abs(used_g - cur_used):.2f}g)")
        return used_g
    
    try:
        # Filament-ID defensiv extrahieren
        sm_filament_id = (
            sm_spool.get("filament", {}).get("id")
            if isinstance(sm_spool.get("filament"), dict)
            else sm_spool.get("filament_id")
        )
        
        await smc.update_spool(sm_spool.get("id"), {
            "filament_id": sm_filament_id,
            "price": sm_spool.get("price"),
            "spool_weight": sm_spool.get("spool_weight"),
            "archived": sm_spool.get("archived", False),
            "lot_nr": sm_spool.get("lot_nr"),
            "used_weight": used_g,
        })
        logger.info(f"Verbrauch aktualisiert für lot_nr={filament_data['uid']}: {cur_used}g → {used_g}g")
        
    except Exception as e:
        logger.error(f"Fehler beim Update von used_weight für lot_nr={filament_data['uid']}: {e}")
    
    return used_g


async def sync_single_filament(
    smc: SpoolmanClient,
    sp_filament: Dict[str, Any],
    lot_map: Dict[str, Any],
    session
) -> bool:
    """
    Synchronisiert ein einzelnes Filament.
    Gibt True zurück bei Erfolg, False bei Fehler.
    """
    try:
        # Daten extrahieren und validieren
        filament_data = extract_filament_data(sp_filament)
        
        if not filament_data["uid"]:
            logger.warning(f"Filament ohne UID übersprungen: {sp_filament}")
            return False
        
        # Filament in lokaler DB speichern/aktualisieren
        filament_id = upsert_filament(session, {
            "name": filament_data["name"],
            "brand": filament_data["brand"],
            "material": filament_data["material"],
            "diameter_mm": filament_data["diameter_mm"],
            "density_g_cm3": filament_data["density_g_cm3"],
            "color_hex": filament_data["color_hex"],
            "nominal_weight_g": filament_data["nominal_weight_g"],
        })
        
        # Spoolman-Spule sicherstellen
        sm_spool = await ensure_spoolman_spool(smc, filament_data["uid"], filament_data, lot_map)
        
        # Verbrauch berechnen und synchronisieren
        used_g = 0.0
        if sm_spool:
            used_g = await calculate_and_sync_usage(smc, filament_data, sm_spool)
        
        # Lokale DB aktualisieren
        upsert_spool(session, {
            "filament_id": filament_id,
            "lot_nr": filament_data["uid"],
            "spool_weight_g": sm_spool.get("spool_weight") if sm_spool else None,
            "price_eur": sm_spool.get("price") if sm_spool else None,
            "used_weight_g": used_g,
            "archived": sm_spool.get("archived", False) if sm_spool else 0,
            "source": "simplyprint",
        })
        
        return True
        
    except Exception as e:
        logger.error(f"Fehler beim Synchronisieren von Filament: {e}", exc_info=True)
        return False


async def run_sync_once():
    """Führt einen vollständigen Synchronisierungslauf durch."""
    logger.info("=== Sync gestartet ===")
    
    spc = SimplyPrintClient()
    smc = SpoolmanClient()
    
    # 1) Daten von beiden APIs laden
    try:
        sp_resp = await spc.list_filaments()
        sm_spools = await smc.list_spools()
    except Exception as e:
        logger.error(f"Fehler beim Laden der Daten: {e}", exc_info=True)
        return
    
    # SimplyPrint Response normalisieren
    # API gibt {"status": true, "filament": {id: {...}, id: {...}}} zurück
    sp_filaments_dict: Dict[str, Any] = {}
    
    if isinstance(sp_resp, dict):
        if "filament" in sp_resp and isinstance(sp_resp["filament"], dict):
            sp_filaments_dict = sp_resp["filament"]
        else:
            logger.error(f"Unerwartetes SimplyPrint Response-Format: {list(sp_resp.keys())}")
            return
    else:
        logger.error(f"SimplyPrint Response ist kein Dictionary: {type(sp_resp)}")
        return
    
    # Dictionary zu Liste umwandeln
    sp_filaments = list(sp_filaments_dict.values())
    logger.info(f"SimplyPrint: {len(sp_filaments)} Filamente gefunden")
    
    # Spoolman lot_nr Map erstellen
    lot_map: Dict[str, Dict[str, Any]] = {}
    if isinstance(sm_spools, list):
        lot_map = {
            s.get("lot_nr"): s
            for s in sm_spools
            if isinstance(s, dict) and s.get("lot_nr")
        }
    
    logger.info(f"Spoolman: {len(lot_map)} Spulen mit lot_nr gefunden")
    
    # 2) Alle Filamente synchronisieren
    success_count = 0
    error_count = 0
    
    with get_session() as session:
        for sp_filament in sp_filaments:
            if not isinstance(sp_filament, dict):
                logger.warning(f"Überspringe ungültigen Eintrag: {type(sp_filament)}")
                continue
            
            if await sync_single_filament(smc, sp_filament, lot_map, session):
                success_count += 1
            else:
                error_count += 1
    
    logger.info(f"=== Sync abgeschlossen: {success_count} erfolgreich, {error_count} Fehler ===")


def start_scheduler():
    """Startet den Scheduler für automatische Syncs."""
    global _scheduler
    if _scheduler:
        logger.warning("Scheduler läuft bereits")
        return
    
    _scheduler = AsyncIOScheduler()
    interval = int(S.get("SYNC_INTERVAL_SECONDS", "300"))
    _scheduler.add_job(run_sync_once, "interval", seconds=interval)
    _scheduler.start()
    logger.info(f"Scheduler gestartet (Intervall: {interval}s)")


def reconfigure_scheduler():
    """Konfiguriert den Scheduler mit aktuellen Einstellungen neu."""
    if not _scheduler:
        logger.warning("Scheduler nicht aktiv")
        return
    
    # Alte Jobs entfernen
    for job in list(_scheduler.get_jobs()):
        _scheduler.remove_job(job.id)
    
    # Neuen Job mit aktuellem Intervall hinzufügen
    interval = int(S.get("SYNC_INTERVAL_SECONDS", "300"))
    _scheduler.add_job(run_sync_once, "interval", seconds=interval)
    logger.info(f"Scheduler neu konfiguriert (Intervall: {interval}s)")