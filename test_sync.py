import asyncio
from app.sync import run_sync_once
from app.db import init_db

async def main():
    print("Initialisiere DB...")
    init_db()
    print("Starte Sync...")
    await run_sync_once()
    print("Sync abgeschlossen!")

if __name__ == "__main__":
    asyncio.run(main())