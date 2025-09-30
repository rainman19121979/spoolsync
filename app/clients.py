import httpx
import logging
from . import settings as S

logger = logging.getLogger(__name__)


class SpoolmanClient:
    """Client für Spoolman API."""

    def __init__(self):
        self.base = S.get("SPOOLMAN_BASE").rstrip('/')

    async def list_spools(self):
        """Listet alle Spulen auf."""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{self.base}/spool")
            r.raise_for_status()
            return r.json()

    async def list_filaments(self):
        """Listet alle Filamente auf."""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{self.base}/filament")
            r.raise_for_status()
            return r.json()

    async def list_vendors(self):
        """Listet alle Hersteller/Vendors auf."""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{self.base}/vendor")
            r.raise_for_status()
            return r.json()

    async def create_vendor(self, payload):
        """Erstellt einen neuen Hersteller/Vendor."""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.base}/vendor", json=payload)
            r.raise_for_status()
            return r.json()

    async def create_filament(self, payload):
        """Erstellt ein neues Filament."""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.base}/filament", json=payload)
            r.raise_for_status()
            return r.json()
    
    async def create_spool(self, payload):
        """Erstellt eine neue Spule."""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.base}/spool", json=payload)
            r.raise_for_status()
            return r.json()
    
    async def update_spool(self, spool_id, payload):
        """Aktualisiert eine Spule."""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.put(f"{self.base}/spool/{spool_id}", json=payload)
            r.raise_for_status()
            return r.json()

    async def delete_spool(self, spool_id):
        """Löscht eine Spule."""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.delete(f"{self.base}/spool/{spool_id}")
            r.raise_for_status()
            return True


class SimplyPrintClient:
    """
    Client für SimplyPrint API.
    
    Die SimplyPrint API benötigt:
    1. Eine Company/Organization ID in der URL: https://api.simplyprint.io/{id}/
    2. Einen API-Key im Header: X-API-KEY
    3. Die Filament-Liste kommt von: GET /{id}/filament/GetFilament
    
    Response-Format:
    {
        "status": true,
        "message": null,
        "showid": true,
        "is_kg": false,
        "brands": {...},
        "types": {...},
        "filament": {
            "3017": {
                "id": 3017,
                "uid": "PL23",  # 4-Zeichen Code
                "type": {"id": 5637, "name": "PLA"},
                "brand": "test brand",
                "colorName": "test color",
                "colorHex": "#000000",
                "dia": 1.75,
                "density": 1.24,
                "total": 335284,  # mm
                "left": 234699,   # mm
                ...
            }
        }
    }
    """
    
    def __init__(self):
        base = S.get("SP_BASE").rstrip('/')
        
        # Company ID aus der Basis-URL extrahieren oder aus Settings
        # Format: https://api.simplyprint.io/{company_id}
        company_id = S.get("SP_COMPANY_ID", "")
        
        if company_id:
            self.base = f"{base}/{company_id}"
        else:
            # Fallback: versuche ID aus der URL zu extrahieren
            self.base = base
            logger.warning(
                "SP_COMPANY_ID nicht gesetzt. Bitte in den Einstellungen die "
                "SimplyPrint Company ID eintragen!"
            )
        
        token = S.get_secret("SP_TOKEN", "")
        self.headers = {"X-API-KEY": token} if token else {}
        
        if not token:
            logger.warning("SP_TOKEN nicht gesetzt. API-Aufrufe werden fehlschlagen!")
    
    async def list_filaments(self):
        """
        Listet alle Filamente auf.

        Endpoint: GET /{id}/filament/GetFilament

        Returns:
            Dictionary mit 'filament' key, der ein Dictionary von Filamenten enthält
        """
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as c:
            url = f"{self.base}/filament/GetFilament"
            logger.debug(f"Lade Filamente von: {url}")

            r = await c.get(url)
            r.raise_for_status()
            data = r.json()

            # API Fehlerbehandlung
            if not data.get("status"):
                error_msg = data.get("message", "Unbekannter Fehler")
                raise Exception(f"SimplyPrint API Fehler: {error_msg}")

            return data

    async def get_filament_types(self):
        """
        Holt alle Filament-Typen mit Details (Material, Dichte, Hersteller, etc.).

        Endpoint: GET /{id}/filament/type/Get

        Returns:
            Dictionary mit allen Filament-Types
        """
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as c:
            url = f"{self.base}/filament/type/Get"
            logger.debug(f"Lade Filament-Types von: {url}")

            r = await c.get(url)
            r.raise_for_status()
            data = r.json()

            # API Fehlerbehandlung
            if not data.get("status"):
                error_msg = data.get("message", "Unbekannter Fehler")
                raise Exception(f"SimplyPrint API Fehler: {error_msg}")

            return data
    
    async def create_filament(self, payload):
        """
        Erstellt ein neues Filament.
        
        Endpoint: POST /{id}/filament/Create
        
        Wichtige Payload-Felder:
        - color_name: str
        - color_hex: str (z.B. "#E5E5E5")
        - width: float (1.75, 2.85, oder 3.00)
        - density: float
        - filament_type: str (z.B. "PLA")
        - brand: str
        - slicing_settings: dict mit nozzle_temp, bed_temp, etc.
        - amount: int
        - total_length_type: str ("kg" oder "m")
        - total_length: float
        """
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as c:
            url = f"{self.base}/filament/Create"
            logger.debug(f"Erstelle Filament bei: {url}")
            
            r = await c.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            
            if not data.get("status"):
                error_msg = data.get("message", "Unbekannter Fehler")
                raise Exception(f"SimplyPrint API Fehler: {error_msg}")
            
            return data
    
    async def create_spool(self, payload):
        """
        Erstellt eine neue Spule (veraltet - SimplyPrint verwaltet Spulen als Filamente).
        
        In SimplyPrint sind "Spools" eigentlich Filamente.
        Diese Methode ist ein Alias für create_filament.
        """
        return await self.create_filament(payload)
    
    async def update_filament(self, filament_id: str, payload):
        """
        Aktualisiert ein bestehendes Filament.
        
        Endpoint: POST /{id}/filament/Create?fid={filament_id}
        
        Args:
            filament_id: Die UID des Filaments (4-Zeichen Code)
            payload: Siehe create_filament für Felder
        """
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as c:
            url = f"{self.base}/filament/Create?fid={filament_id}"
            logger.debug(f"Aktualisiere Filament bei: {url}")
            
            r = await c.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            
            if not data.get("status"):
                error_msg = data.get("message", "Unbekannter Fehler")
                raise Exception(f"SimplyPrint API Fehler: {error_msg}")
            
            return data
    
    async def test_connection(self):
        """
        Testet die Verbindung zur SimplyPrint API.
        
        Endpoint: GET /{id}/account/Test
        
        Returns:
            True wenn Verbindung erfolgreich, sonst False
        """
        try:
            async with httpx.AsyncClient(timeout=10, headers=self.headers) as c:
                url = f"{self.base}/account/Test"
                logger.debug(f"Teste Verbindung zu: {url}")
                
                r = await c.get(url)
                r.raise_for_status()
                data = r.json()
                
                if data.get("status") and data.get("message") == "Your API key is valid!":
                    logger.info("SimplyPrint API Verbindung erfolgreich")
                    return True
                else:
                    logger.error(f"SimplyPrint API Test fehlgeschlagen: {data}")
                    return False
                    
        except Exception as e:
            logger.error(f"SimplyPrint API Test Fehler: {e}")
            return False