"""
tools/sheets.py - Google Sheets Integration
=============================================
Handles:
  - Fetching pending leads from the "Leads" sheet
  - Updating call results (status, duration, meeting info, cost)

Google Sheets Schema (Column mapping, 1-based):
  A: id
  B: phone_number (Required - rows without this are skipped)
  C: patient_name
  D: call_reason (e.g., appointment, checkup, reminder, follow_up)
  E: last_visit_date
  F: status (pending -> calling -> booked / not_interested / callback / complete)
  G: duration_min
  H: meeting_date
  I: meeting_time
  J: call_cost
  K: notes
"""

import os
import json
import logging
from datetime import datetime

import gspread
from google.oauth2.credentials import Credentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

from src.tools.schema import LeadSchema

logger = logging.getLogger(__name__)

# Column mapping (1-based index) — must match actual Google Sheet headers
COL = {
    "id": 1,
    "phone_number": 2,
    "company_name": 3,
    "patient_name": 4,       # "contact_name" in the sheet header
    "industry": 5,
    "call_reason": 6,        # "call_reason / patient_type" in the sheet header
    "status": 7,
    "duration_min": 8,
    "meeting_date": 9,
    "meeting_time": 10,
    "call_cost": 11,
    "notes": 12,
    "reminder_scheduled_for": 13,
    "reminder_status": 14,
    "patient_response": 15,
}

# Per-minute API costs (approximate, in USD)
API_COSTS_PER_MINUTE = {
    "deepgram_stt": 0.0043,    # Deepgram nova-3
    "groq_llm": 0.0001,        # Groq free tier (approx)
    "sarvam_tts": 0.002,       # Sarvam TTS
    "elevenlabs_tts": 0.01,    # ElevenLabs (if used)
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_gspread_client() -> gspread.Client:
    """
    Create a gspread client using either:
    1. Service Account JSON file
    2. Service Account JSON string (env var)
    3. OAuth2 credentials
    """
    # Option 1: Service Account file
    sa_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if sa_file and os.path.exists(sa_file):
        creds = ServiceAccountCredentials.from_service_account_file(sa_file, scopes=SCOPES)
        return gspread.authorize(creds)

    # Option 2: Service Account JSON string
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_json:
        sa_info = json.loads(sa_json)
        creds = ServiceAccountCredentials.from_service_account_info(sa_info, scopes=SCOPES)
        return gspread.authorize(creds)

    # Option 3: OAuth2 credentials
    token_path = os.path.join(os.path.dirname(__file__), "..", "..", "token_sheets.json")
    credentials_path = os.environ.get(
        "GOOGLE_OAUTH_CREDENTIALS_FILE",
        os.path.join(os.path.dirname(__file__), "..", "..", "credentials.json"),
    )

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            is_headless = not os.environ.get("DISPLAY") and os.name != "nt"
            if is_headless and not os.path.exists(token_path):
                raise RuntimeError("Missing token_sheets.json and headless environment detected. Cannot run interactive OAuth.")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return gspread.authorize(creds)


def _get_worksheet() -> gspread.Worksheet:
    """Get the configured worksheet."""
    client = _get_gspread_client()
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    worksheet_name = os.environ.get("GOOGLE_WORKSHEET_NAME", "Leads")

    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID environment variable is not set.")

    spreadsheet = client.open_by_key(sheet_id)
    return spreadsheet.worksheet(worksheet_name)


def get_pending_leads() -> list[dict]:
    """
    Fetch all rows with status='pending' from the Leads sheet.

    Returns:
        List of lead dictionaries with keys matching COL names.
    """
    try:
        ws = _get_worksheet()
        all_rows = ws.get_all_values()

        leads = []
        for row_idx, row in enumerate(all_rows[1:], start=2):  # Skip header row
            # Pad row to ensure enough columns
            while len(row) < max(COL.values()):
                row.append("")

            phone = row[COL["phone_number"] - 1].strip()
            status = row[COL["status"] - 1].strip().lower()
            reminder_status = row[COL["reminder_status"] - 1].strip().lower()

            # Skip rows without phone number or non-pending status
            if not phone or status != "pending":
                continue

            # If it's a reminder call, FORCE call_reason="reminder" in the metadata
            # even if the sheet says "inbound_new" or "appointment".
            actual_call_reason = row[COL["call_reason"] - 1].strip() or "appointment"
            if reminder_status == "dialing":
                actual_call_reason = "reminder"

            lead_dict = {
                "row_index": row_idx,
                "id": row[COL["id"] - 1].strip(),
                "phone_number": phone,
                "patient_name": row[COL["patient_name"] - 1].strip() or "Unknown",
                "call_reason": actual_call_reason,
                "last_visit": "Not available",
            }
            try:
                lead = LeadSchema(**lead_dict).model_dump()
                leads.append(lead)
            except Exception as schema_err:
                logger.error(f"Validation failed for row {row_idx}: {schema_err}")

        logger.info(f"Found {len(leads)} pending leads.")
        return leads

    except Exception as e:
        logger.error(f"Error fetching leads: {e}")
        return []


def update_status(row_index: int, status: str):
    """
    Update the status column for a specific row.

    Args:
        row_index: 1-based row index in the sheet.
        status: New status value (e.g., 'calling', 'booked', 'not_interested').
    """
    try:
        ws = _get_worksheet()
        ws.update_cell(row_index, COL["status"], status)
        logger.info(f"Row {row_index}: status updated to '{status}'.")
    except Exception as e:
        logger.error(f"Error updating status for row {row_index}: {e}")

def clean_phone_number(phone_str: str) -> str:
    """
    Cleans the phone number by removing all non-digits (and '+').
    If the number is exactly 10 digits (common in India), prepends '+91'.
    If the number starts with '91' and is 12 digits, prepends '+'.
    """
    if not phone_str:
        return ""
    
    # 1. Handle edge case if agent passed words like "nine eight seven"
    word_to_digit = {
        "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    }
    
    s = phone_str.lower().strip()
    for word, digit in word_to_digit.items():
        s = s.replace(word, digit)
        
    # 2. Extract only digits and '+'
    cleaned = "".join(c for c in s if c.isdigit() or c == '+')
    
    # 3. Enforce +91 country code for Indian numbers
    if cleaned.startswith("+"):
        pass # Already has a country code
    elif len(cleaned) == 10:
        cleaned = "+91" + cleaned
    elif len(cleaned) == 12 and cleaned.startswith("91"):
        cleaned = "+" + cleaned
    elif len(cleaned) > 10 and not cleaned.startswith("+"):
        # Assume it includes country code but missing plus
        cleaned = "+" + cleaned
        
    return cleaned


def update_patient_info(row_index: int, patient_name: str, phone_number: str, meeting_date: str = "", meeting_time: str = ""):
    """
    Update the patient's name and phone number for a specific row.
    If meeting_date and meeting_time are provided, also schedules the reminder 5 minutes prior.
    
    Args:
        row_index: 1-based row index in the sheet.
        patient_name: The patient's actual name.
        phone_number: The patient's actual phone number.
        meeting_date: The booked date (optional)
        meeting_time: The booked time (optional)
    """
    try:
        ws = _get_worksheet()
        cells_to_update = []
        if patient_name and patient_name.lower() != "unknown" and patient_name.lower() != "sandbox tester":
            cells_to_update.append(gspread.Cell(row=row_index, col=COL["patient_name"], value=patient_name))
        
        if phone_number and phone_number.lower() != "sandbox_test" and phone_number != "sandbox_test_number":
            cleaned_phone = clean_phone_number(phone_number)
            if cleaned_phone:
                cells_to_update.append(gspread.Cell(row=row_index, col=COL["phone_number"], value=cleaned_phone))

        if meeting_date:
            cells_to_update.append(gspread.Cell(row=row_index, col=COL["meeting_date"], value=meeting_date))
        if meeting_time:
            cells_to_update.append(gspread.Cell(row=row_index, col=COL["meeting_time"], value=meeting_time))
            
        if meeting_date and meeting_time:
            from datetime import datetime, timedelta
            reminder_time = None
            try:
                dt_str = f"{meeting_date.strip()} {meeting_time.strip()}"
                try:
                    meeting_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
                except ValueError:
                    meeting_dt = datetime.strptime(dt_str, "%Y-%m-%d %I:%M %p")
                # Schedule reminder 5 minutes BEFORE the meeting
                reminder_time = meeting_dt - timedelta(minutes=5)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to parse meeting date/time for reminder: {e}")
            
            if reminder_time:
                cells_to_update.append(gspread.Cell(row=row_index, col=COL["reminder_scheduled_for"], value=reminder_time.strftime("%Y-%m-%d %H:%M:%S")))
                cells_to_update.append(gspread.Cell(row=row_index, col=COL["reminder_status"], value="pending"))
            
        if cells_to_update:
            ws.update_cells(cells_to_update)
            logger.info(f"Row {row_index}: patient info & booking updated")
    except Exception as e:
        logger.error(f"Error updating patient info for row {row_index}: {e}")


def update_call_result(
    row_index: int,
    status: str,
    duration_seconds: float = 0,
    meeting_date: str = "",
    meeting_time: str = "",
    notes: str = "",
    tts_provider: str = "sarvam",
    call_reason: str = "",
):
    """
    Update the full call result for a row after a call ends.

    Args:
        row_index: 1-based row index.
        status: Final call status (booked, not_interested, callback, complete).
        duration_seconds: Call duration in seconds.
        meeting_date: Booked meeting date (if applicable).
        meeting_time: Booked meeting time (if applicable).
        notes: Any notes from the call.
        tts_provider: Which TTS was used ('sarvam' or 'elevenlabs').
        call_reason: The reason for the call (to separate reminders).
    """
    try:
        ws = _get_worksheet()

        # Calculate duration in minutes
        duration_min = round(duration_seconds / 60, 2)

        # Calculate estimated call cost
        cost = _calculate_call_cost(duration_min, tts_provider)

        # Use batch update for speed (single API call instead of 6)
        cells_to_update = {
            COL["duration_min"]: str(duration_min),
            COL["call_cost"]: f"${cost:.4f}",
        }
        
        if call_reason.lower() == "reminder":
            cells_to_update[COL["reminder_status"]] = status
            cells_to_update[COL["patient_response"]] = notes
        else:
            cells_to_update[COL["status"]] = status
            cells_to_update[COL["notes"]] = notes
            if meeting_date:
                cells_to_update[COL["meeting_date"]] = meeting_date
            if meeting_time:
                cells_to_update[COL["meeting_time"]] = meeting_time
                
            # Only schedule a reminder if an appointment was actually booked
            if status == "booked" and meeting_date and meeting_time:
                from datetime import datetime, timedelta
                reminder_time = None
                
                try:
                    dt_str = f"{meeting_date.strip()} {meeting_time.strip()}"
                    # Try 24-hour format first
                    try:
                        meeting_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
                    except ValueError:
                        # Fallback to 12-hour format with AM/PM
                        meeting_dt = datetime.strptime(dt_str, "%Y-%m-%d %I:%M %p")
                    # Schedule reminder 5 minutes BEFORE the meeting
                    reminder_time = meeting_dt - timedelta(minutes=5)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"Failed to parse meeting date/time for reminder: {e}")
                
                if reminder_time:
                    cells_to_update[COL["reminder_scheduled_for"]] = reminder_time.strftime("%Y-%m-%d %H:%M:%S")
                    cells_to_update[COL["reminder_status"]] = "pending"

        cell_list = []
        for col, value in cells_to_update.items():
            cell = gspread.Cell(row=row_index, col=col, value=value)
            cell_list.append(cell)

        ws.update_cells(cell_list)

        logger.info(
            f"Row {row_index}: call result updated - status={status}, "
            f"duration={duration_min}min, cost=${cost:.4f}"
        )

    except Exception as e:
        logger.error(f"Error updating call result for row {row_index}: {e}")


def _calculate_call_cost(duration_min: float, tts_provider: str = "sarvam") -> float:
    """
    Calculate estimated API cost for a call.

    Args:
        duration_min: Call duration in minutes.
        tts_provider: 'sarvam' or 'elevenlabs'.

    Returns:
        Estimated cost in USD.
    """
    tts_key = f"{tts_provider}_tts"
    tts_cost = API_COSTS_PER_MINUTE.get(tts_key, 0.002)

    total_per_min = (
        API_COSTS_PER_MINUTE["deepgram_stt"]
        + API_COSTS_PER_MINUTE["groq_llm"]
        + tts_cost
    )

    return total_per_min * duration_min

def find_lead_by_phone(phone_number: str) -> dict | None:
    """
    Find a lead by phone number in the Leads sheet.
    Useful for inbound calls to look up caller details.
    
    Args:
        phone_number: The phone number to search for (e.g. '+1234567890').
        
    Returns:
        A dictionary with lead details if found, else None.
    """
    try:
        ws = _get_worksheet()
        # Clean the input phone number
        search_phone = phone_number.strip().lstrip('+')
        
        # We could use ws.findall(), but let's get all values and search to map columns correctly
        all_rows = ws.get_all_values()
        
        for row_idx, row in enumerate(all_rows[1:], start=2): # Skip header
            if len(row) >= COL["phone_number"]:
                cell_phone = row[COL["phone_number"] - 1].strip().lstrip('+')
                # Simple loose matching for now
                if search_phone in cell_phone or cell_phone in search_phone:
                    # Pad row
                    while len(row) < max(COL.values()):
                        row.append("")
                        
                    lead_dict = {
                        "row_index": row_idx,
                        "id": row[COL["id"] - 1].strip(),
                        "phone_number": row[COL["phone_number"] - 1].strip(),
                        "patient_name": row[COL["patient_name"] - 1].strip() or "Unknown",
                        "call_reason": row[COL["call_reason"] - 1].strip() or "inbound",
                        "last_visit": "Not available",
                        "status": row[COL["status"] - 1].strip(),
                    }
                    logger.info(f"Found existing lead for phone {phone_number} at row {row_idx}")
                    return lead_dict
                    
        logger.info(f"No existing lead found for phone {phone_number}")
        return None
        
    except Exception as e:
        logger.error(f"Error finding lead by phone {phone_number}: {e}")
        return None

def insert_new_lead(phone_number: str, patient_name: str = "Unknown", call_reason: str = "inbound_new") -> int:
    """
    Insert a new lead/patient into the Leads sheet for first-time inbound callers.
    
    Args:
        phone_number: The caller's phone number.
        patient_name: Optional name if collected during the call.
        call_reason: Why they are calling.
        
    Returns:
        The row index of the newly inserted lead, or -1 if failed.
    """
    try:
        ws = _get_worksheet()
        
        # Determine the next ID (simple approach: count rows)
        all_values = ws.get_all_values()
        next_id = f"L{len(all_values):03d}" 
        next_row_idx = len(all_values) + 1
        
        # Create an empty row of the right size
        new_row = [""] * max(COL.values())
        
        # Populate known fields
        new_row[COL["id"] - 1] = next_id
        new_row[COL["phone_number"] - 1] = phone_number
        new_row[COL["patient_name"] - 1] = patient_name
        new_row[COL["call_reason"] - 1] = call_reason
        new_row[COL["status"] - 1] = "inbound_active"
        
        # Append the row
        ws.append_row(new_row)
        logger.info(f"Inserted new lead {next_id} for phone {phone_number} at row {next_row_idx}")
        
        return next_row_idx
        
    except Exception as e:
        logger.error(f"Error inserting new lead for phone {phone_number}: {e}")
        return -1
