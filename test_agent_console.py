"""
test_agent_console.py - Terminal-Based Agent Pipeline Simulator
==============================================================
Simulates a full conversation with the voice agent (Ishita) via text.
Integrates with the live Google Calendar and Sheets APIs using the lead information.

Usage:
    python -m uv run python test_agent_console.py
"""

import asyncio
import json
import logging
import os
import sys
import time
from dotenv import load_dotenv
from openai import OpenAI

# Add current directory to path to import modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from prompt import build_prompt
from tools.sheets import get_pending_leads, update_status, update_call_result
from tools.google_calendar import check_availability, book_appointment

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("console_simulator")


# Definitions of tools for the OpenAI/Gemini client
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Check if there are available appointment slots at the dental clinic for a given date in YYYY-MM-DD format. Use this when the patient wants to know available times.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "The date to check availability for, in YYYY-MM-DD format. Example: 2026-06-15"
                    }
                },
                "required": ["date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Book a dental appointment for the patient on Google Calendar. Use this after the patient confirms a date and time slot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "The appointment date in YYYY-MM-DD format. Example: 2026-06-15"
                    },
                    "time_slot": {
                        "type": "string",
                        "description": "The appointment time in HH:MM 24-hour format. Example: 14:00"
                    },
                    "patient_name": {
                        "type": "string",
                        "description": "The name of the patient booking the appointment."
                    },
                    "reason": {
                        "type": "string",
                        "description": "The reason for the dental visit. Example: Routine Checkup, Root Canal, Teeth Cleaning"
                    }
                },
                "required": ["date", "time_slot", "patient_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "end_call",
            "description": "End the call gracefully and record the outcome. Use this when the conversation is complete, the patient is not interested, or they want a callback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "The reason for ending the call. Must be one of: booked, not_interested, callback, complete"
                    },
                    "notes": {
                        "type": "string",
                        "description": "Brief notes about the call outcome."
                    }
                },
                "required": ["reason"]
            }
        }
    }
]


async def run_simulation():
    # 1. Fetch lead
    print("\n[Simulator] Fetching the first pending lead from Google Sheets...")
    leads = get_pending_leads()
    if not leads:
        print("[Simulator] No pending leads found. Using a mockup lead for testing.")
        lead = {
            "phone_number": "+919876543210",
            "patient_name": "Test Patient",
            "call_reason": "appointment",
            "last_visit": "Not available",
            "row_index": None
        }
    else:
        lead = leads[0]
        print(f"[Simulator] Found lead: {lead['patient_name']} ({lead['phone_number']}) at row {lead.get('row_index')}")

    # 2. Update status to 'calling' if we have a row_index
    row_index = lead.get("row_index")
    if row_index:
        print(f"[Simulator] Updating Google Sheet status to 'calling'...")
        update_status(row_index, "calling")

    # 3. Build prompts
    system_instructions, first_message = build_prompt(lead)
    
    # Set up client
    client = OpenAI(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key=os.environ.get("GEMINI_API_KEY")
    )
    
    messages = [
        {"role": "system", "content": system_instructions},
    ]

    print("\n=================== CONVERSATION START ===================")
    print(f"\nIshita: {first_message}")
    transcript = [f"Agent: {first_message}"]
    messages.append({"role": "assistant", "content": first_message})

    call_start_time = time.time()
    booked_date = ""
    booked_time = ""
    call_status = "complete"
    call_ended = False

    while not call_ended:
        try:
            # Get user text input
            user_input = input("\nYou: ").strip()
            if not user_input:
                continue

            if user_input.lower() in ["exit", "quit", "disconnect", "hangup"]:
                print("\n[Simulator] Simulating hangup...")
                break

            transcript.append(f"User: {user_input}")
            messages.append({"role": "user", "content": user_input})

            # Send to Gemini
            response = client.chat.completions.create(
                model="gemini-2.5-flash",
                messages=messages,
                tools=TOOLS,
                tool_choice="auto"
            )

            response_message = response.choices[0].message
            messages.append(response_message)

            # Check for tool calls
            if response_message.tool_calls:
                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function.name
                    arguments = json.loads(tool_call.function.arguments)

                    print(f"\n[Tool Execution] Agent is calling function: {function_name}({arguments})")
                    
                    tool_result = ""
                    if function_name == "check_availability":
                        date = arguments.get("date")
                        tool_result = await check_availability(date)
                    elif function_name == "book_appointment":
                        date = arguments.get("date")
                        time_slot = arguments.get("time_slot")
                        patient_name = arguments.get("patient_name")
                        reason = arguments.get("reason", "Dental Appointment")
                        tool_result = await book_appointment(date, time_slot, patient_name, reason)
                        
                        if "booked successfully" in tool_result.lower() or "confirmed" in tool_result.lower():
                            booked_date = date
                            booked_time = time_slot
                            call_status = "booked"
                    elif function_name == "end_call":
                        reason = arguments.get("reason")
                        notes = arguments.get("notes", "")
                        call_status = reason
                        tool_result = f"Call ended successfully with reason: {reason}"
                        call_ended = True

                    print(f"[Tool Result] {tool_result}")

                    # Send the tool result back to Gemini
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": function_name,
                        "content": tool_result
                    })

                # Call Gemini again with the tool response
                second_response = client.chat.completions.create(
                    model="gemini-2.5-flash",
                    messages=messages
                )
                assistant_response = second_response.choices[0].message.content
                print(f"\nIshita: {assistant_response}")
                transcript.append(f"Agent: {assistant_response}")
                messages.append({"role": "assistant", "content": assistant_response})
            else:
                assistant_response = response_message.content
                print(f"\nIshita: {assistant_response}")
                transcript.append(f"Agent: {assistant_response}")

        except Exception as e:
            print(f"\n[Error] {e}")
            break

    print("\n==================== CONVERSATION END ====================")
    
    # 4. Generate post-call summary using Gemini
    print("\n[Simulator] Generating post-call summary...")
    transcript_str = "\n".join(transcript)
    
    try:
        summary_response = client.chat.completions.create(
            model="gemini-2.5-flash",
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Summarize the following phone call transcript in 1-2 short sentences. Do not use any markdown formatting or bullet points."},
                {"role": "user", "content": f"Transcript:\n{transcript_str}"}
            ]
        )
        summary = summary_response.choices[0].message.content.strip()
        print(f"[Simulator] Summary: {summary}")
    except Exception as e:
        summary = "No summary generated."
        print(f"[Simulator] Failed to generate summary: {e}")

    # 5. Save results to Google Sheet
    duration_seconds = time.time() - call_start_time
    if row_index:
        print(f"[Simulator] Saving call results to Google Sheet row {row_index}...")
        try:
            update_call_result(
                row_index=row_index,
                status=call_status,
                duration_seconds=duration_seconds,
                meeting_date=booked_date,
                meeting_time=booked_time,
                notes=summary,
                tts_provider="sarvam"
            )
            print("[Simulator] [SUCCESS] Google Sheet updated successfully!")
        except Exception as e:
            print(f"[Simulator] [ERROR] Failed to update Google Sheet: {e}")
    else:
        print("\n=== Call Results ===")
        print(f"Status: {call_status}")
        print(f"Duration: {duration_seconds:.1f}s")
        print(f"Booked Date: {booked_date}")
        print(f"Booked Time: {booked_time}")
        print(f"Notes/Summary: {summary}")
        print("====================")


if __name__ == "__main__":
    asyncio.run(run_simulation())
