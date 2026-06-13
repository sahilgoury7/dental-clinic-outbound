import asyncio
from src.tools.sheets import get_pending_leads
import pprint

async def main():
    leads = await asyncio.to_thread(get_pending_leads)
    pprint.pprint(leads)

asyncio.run(main())
