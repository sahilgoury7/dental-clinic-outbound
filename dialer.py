"""
dialer.py - Outbound Call Orchestrator
=======================================
Reads leads with status='pending' from Google Sheets,
creates a LiveKit room per lead, dispatches the agent,
then initiates the SIP outbound call.

Max concurrent calls controlled by MAX_CONCURRENT.

Usage:
    uv run python dialer.py
"""

import asyncio
import json
import logging
import os

from dotenv import load_dotenv
from livekit import api

from src.tools.sheets import get_pending_leads, update_status
from config import CLINIC

load_dotenv()

logger = logging.getLogger(__name__)

# Maximum number of concurrent outbound calls
MAX_CONCURRENT = 3

# Agent name registered in agent.py
AGENT_NAME = "dental-clinic-outbound"


async def dial_lead(lkapi: api.LiveKitAPI, lead: dict, semaphore: asyncio.Semaphore):
    """
    Dial a single lead:
    1. Create a LiveKit room
    2. Dispatch the agent to the room
    3. Create a SIP participant (initiates the phone call)

    Args:
        lkapi: LiveKit API client.
        lead: Lead dictionary from Google Sheets.
        semaphore: Concurrency limiter.
    """
    async with semaphore:
        phone = lead["phone_number"]
        name = lead.get("patient_name", "Patient")
        room_name = f"dental-call-{lead.get('id', phone[-4:])}-{int(asyncio.get_event_loop().time())}"

        logger.info(f"Dialing {name} at {phone} | Room: {room_name}")

        try:
            # Step 1: Create a LiveKit room
            await lkapi.room.create_room(
                api.CreateRoomRequest(name=room_name)
            )
            logger.info(f"Room created: {room_name}")

            # Step 2: Dispatch the agent to the room with lead metadata
            metadata_json = json.dumps(lead)
            await lkapi.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=AGENT_NAME,
                    room=room_name,
                    metadata=metadata_json,
                )
            )
            logger.info(f"Agent dispatched to room: {room_name}")

            # Step 3: Wait briefly for agent to connect
            await asyncio.sleep(2)

            # Step 4: Initiate the SIP outbound call
            sip_trunk_id = os.environ.get("SIP_TRUNK_ID", "")
            if not sip_trunk_id:
                logger.error("SIP_TRUNK_ID not set! Cannot make outbound call.")
                await asyncio.to_thread(update_status, lead["row_index"], "error")
                return

            await lkapi.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    sip_trunk_id=sip_trunk_id,
                    sip_call_to=phone,
                    room_name=room_name,
                    participant_identity=f"patient-{phone[-4:]}",
                    participant_name=name,
                )
            )
            logger.info(f"SIP call initiated to {phone}")

            # Wait for the call to complete (agent will handle the rest)
            # The agent's end_call tool will update the sheet status
            # We just need to wait a reasonable time before allowing next call
            await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Error dialing {phone}: {e}")
            try:
                await asyncio.to_thread(update_status, lead["row_index"], "error")
            except Exception as sheet_err:
                logger.error(f"Failed to update sheet status: {sheet_err}")


async def main():
    """
    Main orchestrator loop:
    1. Fetch pending leads from Google Sheets
    2. Dial each lead with concurrency control
    """
    logger.info(f"=== {CLINIC} Outbound Dialer Started ===")

    # Initialize LiveKit API client
    lkapi = api.LiveKitAPI()

    # Fetch pending leads
    leads = await asyncio.to_thread(get_pending_leads)

    if not leads:
        logger.info("No pending leads found. Exiting.")
        return

    logger.info(f"Found {len(leads)} pending leads. Starting calls (max {MAX_CONCURRENT} concurrent)...")

    # Create semaphore for concurrency control
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    # Dial all leads concurrently (limited by semaphore)
    tasks = [dial_lead(lkapi, lead, semaphore) for lead in leads]
    await asyncio.gather(*tasks)

    logger.info("=== All calls completed ===")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
