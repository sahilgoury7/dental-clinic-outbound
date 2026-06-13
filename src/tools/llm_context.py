import asyncio
import logging
import time
from typing import Annotated

from livekit.agents import Agent, RunContext, function_tool

from src.tools.google_calendar import (
    check_availability as cal_check_availability,
    book_appointment as cal_book_appointment,
    cancel_appointment as cal_cancel_appointment,
    reschedule_appointment as cal_reschedule_appointment,
)
from src.tools.sheets import update_call_result, update_status, update_patient_info

logger = logging.getLogger(__name__)

class DentalAssistant(Agent):
    """
    AI Receptionist for the Dental Clinic.
    Handles outbound calls for appointment booking, reminders, and follow-ups.
    """

    def __init__(self, system_instructions: str, lead: dict) -> None:
        super().__init__(instructions=system_instructions)
        self.lead = lead
        self.call_start_time = time.time()
        self.booked_date = ""
        self.booked_time = ""
        self.call_status = "complete"  # Default status if call ends without specific action
        self.transcript = []           # Store transcript here

    @function_tool()
    async def check_availability(
        self,
        context: RunContext,
        date: Annotated[str, "The date to check availability for, in YYYY-MM-DD format. Example: 2026-06-15"],
    ) -> str:
        """Check if there are available appointment slots at the dental clinic for a given date. Use this when the patient wants to know available times."""
        logger.info(f"Checking availability for date: {date}")
        result = await cal_check_availability(date)
        return result

    @function_tool()
    async def book_appointment(
        self,
        context: RunContext,
        date: Annotated[str, "The appointment date in YYYY-MM-DD format. Example: 2026-06-15"],
        time_slot: Annotated[str, "The appointment time in HH:MM 24-hour format. Example: 14:00"],
        patient_name: Annotated[str, "The name of the patient booking the appointment."],
        patient_phone: Annotated[str, "The phone number of the patient. MUST be formatted as numerical digits only (e.g. '9876543210' or '+919876543210'). Ask for this if it's missing or if they are a new patient. NEVER use words."] = "",
        reason: Annotated[str, "The reason for the dental visit. Example: Routine Checkup, Root Canal, Teeth Cleaning"] = "Dental Appointment",
    ) -> str:
        """Book a dental appointment for the patient on Google Calendar. Use this after the patient confirms a date and time slot."""
        logger.info(f"Booking appointment: {patient_name} on {date} at {time_slot} for {reason}")
        result = await cal_book_appointment(date, time_slot, patient_name, reason)

        # Track the booking for post-call update
        if "booked successfully" in result.lower() or "confirmed" in result.lower():
            self.booked_date = date
            self.booked_time = time_slot
            self.call_status = "booked"
            
            # Update patient info and schedule reminder in Google Sheets right at the moment of booking
            row_index = self.lead.get("row_index")
            if row_index:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(update_patient_info, row_index, patient_name, patient_phone, date, time_slot),
                        timeout=5.0
                    )
                except Exception as e:
                    logger.error(f"Failed to update booking info in sheets: {e}")

        return result

    @function_tool()
    async def end_call(
        self,
        context: RunContext,
        reason: Annotated[str, "The reason for ending the call. Must be one of: booked, not_interested, callback, complete"],
        notes: Annotated[str, "Brief notes about the call outcome."] = "",
    ) -> str:
        """End the call gracefully and record the outcome. Use this when the conversation is complete, the patient is not interested, or they want a callback."""
        logger.info(f"Ending call - reason: {reason}, notes: {notes}")

        # Calculate call duration
        duration_seconds = time.time() - self.call_start_time
        self.call_status = reason

        # Update Google Sheets with the call result
        row_index = self.lead.get("row_index")
        if row_index:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        update_call_result,
                        row_index=row_index,
                        status=reason,
                        duration_seconds=duration_seconds,
                        meeting_date=self.booked_date,
                        meeting_time=self.booked_time,
                        notes=notes,
                        tts_provider="sarvam",
                        call_reason=self.lead.get("call_reason", ""),
                    ),
                    timeout=15.0
                )
            except asyncio.TimeoutError:
                logger.error("Google Sheets update in end_call timed out after 15 seconds")
            except Exception as e:
                logger.error(f"Failed to update sheet: {e}")

        return f"Call ended. Reason: {reason}. Duration: {duration_seconds:.0f} seconds."

    @function_tool()
    async def cancel_appointment(
        self,
        context: RunContext,
        date: Annotated[str, "The date of the appointment to cancel in YYYY-MM-DD format."],
        patient_name: Annotated[str, "The name of the patient whose appointment is being cancelled."],
    ) -> str:
        """Cancel an existing dental appointment. Use this when a patient explicitly asks to cancel their appointment."""
        logger.info(f"Cancelling appointment for {patient_name} on {date}")
        result = await cal_cancel_appointment(date, patient_name)
        if "Successfully cancelled" in result:
            self.call_status = "cancelled"
            
            row_index = self.lead.get("row_index")
            if row_index:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(update_status, row_index, "cancelled"),
                        timeout=5.0
                    )
                except Exception as e:
                    logger.error(f"Failed to update sheet for cancellation: {e}")
        return result

    @function_tool()
    async def reschedule_appointment(
        self,
        context: RunContext,
        old_date: Annotated[str, "The original appointment date in YYYY-MM-DD format."],
        new_date: Annotated[str, "The new appointment date in YYYY-MM-DD format."],
        new_time_slot: Annotated[str, "The new appointment time in HH:MM 24-hour format. Example: 14:00"],
        patient_name: Annotated[str, "The name of the patient rescheduling."],
    ) -> str:
        """Reschedule a dental appointment to a new date and time. Use this when the patient wants to change their appointment time."""
        logger.info(f"Rescheduling appointment for {patient_name} from {old_date} to {new_date} at {new_time_slot}")
        result = await cal_reschedule_appointment(old_date, new_date, new_time_slot, patient_name)
        if "Successfully rescheduled" in result:
            self.booked_date = new_date
            self.booked_time = new_time_slot
            self.call_status = "rescheduled"
            
            row_index = self.lead.get("row_index")
            if row_index:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(update_status, row_index, "rescheduled"),
                        timeout=5.0
                    )
                except Exception as e:
                    logger.error(f"Failed to update sheet for rescheduling: {e}")
        return result
