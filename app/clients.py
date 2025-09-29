import httpx
from . import settings as S

class SpoolmanClient:
    def __init__(self): self.base = S.get("SPOOLMAN_BASE").rstrip('/')
    async def list_spools(self):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{self.base}/spool"); r.raise_for_status(); return r.json()
    async def create_filament(self, payload):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.base}/filament", json=payload); r.raise_for_status(); return r.json()
    async def create_spool(self, payload):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.base}/spool", json=payload); r.raise_for_status(); return r.json()
    async def update_spool(self, spool_id, payload):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.put(f"{self.base}/spool/{spool_id}", json=payload); r.raise_for_status(); return r.json()

class SimplyPrintClient:
    def __init__(self):
        self.base = S.get("SP_BASE").rstrip('/')
        token = S.get_secret("SP_TOKEN","")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
    async def list_filaments(self):
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as c:
            r = await c.get(f"{self.base}/filaments"); r.raise_for_status(); return r.json()
    async def create_filament(self, payload):
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as c:
            r = await c.post(f"{self.base}/filaments", json=payload); r.raise_for_status(); return r.json()
    async def create_spool(self, payload):
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as c:
            r = await c.post(f"{self.base}/spools", json=payload); r.raise_for_status(); return r.json()
