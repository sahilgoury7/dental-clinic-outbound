import asyncio
import os
from dotenv import load_dotenv
from livekit import api

load_dotenv()

async def main():
    lkapi = api.LiveKitAPI()
    sip_trunk_id = os.environ.get("SIP_TRUNK_ID", "")
    phone = "+919660280889"
    room_name = "test-call-manual"
    
    await lkapi.room.create_room(api.CreateRoomRequest(name=room_name))
    print(f"Room {room_name} created.")
    
    print(f"Dialing {phone}...")
    await lkapi.sip.create_sip_participant(
        api.CreateSIPParticipantRequest(
            sip_trunk_id=sip_trunk_id,
            sip_call_to=phone,
            room_name=room_name,
            participant_identity="test-caller",
            participant_name="Test Caller",
        )
    )
    print("Call initiated!")

asyncio.run(main())
