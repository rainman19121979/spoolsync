import math
import logging
import time
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .db import get_session, upsert_filament, upsert_spool
from .clients import SpoolmanClient, SimplyPrintClient
from . import settings as S

# Logging konfigurieren
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
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


def normalize_timestamp(timestamp: Any) -> Optional[str]:
    """
    Normalisiert Timestamps zu ISO 8601 Format für Spoolman.

    Args:
        timestamp: Unix timestamp (int/float) oder ISO string

    Returns:
        ISO 8601 string oder None
    """
    if not timestamp:
        return None

    try:
        # Falls bereits ein String (ISO format)
        if isinstance(timestamp, str):
            # Validierung durch Parsing
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            return dt.isoformat()

        # Falls Unix timestamp (Sekunden)
        if isinstance(timestamp, (int, float)):
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            return dt.isoformat()

    except Exception as e:
        logger.warning(f"Ungültiger Timestamp: {timestamp} - {e}")

    return None


def find_matching_filament(
    sm_filaments: List[Dict[str, Any]],
    filament_data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Sucht nach einem passenden Filament in Spoolman basierend auf Material, Durchmesser, Marke UND Farbe.
    """
    for sm_fil in sm_filaments:
        # Vergleiche Material, Durchmesser und Marke
        material_match = (
            sm_fil.get("material", "").lower() == filament_data.get("material", "").lower()
        )
        diameter_match = (
            abs(float(sm_fil.get("diameter", 0)) - float(filament_data.get("diameter_mm", 0))) < 0.01
        )

        # Vendor kann ein Dict oder String sein
        sm_vendor = sm_fil.get("vendor")
        if isinstance(sm_vendor, dict):
            sm_vendor_name = sm_vendor.get("name", "")
        else:
            sm_vendor_name = str(sm_vendor) if sm_vendor else ""

        vendor_match = (
            sm_vendor_name.lower() == filament_data.get("brand", "").lower()
        )

        # Farbe vergleichen (wichtig für verschiedene Farben desselben Materials!)
        color_match = True  # Default: Farbe nicht prüfen wenn nicht vorhanden
        if filament_data.get("color_hex") and sm_fil.get("color_hex"):
            # Beide haben Farbe → vergleichen
            color_match = (
                sm_fil.get("color_hex", "").lower() == filament_data.get("color_hex", "").lower()
            )
        elif filament_data.get("color_hex") or sm_fil.get("color_hex"):
            # Nur einer hat Farbe → kein Match
            color_match = False

        if material_match and diameter_match and vendor_match and color_match:
            return sm_fil

    return None


def calculate_weight_from_length(
    length_mm: float,
    density_g_cm3: float,
    diameter_mm: float
) -> float:
    """
    Berechnet Gewicht in Gramm aus Länge in Millimetern.
    """
    gpm = grams_per_meter(density_g_cm3, diameter_mm) or 2.98
    return round((length_mm / 1000.0) * gpm, 2)


async def update_simplyprint_usage(spc, uid: str, remaining_length_mm: float, sp_filament: Dict[str, Any]):
    """
    Aktualisiert die verbleibende Länge in SimplyPrint basierend auf Waagen-Messung.

    Args:
        spc: SimplyPrintClient
        uid: 4-Zeichen Filament-Code
        remaining_length_mm: Verbleibende Länge in Millimetern
        sp_filament: Das komplette SimplyPrint Filament-Dict für alle benötigten Felder
    """
    try:
        # SimplyPrint Create/Update Endpoint benötigt die numerische ID, nicht die UID
        filament_id = sp_filament.get("id")
        if not filament_id:
            raise ValueError(f"Filament ID fehlt für UID {uid}")

        # SimplyPrint Create/Update Endpoint benötigt ALLE Felder, nicht nur "left"
        # Wir müssen das existierende Filament mit dem neuen "left" Wert überschreiben

        # Berechne Prozent verbleibend (laut Discord: length_used ist semantisch vertauscht)
        total_length = int(sp_filament.get("total", 0))
        remaining_length = max(0, int(remaining_length_mm))

        # length_used als Prozent verbleibend (nicht verbraucht!)
        if total_length > 0:
            length_used_percent = round((remaining_length / total_length) * 100, 2)
        else:
            length_used_percent = 0

        payload = {
            "left": remaining_length,  # Verbleibende Länge in mm
            "total_length": total_length,  # Gesamtlänge in mm
            "total_length_type": "m",  # Meter (m = meters/mm in SimplyPrint)
            "length_used": length_used_percent,  # Prozent verbleibend (vertauschte Semantik!)
            "left_length_type": "percent",  # Typ für length_used
            "color_name": sp_filament.get("colorName", ""),
            "color_hex": sp_filament.get("colorHex", "#FFFFFF"),
            "width": float(sp_filament.get("dia", 1.75)),
            "density": float(sp_filament.get("density", 1.24)),
            "brand": sp_filament.get("brand", ""),
        }

        # Material Type - SimplyPrint braucht filament_type als INTEGER (Type ID)
        sp_type = sp_filament.get("type", {})
        if isinstance(sp_type, dict) and sp_type.get("id"):
            payload["filament_type"] = int(sp_type.get("id"))  # Type ID als Integer
        else:
            # Fallback: Verwende eine Default Type ID oder None
            logger.warning(f"Keine Type ID für {uid}, Update könnte fehlschlagen")
            # Versuche trotzdem - SimplyPrint könnte einen Default haben
            payload["filament_type"] = 1  # Fallback Type ID

        # Debug: Zeige kompletten Payload
        logger.debug(f"SimplyPrint Update Payload für {uid} (ID: {filament_id}): {payload}")

        # Verwende die numerische ID für das Update
        result = await spc.update_filament(str(filament_id), payload)
        logger.info(f"SimplyPrint Filament {uid} (ID: {filament_id}) aktualisiert: left={payload['left']}mm ({length_used_percent}%)")
        logger.debug(f"SimplyPrint API Response: {result}")

        # Verifiziere, ob der Wert wirklich gespeichert wurde
        verification = await spc.list_filaments()
        if verification and "filament" in verification:
            for fil_data in verification["filament"].values():
                if fil_data.get("uid") == uid:
                    logger.info(f"Verifikation nach Update für {uid}: left={fil_data.get('left')}mm, total={fil_data.get('total')}mm, percentage={fil_data.get('percentage', 0)}%")
                    break
    except Exception as e:
        logger.error(f"Fehler beim Aktualisieren von SimplyPrint Filament {uid}: {e}")


def round_to_standard_weight(weight_g: float, brand: str = "") -> float:
    """
    Rundet Gewicht auf Standard-Spulengrößen.
    z.B. 988g → 1000g, 1088g → 1100g (nur JAYO), sonst → 1000g
    """
    # Standard-Gewichte (1100g nur für JAYO)
    if brand.upper() == "JAYO" and 1000 < weight_g < 1200:
        standard_weights = [250, 500, 1000, 1100, 2000, 5000, 10000]
    else:
        standard_weights = [250, 500, 1000, 2000, 5000, 10000]

    # Finde nächstes Standard-Gewicht
    closest = min(standard_weights, key=lambda x: abs(x - weight_g))

    # Wenn innerhalb von ±12% des Standard-Gewichts, runde darauf
    tolerance = 0.12
    if abs(weight_g - closest) / closest <= tolerance:
        return float(closest)

    return weight_g


def extract_material_type(type_field: Any) -> str:
    """
    Extrahiert den reinen Material-Typ aus dem SimplyPrint type-Feld.
    Entfernt Hersteller-Präfixe wie "JAYO PETG" -> "PETG"
    Behält Varianten wie "PLA+", "PETG-CF" etc.
    """
    if isinstance(type_field, dict):
        material = type_field.get("name", "Unknown")
    else:
        material = str(type_field) if type_field else "Unknown"

    material = material.strip()
    material_upper = material.upper()

    # Liste bekannter Material-Typen mit Varianten (längere zuerst!)
    known_materials = [
        "PLA+", "PETG-CF", "PLA-CF", "ABS+", "TPU-95A", "TPU-98A",
        "PETG", "PLA", "ABS", "TPU", "NYLON", "ASA", "PC", "PP", "PVA", "HIPS"
    ]

    # Suche nach bekanntem Material-Typ im String (längere Matches haben Priorität)
    for mat in known_materials:
        # Exakte Übereinstimmung (mit oder ohne Hersteller-Präfix)
        if material_upper == mat:
            return mat

        # Material am Ende (z.B. "JAYO PLA+")
        if material_upper.endswith(" " + mat):
            return mat

        # Material am Anfang (z.B. "PLA+ Natural")
        if material_upper.startswith(mat + " "):
            return mat

        # Material alleine in Wort-Liste
        words = material_upper.split()
        if mat in words:
            return mat

    # Fallback: Wenn kein bekanntes Material gefunden, nimm das letzte Wort
    words = material.split()
    if len(words) > 1:
        last_word = words[-1]
        # Behalte original Schreibweise für unbekannte Materialien
        if 2 <= len(last_word) <= 10:
            return last_word

    return material


def extract_filament_data(sp_filament: Dict[str, Any], sp_types: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Extrahiert und normalisiert Filament-Daten aus SimplyPrint.

    SimplyPrint API Struktur:
    - uid: 4-Zeichen Code (z.B. "PL23")
    - type: {id: int, name: str} oder einfach string (kann "JAYO PETG" enthalten)
    - dia: Durchmesser (diameter)
    - density: Dichte
    - total: Gesamtlänge in mm
    - left: Verbleibende Länge in mm
    - spoolWeight: Gewicht der leeren Spule in Gramm

    Args:
        sp_filament: Filament-Daten aus SimplyPrint
        sp_types: Optional - Filament-Types Dictionary aus GET /{id}/filament/type/Get
    """
    # Material-Typ extrahieren (nur PLA/PETG/etc., ohne Hersteller)
    # WICHTIG: Wird später aus Type API überschrieben falls verfügbar!
    material = extract_material_type(sp_filament.get("type"))

    # Brand extrahieren (priorisiere Filament-Brand)
    brand = sp_filament.get("brand", "").strip() or "Unknown"

    # Durchmesser und Dichte - erst aus Filament, dann aus Type
    diameter_mm = float(sp_filament.get("dia", 1.75))
    density_g_cm3 = float(sp_filament.get("density", 1.24))

    # Wenn sp_types verfügbar, versuche bessere Werte aus dem Type zu holen
    if sp_types:
        type_obj = sp_filament.get("type")
        type_id = None

        if isinstance(type_obj, dict):
            type_id = type_obj.get("id")
        elif isinstance(type_obj, int):
            type_id = type_obj

        if type_id and str(type_id) in sp_types:
            type_data = sp_types[str(type_id)]

            # Material-Typ aus Type API verwenden (das ist das sauberste!)
            if type_data.get("material_type_name"):
                material = type_data["material_type_name"]
                logger.debug(f"Material aus Type API: {material}")
            elif type_data.get("filament_type_name"):
                material = type_data["filament_type_name"]
                logger.debug(f"Material aus Type API (legacy): {material}")

            # Überschreibe mit Werten aus Type, falls vorhanden
            if type_data.get("density"):
                density_g_cm3 = float(type_data["density"])
            if type_data.get("width"):
                diameter_mm = float(type_data["width"])
            elif type_data.get("diameter") or type_data.get("dia"):
                diameter_mm = float(type_data.get("diameter") or type_data.get("dia", diameter_mm))

            # Brand aus Type verwenden wenn im Filament nicht gesetzt
            if brand == "Unknown" and type_data.get("brand"):
                if isinstance(type_data["brand"], dict):
                    brand = type_data["brand"].get("name", "Unknown")
                else:
                    brand = type_data["brand"]

    # Spulengewicht (leer)
    spool_weight = sp_filament.get("spoolWeight") or sp_filament.get("spool_weight")

    # Zuletzt benutzt - SimplyPrint kann verschiedene Felder haben
    last_used = None
    for field in ["lastUsed", "last_used", "used", "lastActive"]:
        if sp_filament.get(field):
            last_used = sp_filament.get(field)
            break

    # Name: profile_name aus Type API + Farbe
    profile_name = None

    # Hole profile_name aus Type API
    if sp_types:
        type_obj = sp_filament.get("type")
        type_id = None

        if isinstance(type_obj, dict):
            type_id = type_obj.get("id")
        elif isinstance(type_obj, int):
            type_id = type_obj

        if type_id and str(type_id) in sp_types:
            type_data = sp_types[str(type_id)]
            if type_data.get("profile_name"):
                profile_name = type_data["profile_name"]
                logger.debug(f"profile_name aus Type API: {profile_name}")

    # Name = profile_name + Farbe (oder Fallback auf Material + Farbe)
    color_name = sp_filament.get('colorName', '').strip()
    base_name = profile_name if profile_name else material

    if color_name:
        name = f"{base_name} {color_name}"
    else:
        name = base_name

    # Temperaturen und Kosten aus Type API extrahieren
    extruder_temp = None
    bed_temp = None
    cost = None

    if sp_types:
        type_obj = sp_filament.get("type")
        type_id = None

        if isinstance(type_obj, dict):
            type_id = type_obj.get("id")
        elif isinstance(type_obj, int):
            type_id = type_obj

        if type_id and str(type_id) in sp_types:
            type_data = sp_types[str(type_id)]

            # Temperaturen aus temps-Objekt
            if type_data.get("temps"):
                temps = type_data["temps"]
                if temps.get("nozzle"):
                    extruder_temp = int(temps["nozzle"])
                if temps.get("bed"):
                    bed_temp = int(temps["bed"])

            # Kosten (in SimplyPrint in Cent, zu Euro konvertieren)
            if type_data.get("cost"):
                cost = float(type_data["cost"]) / 100.0

    return {
        "uid": sp_filament.get("uid"),  # 4-Zeichen Code
        "name": name,  # Nur Material + Farbe (Brand ist separates Feld!)
        "brand": brand,
        "material": material,  # Nur der Material-Typ (PLA, PETG, etc.)
        "diameter_mm": diameter_mm,
        "density_g_cm3": density_g_cm3,
        "color_hex": normalize_color(sp_filament.get("colorHex")),
        "nominal_weight_g": None,  # SimplyPrint hat kein direktes Filament-Gewicht
        "total_length_mm": sp_filament.get("total"),  # Gesamtlänge in mm
        "left_length_mm": sp_filament.get("left"),   # Verbleibend in mm
        "spool_weight_g": float(spool_weight) if spool_weight else None,  # Gewicht der leeren Spule oder None
        "last_used": last_used,  # Zuletzt benutzt Timestamp
        "extruder_temp": extruder_temp,  # Düsentemperatur
        "bed_temp": bed_temp,  # Betttemperatur
        "cost": cost,  # Kosten in Euro
    }


async def ensure_vendor(
    smc: SpoolmanClient,
    brand_name: str,
    sm_vendors: Dict[str, Dict[str, Any]]
) -> Optional[int]:
    """
    Stellt sicher, dass ein Vendor in Spoolman existiert.
    Gibt die Vendor-ID zurück oder None bei Fehler.
    """
    if not brand_name or brand_name == "Unknown":
        return None

    # Normalisierte Suche (case-insensitive)
    brand_lower = brand_name.lower()

    # Suche in existierenden Vendors
    for vendor_id, vendor in sm_vendors.items():
        vendor_name = vendor.get("name", "")
        if vendor_name.lower() == brand_lower:
            return vendor.get("id")

    # Vendor existiert nicht, erstelle ihn
    if S.get("DRY_RUN", "false") == "true":
        logger.info(f"[DRY-RUN] Würde Vendor erstellen: {brand_name}")
        return None

    try:
        new_vendor = await smc.create_vendor({"name": brand_name})
        vendor_id = new_vendor.get("id")
        logger.info(f"Vendor erstellt in Spoolman: {vendor_id} - {brand_name}")
        # Zur Map hinzufügen
        sm_vendors[str(vendor_id)] = new_vendor
        return vendor_id
    except Exception as e:
        logger.error(f"Fehler beim Erstellen von Vendor '{brand_name}': {e}")
        return None


async def ensure_spoolman_spool(
    smc: SpoolmanClient,
    uid: str,
    filament_data: Dict[str, Any],
    lot_map: Dict[str, Any],
    sm_filaments: List[Dict[str, Any]],
    sm_vendors: Dict[str, Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    Stellt sicher, dass eine Spule in Spoolman existiert.
    Erstellt sie bei Bedarf, wenn nicht im Dry-Run-Modus.
    Wiederverwendet existierende Filamente, wenn Material, Durchmesser und Marke übereinstimmen.
    """
    if uid in lot_map:
        return lot_map[uid]

    if S.get("DRY_RUN", "false") == "true":
        logger.info(f"[DRY-RUN] Würde Spule mit lot_nr={uid} in Spoolman erstellen")
        return None

    try:
        # Suche nach existierendem Filament
        sm_fil = find_matching_filament(sm_filaments, filament_data)

        if sm_fil:
            logger.info(f"Bestehendes Filament gefunden: {sm_fil.get('id')} - {sm_fil.get('name')}")
        else:
            # Vendor sicherstellen
            vendor_id = None
            if filament_data.get("brand"):
                vendor_id = await ensure_vendor(smc, filament_data["brand"], sm_vendors)

            # Filament in Spoolman erstellen
            sm_fil_payload = {
                "name": filament_data["name"],
                "diameter": filament_data["diameter_mm"],
                "density": filament_data["density_g_cm3"],
            }
            if filament_data.get("material"):
                sm_fil_payload["material"] = filament_data["material"]
            if vendor_id:
                sm_fil_payload["vendor_id"] = vendor_id
            if filament_data["color_hex"]:
                sm_fil_payload["color_hex"] = filament_data["color_hex"]

            # Temperaturen aus SimplyPrint Type API
            if filament_data.get("extruder_temp"):
                sm_fil_payload["settings_extruder_temp"] = filament_data["extruder_temp"]
            if filament_data.get("bed_temp"):
                sm_fil_payload["settings_bed_temp"] = filament_data["bed_temp"]

            # Kosten aus SimplyPrint Type API
            if filament_data.get("cost"):
                sm_fil_payload["price"] = filament_data["cost"]

            # Gewicht: Berechne aus total_length wenn vorhanden und runde auf Standard-Gewicht
            if filament_data.get("total_length_mm"):
                weight = calculate_weight_from_length(
                    filament_data["total_length_mm"],
                    filament_data["density_g_cm3"],
                    filament_data["diameter_mm"]
                )
                sm_fil_payload["weight"] = round_to_standard_weight(weight, filament_data.get("brand", ""))

            sm_fil = await smc.create_filament(sm_fil_payload)
            logger.info(f"Filament erstellt in Spoolman: {sm_fil.get('id')} - {filament_data['name']}")
            # Zur Liste hinzufügen für zukünftige Matches
            sm_filaments.append(sm_fil)

        # Gesamtgewicht aus SimplyPrint-Länge berechnen und auf Standard-Gewicht runden
        total_weight = None
        if filament_data.get("total_length_mm"):
            calculated_weight = calculate_weight_from_length(
                filament_data["total_length_mm"],
                filament_data["density_g_cm3"],
                filament_data["diameter_mm"]
            )
            total_weight = round_to_standard_weight(calculated_weight, filament_data.get("brand", ""))

        # Spule in Spoolman erstellen
        spool_payload = {
            "filament_id": sm_fil.get("id"),
            "lot_nr": uid,
            "initial_weight": total_weight,  # Gesamtgewicht des Filaments (gerundet)
            "price": 0,
            "used_weight": 0,
            "archived": False,
        }

        # spool_weight nur setzen wenn vorhanden
        if filament_data.get("spool_weight_g"):
            spool_payload["spool_weight"] = filament_data["spool_weight_g"]

        # last_used setzen wenn vorhanden
        if filament_data.get("last_used"):
            last_used_iso = normalize_timestamp(filament_data["last_used"])
            if last_used_iso:
                spool_payload["last_used"] = last_used_iso

        sm_spool = await smc.create_spool(spool_payload)
        spool_weight_log = filament_data.get('spool_weight_g', 'nicht gesetzt')
        logger.info(
            f"Spule erstellt in Spoolman: {sm_spool.get('id')} (lot_nr={uid}, "
            f"spool_weight={spool_weight_log}, "
            f"initial_weight={total_weight}g)"
        )
        return sm_spool

    except Exception as e:
        logger.error(f"Fehler beim Erstellen der Spule in Spoolman (lot_nr={uid}): {e}")
        return None


async def calculate_and_sync_usage(
    smc: SpoolmanClient,
    spc,
    filament_data: Dict[str, Any],
    sm_spool: Dict[str, Any],
    last_sync_time: Optional[float] = None,
    sp_filament: Optional[Dict[str, Any]] = None
) -> float:
    """
    Berechnet den Verbrauch aus SimplyPrint-Längen und synchronisiert zu Spoolman.
    Unterstützt bidirektionale Synchronisation bei Waagen-Messungen.

    Args:
        smc: SpoolmanClient
        spc: SimplyPrintClient
        filament_data: Filament-Daten aus SimplyPrint
        sm_spool: Spulen-Daten aus Spoolman
        last_sync_time: Timestamp des letzten Syncs (Unix timestamp)

    Returns:
        Das aktuelle used_weight
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

    # Bidirektionale Synchronisation: Spoolman → SimplyPrint
    #
    # Wenn Spoolman nach dem letzten Sync aktualisiert wurde (manuelle Änderung/Waage),
    # dann ist Spoolman die "Source of Truth" und SimplyPrint wird aktualisiert.
    #
    # Ansonsten: SimplyPrint → Spoolman (normaler Verbrauch durch Druck)
    #
    sm_updated = sm_spool.get("last_used") or sm_spool.get("updated_at")
    if sm_updated and last_sync_time:
        try:
            # Parse ISO timestamp von Spoolman
            if isinstance(sm_updated, str):
                sm_timestamp = datetime.fromisoformat(sm_updated.replace('Z', '+00:00')).timestamp()
            else:
                sm_timestamp = sm_updated

            # Debug-Info
            sm_dt = datetime.fromtimestamp(sm_timestamp, tz=timezone.utc)
            last_sync_dt = datetime.fromtimestamp(last_sync_time, tz=timezone.utc)
            logger.debug(
                f"Timestamp-Check für lot_nr={filament_data['uid']}: "
                f"Spoolman={sm_dt.isoformat()} vs LastSync={last_sync_dt.isoformat()}, "
                f"Δweight={abs(used_g - cur_used):.2f}g (EPS={EPS():.2f}g)"
            )

            # Wenn Spoolman NEUER als letzter Sync UND Wert abweicht
            if sm_timestamp > last_sync_time and abs(used_g - cur_used) > EPS():
                logger.info(
                    f"Spoolman-Wert ist neuer (manuelle Änderung/Waage) für lot_nr={filament_data['uid']}: "
                    f"Spoolman={cur_used}g vs SimplyPrint={used_g}g - aktualisiere SimplyPrint"
                )

                # Berechne verbleibende Länge aus Spoolman used_weight zurück
                initial_weight = sm_spool.get("initial_weight") or round_to_standard_weight(
                    calculate_weight_from_length(
                        filament_data["total_length_mm"],
                        filament_data["density_g_cm3"],
                        filament_data["diameter_mm"]
                    ),
                    filament_data.get("brand", "")
                )
                remaining_weight = initial_weight - cur_used
                remaining_length_mm = (remaining_weight / gpm) * 1000.0 if gpm > 0 else 0

                # Aktualisiere SimplyPrint mit korrigiertem Wert
                if S.get("DRY_RUN", "false") != "true":
                    if sp_filament:
                        await update_simplyprint_usage(spc, filament_data["uid"], remaining_length_mm, sp_filament)
                        logger.info(f"SimplyPrint aktualisiert mit korrigiertem Wert: {remaining_length_mm:.0f}mm verbleibend")
                    else:
                        logger.warning(f"Kann SimplyPrint nicht aktualisieren - sp_filament fehlt für {filament_data['uid']}")
                else:
                    logger.info(f"[DRY-RUN] Würde SimplyPrint aktualisieren: {remaining_length_mm:.0f}mm verbleibend")

                # Spoolman-Wert beibehalten - beim nächsten Sync sollten beide Systeme synchron sein
                return cur_used

        except Exception as e:
            logger.warning(f"Fehler beim Timestamp-Vergleich für lot_nr={filament_data['uid']}: {e}")

    # Normal: SimplyPrint → Spoolman
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

        update_payload = {
            "filament_id": sm_filament_id,
            "price": sm_spool.get("price"),
            "spool_weight": sm_spool.get("spool_weight"),
            "archived": sm_spool.get("archived", False),
            "lot_nr": sm_spool.get("lot_nr"),
            "used_weight": used_g,
        }

        # last_used IMMER setzen (entweder von SimplyPrint oder aktueller Timestamp)
        # Das ist wichtig für die bidirektionale Synchronisation
        if filament_data.get("last_used"):
            last_used_iso = normalize_timestamp(filament_data["last_used"])
        else:
            # Wenn SimplyPrint keinen last_used hat, verwende aktuellen Timestamp
            last_used_iso = datetime.now(timezone.utc).isoformat()

        if last_used_iso:
            update_payload["last_used"] = last_used_iso
            logger.debug(f"Setze last_used für lot_nr={filament_data['uid']}: {last_used_iso}")

        await smc.update_spool(sm_spool.get("id"), update_payload)
        logger.info(f"Verbrauch aktualisiert für lot_nr={filament_data['uid']}: {cur_used}g → {used_g}g")

    except Exception as e:
        logger.error(f"Fehler beim Update von used_weight für lot_nr={filament_data['uid']}: {e}")

    return used_g


async def sync_single_filament(
    smc: SpoolmanClient,
    spc,
    sp_filament: Dict[str, Any],
    lot_map: Dict[str, Any],
    sm_filaments: List[Dict[str, Any]],
    sm_vendors: Dict[str, Dict[str, Any]],
    session,
    sp_types: Dict[str, Any] = None,
    last_sync_time: Optional[float] = None
) -> bool:
    """
    Synchronisiert ein einzelnes Filament.
    Gibt True zurück bei Erfolg, False bei Fehler.
    """
    try:
        # Daten extrahieren und validieren
        filament_data = extract_filament_data(sp_filament, sp_types)

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
        sm_spool = await ensure_spoolman_spool(smc, filament_data["uid"], filament_data, lot_map, sm_filaments, sm_vendors)

        # Verbrauch berechnen und synchronisieren
        used_g = 0.0
        if sm_spool:
            used_g = await calculate_and_sync_usage(smc, spc, filament_data, sm_spool, last_sync_time, sp_filament)
        
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
    # Timestamp vor dem Sync speichern
    sync_start_time = time.time()

    logger.info("=== Sync gestartet ===")

    spc = SimplyPrintClient()
    smc = SpoolmanClient()

    # Letzten Sync-Timestamp aus Settings laden (falls vorhanden)
    last_sync_time = float(S.get("LAST_SYNC_TIME", "0"))
    if last_sync_time > 0:
        last_sync_dt = datetime.fromtimestamp(last_sync_time, tz=timezone.utc)
        logger.info(f"Letzter Sync: {last_sync_dt.isoformat()}")

    # 1) Daten von beiden APIs laden
    try:
        sp_resp = await spc.list_filaments()
        sm_spools = await smc.list_spools()
        sm_filaments = await smc.list_filaments()
        sm_vendors_list = await smc.list_vendors()

        # Filament-Types von SimplyPrint laden für bessere Material-Daten
        sp_types_resp = await spc.get_filament_types()

        # Response kann "data" oder "types" enthalten, als Array oder Dict
        sp_types = {}
        if isinstance(sp_types_resp, dict):
            types_list = sp_types_resp.get("data") or sp_types_resp.get("types")
            if isinstance(types_list, list):
                # Array zu Dictionary konvertieren (id -> type_data)
                sp_types = {str(t.get("id")): t for t in types_list if isinstance(t, dict) and t.get("id")}
            elif isinstance(types_list, dict):
                sp_types = types_list

        logger.info(f"SimplyPrint: {len(sp_types)} Filament-Types geladen")

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

    # Debug: Liste alle UIDs auf
    sp_uids_found = [f.get("uid") for f in sp_filaments if isinstance(f, dict)]
    logger.debug(f"SimplyPrint UIDs: {sp_uids_found}")

    # Spoolman lot_nr Map erstellen
    lot_map: Dict[str, Dict[str, Any]] = {}
    if isinstance(sm_spools, list):
        lot_map = {
            s.get("lot_nr"): s
            for s in sm_spools
            if isinstance(s, dict) and s.get("lot_nr")
        }

    logger.info(f"Spoolman: {len(lot_map)} Spulen mit lot_nr gefunden")

    # Spoolman Filamente zur Liste machen
    if not isinstance(sm_filaments, list):
        sm_filaments = []

    logger.info(f"Spoolman: {len(sm_filaments)} Filamente gefunden")

    # Spoolman Vendors zu Dictionary machen (id -> vendor)
    sm_vendors: Dict[str, Dict[str, Any]] = {}
    if isinstance(sm_vendors_list, list):
        sm_vendors = {str(v.get("id")): v for v in sm_vendors_list if isinstance(v, dict)}

    logger.info(f"Spoolman: {len(sm_vendors)} Vendors gefunden")

    # 2) Alle Filamente synchronisieren
    success_count = 0
    error_count = 0

    # UIDs die in SimplyPrint vorhanden sind
    sp_uids = {sp_fil.get("uid") for sp_fil in sp_filaments if isinstance(sp_fil, dict) and sp_fil.get("uid")}

    with get_session() as session:
        for sp_filament in sp_filaments:
            if not isinstance(sp_filament, dict):
                logger.warning(f"Überspringe ungültigen Eintrag: {type(sp_filament)}")
                continue

            if await sync_single_filament(smc, spc, sp_filament, lot_map, sm_filaments, sm_vendors, session, sp_types, last_sync_time):
                success_count += 1
            else:
                error_count += 1

    # 3) Spulen in Spoolman verwalten, die nicht mehr in SimplyPrint existieren
    await cleanup_deleted_spools(smc, lot_map, sp_uids)

    # 4) Sync-Timestamp speichern für nächsten Lauf
    S.set("LAST_SYNC_TIME", str(sync_start_time))

    logger.info(f"=== Sync abgeschlossen: {success_count} erfolgreich, {error_count} Fehler ===")


async def cleanup_deleted_spools(
    smc: SpoolmanClient,
    lot_map: Dict[str, Any],
    sp_uids: set
):
    """
    Verwaltet Spulen in Spoolman, die nicht mehr in SimplyPrint existieren.

    - Wenn used_weight > 0: Archivieren (wurde benutzt)
    - Wenn used_weight == 0: Löschen (nie benutzt)
    """
    deleted_count = 0
    archived_count = 0

    for lot_nr, sm_spool in lot_map.items():
        # Überspringe wenn noch in SimplyPrint vorhanden
        if lot_nr in sp_uids:
            continue

        # Überspringe bereits archivierte
        if sm_spool.get("archived"):
            continue

        spool_id = sm_spool.get("id")
        used_weight = float(sm_spool.get("used_weight") or 0)

        if S.get("DRY_RUN", "false") == "true":
            action = "archivieren" if used_weight > 0 else "löschen"
            logger.info(f"[DRY-RUN] Würde Spule {spool_id} (lot_nr={lot_nr}) {action} (used_weight={used_weight}g)")
            continue

        try:
            if used_weight > 0:
                # Archivieren wenn benutzt
                # Filament-ID extrahieren
                sm_filament_id = (
                    sm_spool.get("filament", {}).get("id")
                    if isinstance(sm_spool.get("filament"), dict)
                    else sm_spool.get("filament_id")
                )

                await smc.update_spool(spool_id, {
                    "filament_id": sm_filament_id,
                    "price": sm_spool.get("price"),
                    "spool_weight": sm_spool.get("spool_weight"),
                    "archived": True,
                    "lot_nr": sm_spool.get("lot_nr"),
                    "used_weight": sm_spool.get("used_weight"),
                })
                logger.info(f"Spule archiviert: {spool_id} (lot_nr={lot_nr}, used_weight={used_weight}g)")
                archived_count += 1
            else:
                # Löschen wenn nie benutzt
                await smc.delete_spool(spool_id)
                logger.info(f"Spule gelöscht: {spool_id} (lot_nr={lot_nr}, used_weight={used_weight}g)")
                deleted_count += 1

        except Exception as e:
            logger.error(f"Fehler beim Verwalten von Spule {spool_id} (lot_nr={lot_nr}): {e}")

    if archived_count > 0 or deleted_count > 0:
        logger.info(f"Cleanup: {archived_count} archiviert, {deleted_count} gelöscht")


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