"""
config.py - Centralized configuration for the Dental Clinic Outbound Voice Agent
================================================================================
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Identity / Brand
NAME = "Ishita"
CLINIC = "City Dental Clinic"

# Localization
TIMEZONE = os.environ.get("CLINIC_TIMEZONE", "Asia/Kolkata")
