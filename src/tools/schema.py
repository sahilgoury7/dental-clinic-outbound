from pydantic import BaseModel, Field
from typing import Optional

class LeadSchema(BaseModel):
    id: Optional[str] = ""
    phone_number: str
    patient_name: str = "Unknown"
    call_reason: str = "appointment"
    last_visit: Optional[str] = "Not available"
    status: Optional[str] = ""
    duration_min: Optional[str] = ""
    meeting_date: Optional[str] = ""
    meeting_time: Optional[str] = ""
    call_cost: Optional[str] = ""
    notes: Optional[str] = ""
    reminder_scheduled_for: Optional[str] = ""
    reminder_status: Optional[str] = ""
    row_index: Optional[int] = None
