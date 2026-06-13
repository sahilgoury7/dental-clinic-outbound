"""
tools/google_calendar.py - Google Calendar Integration
======================================================
Handles:
  - check_availability(date) - Check free/busy slots on a given date
  - book_appointment(date, time, patient_name) - Create a calendar event
"""

import os
import logging
from datetime import datetime, timedelta

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from config import TIMEZONE
import asyncio

logger = logging.getLogger(__name__)


def _resolve_date(date_str: str) -> str:
    """
    Convert relative date strings to YYYY-MM-DD format.
    Handles: 'today', 'tomorrow', 'day after tomorrow', and actual YYYY-MM-DD.
    Returns the date in YYYY-MM-DD format.
    """
    normalized = date_str.strip().lower()
    today = datetime.now()

    relative_map = {
        "today": today,
        "tomorrow": today + timedelta(days=1),
        "day after tomorrow": today + timedelta(days=2),
        "parso": today + timedelta(days=2),  # Hindi for day after tomorrow
        "kal": today + timedelta(days=1),     # Hindi for tomorrow
        "aaj": today,                          # Hindi for today
    }

    # Check for exact relative match
    if normalized in relative_map:
        return relative_map[normalized].strftime("%Y-%m-%d")

    # Check if it contains a relative word (e.g., "tomorrow morning")
    for keyword, resolved_date in relative_map.items():
        if keyword in normalized:
            return resolved_date.strftime("%Y-%m-%d")

    # Try parsing as YYYY-MM-DD directly
    try:
        datetime.strptime(date_str.strip(), "%Y-%m-%d")
        return date_str.strip()
    except ValueError:
        pass

    # Last resort: raise with a helpful message
    raise ValueError(
        f"Could not parse date '{date_str}'. Expected YYYY-MM-DD format or "
        f"a relative date like 'today', 'tomorrow'."
    )


def _resolve_time(time_str: str) -> str:
    """
    Convert various time formats to 24-hour HH:MM format.
    Handles: '11:00 AM', '11 AM', '2:30 PM', '14:30', '11:00', etc.
    """
    time_str = time_str.strip()

    # Try multiple formats
    for fmt in ["%I:%M %p", "%I %p", "%I:%M%p", "%I%p", "%H:%M", "%H"]:
        try:
            parsed = datetime.strptime(time_str, fmt)
            return parsed.strftime("%H:%M")
        except ValueError:
            continue

    raise ValueError(
        f"Could not parse time '{time_str}'. Expected HH:MM, 'HH:MM AM/PM', or 'HH AM/PM'."
    )

# Google Calendar API scopes
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Clinic working hours (used to define available slots)
CLINIC_SLOTS = [
    "10:00", "10:30", "11:00", "11:30", "12:00",
    "14:00", "14:30", "15:00", "15:30", "16:00",
    "17:00", "17:30", "18:00", "18:30",
]

# Duration of each appointment in minutes
APPOINTMENT_DURATION_MINUTES = 30


def _get_credentials() -> Credentials:
    """
    Get Google Calendar API credentials using OAuth2.
    First run will open a browser for authorization.
    Subsequent runs use the saved token.
    """
    creds = None
    token_path = os.path.join(os.path.dirname(__file__), "..", "..", "token.json")
    credentials_path = os.environ.get(
        "GOOGLE_OAUTH_CREDENTIALS_FILE",
        os.path.join(os.path.dirname(__file__), "..", "..", "credentials.json"),
    )

    # Load existing token
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # Refresh or get new token
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            is_headless = not os.environ.get("DISPLAY") and os.name != "nt"
            if is_headless and not os.path.exists(token_path):
                raise RuntimeError("Missing token.json and headless environment detected. Cannot run interactive OAuth.")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save token for next run
        with open(token_path, "w") as token_file:
            token_file.write(creds.to_json())

    return creds


def _get_calendar_service():
    """Build and return the Google Calendar API service."""
    creds = _get_credentials()
    return build("calendar", "v3", credentials=creds)


def get_calendar_id() -> str:
    """Get the configured calendar ID from environment."""
    return os.environ.get("GOOGLE_CALENDAR_ID", "primary")


async def _get_available_slots(date_str: str) -> tuple[str, list[str]]:
    """Internal helper to find available slots for a given date."""
    # Resolve relative dates like 'tomorrow' to YYYY-MM-DD
    date_str = _resolve_date(date_str)
    
    service = await asyncio.to_thread(_get_calendar_service)
    calendar_id = get_calendar_id()

    # Parse the date
    target_date = datetime.strptime(date_str, "%Y-%m-%d")

    # Define the time range for the full day
    time_min = target_date.replace(hour=0, minute=0, second=0).isoformat() + "Z"
    time_max = target_date.replace(hour=23, minute=59, second=59).isoformat() + "Z"

    # Fetch existing events for that day
    events_result = await asyncio.to_thread(
        service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute
    )

    events = events_result.get("items", [])

    # Find busy time ranges
    busy_times = set()
    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        if "T" in start:
            event_start = datetime.fromisoformat(start.replace("Z", "+00:00"))
            busy_times.add(event_start.strftime("%H:%M"))

    # Filter available slots
    available_slots = [slot for slot in CLINIC_SLOTS if slot not in busy_times]
    return date_str, available_slots


async def check_availability(date_str: str) -> str:
    """
    Check available appointment slots for a given date.

    Args:
        date_str: Date string in YYYY-MM-DD format (e.g., "2026-06-15")

    Returns:
        Human-readable string of available slots, or a message if none available.
    """
    try:
        date_str, available_slots = await _get_available_slots(date_str)
        logger.info(f"Resolved date: {date_str}")

        if not available_slots:
            return f"Sorry, there are no available slots on {date_str}. Would you like to check another date?"

        # Format slots for speech
        formatted_slots = []
        for slot in available_slots:
            hour = int(slot.split(":")[0])
            minute = slot.split(":")[1]
            period = "AM" if hour < 12 else "PM"
            display_hour = hour if hour <= 12 else hour - 12
            if minute == "00":
                formatted_slots.append(f"{display_hour} {period}")
            else:
                formatted_slots.append(f"{display_hour}:{minute} {period}")

        slots_text = ", ".join(formatted_slots)
        return f"Available slots on {date_str}: {slots_text}. Which time works best for you?"

    except Exception as e:
        logger.error(f"Error checking availability: {e}")
        return "I'm having trouble checking the calendar right now. Let me have someone call you back with available times."


async def book_appointment(
    date_str: str,
    time_str: str,
    patient_name: str,
    reason: str = "Dental Appointment",
) -> str:
    """
    Book an appointment on Google Calendar.

    Args:
        date_str: Date in YYYY-MM-DD format
        time_str: Time in HH:MM format (24-hour)
        patient_name: Name of the patient
        reason: Reason for the appointment

    Returns:
        Confirmation message or error.
    """
    try:
        # Resolve time format (e.g. '11:00 AM' -> '11:00')
        time_str = _resolve_time(time_str)
        
        date_str, available_slots = await _get_available_slots(date_str)
        logger.info(f"Resolved date for booking: {date_str}, time: {time_str}")
        
        if time_str not in available_slots:
            return f"I'm sorry, but the slot at {time_str} is no longer available on {date_str}. Please choose another time."

        service = await asyncio.to_thread(_get_calendar_service)
        calendar_id = get_calendar_id()

        # Parse date and time
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(TIMEZONE)
        
        start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        end_dt = start_dt + timedelta(minutes=APPOINTMENT_DURATION_MINUTES)

        from config import NAME

        # Create the event
        event = {
            "summary": f"Dental Appointment - {patient_name}",
            "description": f"Patient: {patient_name}\nReason: {reason}\nBooked by: AI Assistant ({NAME})",
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": TIMEZONE,
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": TIMEZONE,
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 60},
                    {"method": "popup", "minutes": 15},
                ],
            },
        }

        created_event = await asyncio.to_thread(
            service.events().insert(
                calendarId=calendar_id,
                body=event,
            ).execute
        )

        logger.info(f"Appointment booked: {created_event.get('htmlLink')}")

        # Format confirmation for speech
        hour = int(time_str.split(":")[0])
        minute = time_str.split(":")[1]
        period = "AM" if hour < 12 else "PM"
        display_hour = hour if hour <= 12 else hour - 12
        if minute == "00":
            time_spoken = f"{display_hour} o'clock {period}"
        else:
            time_spoken = f"{display_hour} {minute} {period}"

        return (
            f"Your appointment has been booked successfully! "
            f"{patient_name}, your dental appointment is confirmed for "
            f"{date_str} at {time_spoken}. "
            f"Please arrive ten minutes early."
        )

    except Exception as e:
        logger.error(f"Error booking appointment: {e}")
        return "I'm sorry, I wasn't able to book the appointment right now. Let me have someone call you back to confirm."


async def cancel_appointment(date_str: str, patient_name: str) -> str:
    """
    Cancel a dental appointment on Google Calendar by deleting the corresponding event.
    """
    try:
        # Resolve date
        date_str = _resolve_date(date_str)
        service = await asyncio.to_thread(_get_calendar_service)
        calendar_id = get_calendar_id()
        
        target_date = datetime.strptime(date_str, "%Y-%m-%d")
        time_min = target_date.replace(hour=0, minute=0, second=0).isoformat() + "Z"
        time_max = target_date.replace(hour=23, minute=59, second=59).isoformat() + "Z"
        
        # List events for that day
        events_result = await asyncio.to_thread(
            service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
            ).execute
        )
        events = events_result.get("items", [])
        
        # Find the event matching patient_name
        event_to_delete = None
        for event in events:
            summary = event.get("summary", "").lower()
            description = event.get("description", "").lower()
            if patient_name.lower() in summary or patient_name.lower() in description:
                event_to_delete = event
                break
                
        if not event_to_delete:
            return f"I couldn't find any appointment for {patient_name} on {date_str}. Please double-check the date."
            
        event_id = event_to_delete["id"]
        await asyncio.to_thread(
            service.events().delete(
                calendarId=calendar_id,
                eventId=event_id,
            ).execute
        )
        logger.info(f"Deleted event {event_id} for {patient_name} on {date_str}")
        return f"Successfully cancelled the appointment for {patient_name} on {date_str}."
        
    except Exception as e:
        logger.error(f"Error cancelling appointment: {e}")
        return "I'm sorry, I encountered an issue while trying to cancel the appointment on the calendar."


async def reschedule_appointment(
    old_date_str: str,
    new_date_str: str,
    new_time_str: str,
    patient_name: str,
    reason: str = "Rescheduled Dental Appointment",
) -> str:
    """
    Reschedule an appointment by cancelling the old one and booking a new one.
    """
    # 1. Cancel old appointment
    cancel_result = await cancel_appointment(old_date_str, patient_name)
    if "couldn't find" in cancel_result:
        return cancel_result  # Return the not-found message
        
    # 2. Book new appointment
    book_result = await book_appointment(new_date_str, new_time_str, patient_name, reason)
    return f"Successfully rescheduled. {book_result}"
