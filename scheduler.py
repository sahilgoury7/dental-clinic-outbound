import asyncio
import logging
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from livekit import api

from src.tools.sheets import _get_worksheet, COL
from dialer import dial_lead, MAX_CONCURRENT

load_dotenv()
logger = logging.getLogger(__name__)

async def run_scheduler():
    logger.info("=== Automated Local Reminder Scheduler Started ===")
    
    # Continuous polling loop
    while True:
        try:
            ws = await asyncio.to_thread(_get_worksheet)
            all_rows = await asyncio.to_thread(ws.get_all_values)
            
            leads_to_dial = []
            
            # Skip header row
            for row_idx, row in enumerate(all_rows[1:], start=2):
                while len(row) < max(COL.values()):
                    row.append("")
                
                reminder_time_str = row[COL["reminder_scheduled_for"] - 1].strip()
                reminder_status = row[COL["reminder_status"] - 1].strip().lower()
                phone = row[COL["phone_number"] - 1].strip()
                
                # If no reminder scheduled, or already processed, skip
                if not reminder_time_str or reminder_status in ["dialing", "complete", "cancelled", "rescheduled", "booked", "callback", "not_interested"]:
                    continue
                
                try:
                    reminder_dt = datetime.strptime(reminder_time_str, "%Y-%m-%d %H:%M:%S")
                    if datetime.now() >= reminder_dt:
                        # Time to dial!
                        logger.info(f"Row {row_idx} triggered for reminder: {reminder_time_str}")
                        
                        # Update sheet to lock it from being picked up again on next tick
                        # We do NOT overwrite call_reason here so the original patient type is preserved.
                        # get_pending_leads in sheets.py will dynamically override call_reason to "reminder" in memory.
                        await asyncio.to_thread(ws.update_cell, row_idx, COL["reminder_status"], "dialing")
                        await asyncio.to_thread(ws.update_cell, row_idx, COL["status"], "pending")
                        
                        lead_dict = {
                            "row_index": row_idx,
                            "id": row[COL["id"] - 1].strip(),
                            "phone_number": phone,
                            "patient_name": row[COL["patient_name"] - 1].strip() or "Unknown",
                            "call_reason": "reminder",
                            "last_visit": "Not available",
                            "meeting_date": row[COL["meeting_date"] - 1].strip(),
                            "meeting_time": row[COL["meeting_time"] - 1].strip(),
                        }
                        leads_to_dial.append(lead_dict)
                except ValueError as ve:
                    # Ignore invalid date formats silently or log trace
                    pass
            
            if leads_to_dial:
                logger.info(f"Triggering {len(leads_to_dial)} automated reminder calls...")
                lkapi = api.LiveKitAPI()
                semaphore = asyncio.Semaphore(MAX_CONCURRENT)
                tasks = [dial_lead(lkapi, lead, semaphore) for lead in leads_to_dial]
                await asyncio.gather(*tasks)
                await lkapi.aclose()
                logger.info("Automated reminder batch completed.")
                
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            
        await asyncio.sleep(60)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_scheduler())
