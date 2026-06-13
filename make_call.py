"""
make_call.py - Outbound Call Launcher
======================================
Makes a single outbound call, either from CLI args or from the first
pending lead in Google Sheets.

Usage:
    uv run python make_call.py +919876543210
    uv run python make_call.py +919876543210 --name "Rahul" --reason "checkup"
    uv run python make_call.py --from-sheet
"""

import argparse
import asyncio
import json
import logging
import os

from dotenv import load_dotenv
from livekit import api

load_dotenv()

logger = logging.getLogger(__name__)

AGENT_NAME = "dental-clinic-outbound"


async def make_call(lead: dict):
    """
    Make an outbound call using the given lead metadata.

    Args:
        lead: Dictionary with at least 'phone_number' and 'patient_name'.
              If 'row_index' is present, post-call data will be saved to Google Sheets.
    """
    phone = lead["phone_number"]
    if not phone.startswith("+"):
        phone = "+" + phone
    name = lead.get("patient_name", "Patient")

    logger.info(f"=== Making call to {name} at {phone} ===")
    if lead.get("row_index"):
        logger.info(f"    Google Sheet row: {lead['row_index']} (post-call data WILL be saved)")
    else:
        logger.info("    No row_index — post-call data will NOT be saved to Google Sheets")

    lkapi = api.LiveKitAPI()
    room_name = f"call-{phone[-4:]}"

    try:
        # Step 1: Create room (attach metadata so the worker can read it from ctx.room.metadata)
        await lkapi.room.create_room(
            api.CreateRoomRequest(
                name=room_name,
                metadata=json.dumps(lead)
            )
        )
        logger.info(f"Room created: {room_name}")

        # Step 2: Dispatch agent with full lead metadata
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=json.dumps(lead),
            )
        )
        logger.info("Agent dispatched.")

        # Step 3: Wait for agent to connect
        await asyncio.sleep(2)

        # Step 4: Make the SIP call
        sip_trunk_id = os.environ.get("SIP_TRUNK_ID", "")
        if not sip_trunk_id:
            logger.error("SIP_TRUNK_ID not set! Cannot make call.")
            return

        participant_id = f"patient-{phone[-4:]}"
        await lkapi.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=sip_trunk_id,
                sip_call_to=phone,
                room_name=room_name,
                participant_identity=participant_id,
                participant_name=name,
            )
        )
        logger.info(f"SIP call initiated to {phone}. Waiting for call to complete...")

        # Wait for SIP participant to actually join before starting to poll
        logger.info("Waiting for SIP participant to join the room...")
        await asyncio.sleep(15)

        # Poll the room until the SIP participant leaves, with a 5-minute max timeout
        max_wait = 300  # 5 minutes
        elapsed = 0
        poll_interval = 5
        logger.info("Monitoring room for call completion (max 5 minutes)...")

        while elapsed < max_wait:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            try:
                participants = await lkapi.room.list_participants(
                    api.ListParticipantsRequest(room=room_name)
                )
                sip_still_connected = any(
                    p.identity == participant_id for p in participants
                )
                if not sip_still_connected:
                    logger.info("SIP participant has left the room. Call ended.")
                    # Give agent 25 seconds to run shutdown callback
                    # (generate summary + update Google Sheets)
                    logger.info("Waiting 25s for agent to complete post-call cleanup...")
                    await asyncio.sleep(25)
                    break
            except Exception:
                # Room may have been deleted already
                logger.info("Room no longer accessible. Call ended.")
                # Still wait for agent shutdown
                logger.info("Waiting 25s for agent cleanup...")
                await asyncio.sleep(25)
                break

        if elapsed >= max_wait:
            logger.warning("Max wait time (5 minutes) reached. Exiting.")

        logger.info("Call complete. Exiting.")

    except KeyboardInterrupt:
        logger.info("Call ended by user.")
    except Exception as e:
        logger.error(f"Error making call: {e}")
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Make an outbound call.")
    parser.add_argument("phone", nargs="?", default=None, help="Phone number (e.g., +919876543210)")
    parser.add_argument("--name", default="Patient", help="Patient name")
    parser.add_argument("--reason", default="appointment", help="Call reason")
    parser.add_argument("--from-sheet", action="store_true", help="Fetch the first pending lead from Google Sheets")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    if args.from_sheet:
        # Fetch lead from Google Sheets (includes row_index for post-call saving)
        from tools.sheets import get_pending_leads
        leads = get_pending_leads()
        if not leads:
            logger.error("No pending leads found in Google Sheets!")
            exit(1)
        lead = leads[0]
        logger.info(f"Fetched lead from Google Sheets: {lead}")
        asyncio.run(make_call(lead))
    elif args.phone:
        # Manual call (no row_index, no Sheets saving)
        lead = {
            "phone_number": args.phone,
            "patient_name": args.name,
            "call_reason": args.reason,
            "last_visit": "Not available",
        }
        asyncio.run(make_call(lead))
    else:
        parser.error("Please provide a phone number or use --from-sheet")
