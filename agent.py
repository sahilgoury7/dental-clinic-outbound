"""
agent.py - Dental Clinic AI Voice Agent (LiveKit Worker)
========================================================
Main file - robot yahan se chalta hai.

Registers 'dental-clinic-outbound' agent with LiveKit Cloud.
When a call is dispatched, it:
  1. Reads lead metadata from ctx.job.metadata (JSON)
  2. Builds a prompt via build_prompt(lead)
  3. Starts an AgentSession with STT/LLM/TTS/VAD pipeline
  4. Speaks the first message
  5. Handles tool calls (check_availability, book_appointment, end_call)
  6. Updates Google Sheets with call results
"""

import asyncio
import json
import logging
import os
import time
from typing import Annotated

from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    AgentServer,
    JobContext,
    RunContext,
    function_tool,
)
from livekit.plugins import deepgram, groq, sarvam, silero, openai

from prompt import build_prompt
from src.audio.clean_tts import CleanTTS
from src.tools.llm_context import DentalAssistant
from src.tools.sheets import update_call_result, update_status, find_lead_by_phone, insert_new_lead, update_patient_info

load_dotenv()

logger = logging.getLogger(__name__)




# ============================================
# LiveKit Agent Server
# ============================================
server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    """
    Entry point when a call is dispatched to this agent.
    Reads lead metadata, builds the prompt, and starts the voice session.
    """
    # Parse lead metadata from the job or room metadata
    lead = {}
    logger.info(f"Full job details: {ctx.job}")
    logger.info(f"Room details: {ctx.room}")
    
    metadata_str = ctx.job.metadata or ctx.room.metadata
    is_inbound = False
    
    if metadata_str:
        try:
            lead = json.loads(metadata_str)
            logger.info(f"Lead metadata loaded: {lead}")
        except json.JSONDecodeError:
            logger.warning("Failed to parse job or room metadata as JSON.")
    else:
        # CHECK FOR INBOUND SIP CALL (Only if no dispatch metadata exists)
        sip_phone_number = None
        for _, participant in ctx.room.remote_participants.items():
            if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
                is_inbound = True
                sip_phone_number = participant.attributes.get("sip.phoneNumber")
                logger.info(f"Detected inbound SIP call from {sip_phone_number}")
                break

        if is_inbound and sip_phone_number:
            logger.info(f"Looking up lead for inbound number: {sip_phone_number}")
            found_lead = await asyncio.to_thread(find_lead_by_phone, sip_phone_number)
            
            if found_lead:
                logger.info(f"Found existing lead: {found_lead}")
                lead = found_lead
            else:
                logger.info("New patient calling. Treating as new lead.")
                lead = {
                    "phone_number": sip_phone_number,
                    "patient_name": "Unknown",
                    "call_reason": "inbound_new",
                    "status": "inbound_active"
                }
                new_row_idx = await asyncio.to_thread(insert_new_lead, sip_phone_number)
                if new_row_idx != -1:
                    lead["row_index"] = new_row_idx
                    lead["id"] = f"L{new_row_idx:03d}"
        else:
            # Sandbox test: Treat as inbound new patient if no metadata is found
            logger.info("No metadata and no SIP detected. Assuming Web Sandbox Inbound Test.")
            is_inbound = True
            dummy_phone = "sandbox_test"
            lead = {
                "phone_number": dummy_phone,
                "patient_name": "Unknown",
                "call_reason": "inbound_new",
                "status": "inbound_active"
            }
            # Look up or insert dummy tester in sheets so end-to-end works
            new_row_idx = await asyncio.to_thread(insert_new_lead, dummy_phone, "Unknown")
            if new_row_idx != -1:
                lead["row_index"] = new_row_idx
                lead["id"] = f"L{new_row_idx:03d}"

    # Build the system prompt and first message from lead data
    system_instructions, first_message = build_prompt(lead)

    # Configure the voice pipeline
    session = AgentSession(
        vad=silero.VAD.load(
            activation_threshold=0.5,
            min_speech_duration=0.15,
            min_silence_duration=0.7,      # 700ms is the sweet spot between latency and cutting off
        ),
        stt=deepgram.STT(model="nova-3", language="en-US"),   # Speech-to-Text
        llm=openai.LLM(
            model="llama-3.1-8b-instant",
            base_url="https://api.groq.com/openai/v1",
            api_key=os.environ.get("GROQ_API_KEY"),
        ),        # Using standard LLM for fast streaming
        tts=CleanTTS(model="bulbul:v3", speaker="ishita", pace=1.35),  # Text-to-Speech (Hindi/Hinglish)
        preemptive_generation=False,       # Disable preemptive queries to prevent hitting Gemini API 429/503 limits
    )

    # Create the dental assistant agent with the built prompt
    agent = DentalAssistant(
        system_instructions=system_instructions,
        lead=lead,
    )
    
    # Set up call state tracking
    agent.call_start_time = time.time()
    agent.booked_date = ""
    agent.booked_time = ""
    agent.call_status = "active"

    # Mark as 'calling' or 'inbound_active' in Google Sheets
    row_index = lead.get("row_index")
    if row_index:
        try:
            status_to_update = "inbound_active" if is_inbound else "calling"
            await asyncio.wait_for(
                asyncio.to_thread(update_status, row_index, status_to_update),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.error(f"Failed to update status to '{status_to_update}' (timeout)")
        except Exception as e:
            logger.error(f"Failed to update status to '{status_to_update}': {e}")

    @session.on("user_speech_committed")
    def on_user_speech_committed(msg):
        text = getattr(msg, 'text', msg)
        logger.info(f"[STT/VAD] User spoke: {text}")
        agent.transcript.append(f"User: {text}")

    @session.on("agent_speech_committed")
    def on_agent_speech_committed(msg):
        text = getattr(msg, 'text', msg)
        logger.info(f"[LLM] Agent generated response: {text}")
        agent.transcript.append(f"Agent: {text}")

    @session.on("agent_speech_started")
    def on_agent_speech_started():
        logger.info("[State] Agent started speaking")

    @session.on("agent_speech_stopped")
    def on_agent_speech_stopped():
        logger.info("[State] Agent stopped speaking, transitioning to listening state")
        # Context Memory Trimmer to prevent massive token leaks
        try:
            MAX_MESSAGES = 10
            if hasattr(session, 'chat_ctx') and session.chat_ctx and hasattr(session.chat_ctx, 'messages'):
                msgs = session.chat_ctx.messages
                if len(msgs) > MAX_MESSAGES:
                    # Keep the system prompt (first msg) and the N most recent messages
                    session.chat_ctx.messages = [msgs[0]] + msgs[-(MAX_MESSAGES - 1):]
                    logger.info(f"Trimmed chat_ctx from {len(msgs)} to {len(session.chat_ctx.messages)} messages to prevent memory leak")
        except Exception as e:
            logger.error(f"Error trimming chat_ctx: {e}")

    # Start the session and speak the first message
    await session.start(
        agent=agent,
        room=ctx.room,
    )

    # Speak the first message (greeting)
    await session.say(first_message)
    agent.transcript.append(f"Agent: {first_message}")

    # Register shutdown callback to summarize and write to Sheets when call ends
    async def on_shutdown():
        logger.info("Session shutting down, generating summary...")
        duration_seconds = time.time() - agent.call_start_time
        
        transcript_str = "\n".join(agent.transcript)
        summary = "No summary generated."
        
        if agent.transcript:
            try:
                import os
                from openai import OpenAI
                
                # Fetch summary in a separate thread so it doesn't block the event loop
                def fetch_summary():
                    client = OpenAI(
                        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                        api_key=os.environ.get("GEMINI_API_KEY")
                    )
                    response = client.chat.completions.create(
                        model="gemini-2.5-flash",
                        messages=[
                            {"role": "system", "content": "You are a helpful assistant. Summarize the following phone call transcript in 1-2 short sentences. Do not use any markdown formatting or bullet points."},
                            {"role": "user", "content": f"Transcript:\n{transcript_str}"}
                        ]
                    )
                    return response.choices[0].message.content.strip()

                summary = await asyncio.wait_for(
                    asyncio.to_thread(fetch_summary),
                    timeout=10.0
                )
                logger.info(f"Generated summary: {summary}")
            except asyncio.TimeoutError:
                logger.error("Generating summary timed out after 10 seconds")
            except Exception as e:
                logger.error(f"Failed to generate summary: {e}")
        
        # Write to Google Sheets
        row_index = lead.get("row_index")
        if row_index:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        update_call_result,
                        row_index=row_index,
                        status=agent.call_status,
                        duration_seconds=duration_seconds,
                        meeting_date=agent.booked_date,
                        meeting_time=agent.booked_time,
                        notes=summary,
                        tts_provider="sarvam",
                        call_reason=agent.lead.get("call_reason", ""),
                    ),
                    timeout=15.0
                )
                logger.info(f"Google Sheets row {row_index} updated with summary and status.")
            except asyncio.TimeoutError:
                logger.error("Google Sheets update in on_shutdown timed out after 15 seconds")
            except Exception as e:
                logger.error(f"Failed to update Google Sheets: {e}")
        else:
            logger.info("No row_index in lead metadata (test call). Skipping Google Sheets update.")
            logger.info(f"Call summary: {summary}")
            logger.info(f"Call duration: {duration_seconds:.0f}s | Status: {agent.call_status}")

    ctx.add_shutdown_callback(on_shutdown)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agents.cli.run_app(server)
