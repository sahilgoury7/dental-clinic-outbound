# 🦷 Dental Clinic AI Voice Agent - Outbound Calls

AI-powered outbound calling agent for **Maya Dental Clinic** built with **LiveKit Agents**.

## Architecture

Two processes must run simultaneously for calls to work:

1. **`agent.py`** - LiveKit worker process. Registers `dental-clinic-outbound` agent with LiveKit Cloud and waits for dispatch jobs.
2. **`dialer.py`** - Orchestrator. Reads leads from Google Sheets, creates rooms, dispatches agents, and initiates SIP calls.

```
dialer.py reads sheet → creates room → dispatches agent → creates SIP participant
                                              │
                                      agent.py entrypoint()
                                              │
                                      build_prompt(lead) → AgentSession.start()
                                              │
                          STT (Deepgram nova-3) → LLM (Groq llama-3.3-70b) → TTS (Sarvam bulbul:v3)
                                              │
                          function_tool: check_availability / book_appointment / end_call
                                              │
                                  update_call_result() → Google Sheets
```

## Tech Stack

| Tool | Purpose |
|------|---------|
| LiveKit Agents | Call framework |
| Vobiz | Phone number (outbound calls) |
| Deepgram (nova-3) | STT - Speech to Text |
| Groq (llama-3.3-70b-versatile) | LLM - Brain |
| Sarvam TTS (bulbul:v3) | TTS - Text to Speech (primary) |
| Silero VAD | Voice Activity Detection |
| Google Calendar | Appointment booking |
| Google Sheets | Lead management + call results |

## Setup

### 1. Install Dependencies
```bash
uv sync
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Google Calendar Setup
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Enable **Google Calendar API** and **Google Sheets API**
3. Create OAuth2 credentials → download as `credentials.json`
4. Place `credentials.json` in the project root
5. First run will open browser for authorization

### 4. Google Sheets Setup
Create a Google Sheet with these columns:
| A | B | C | D | E | F | G | H | I | J | K |
|---|---|---|---|---|---|---|---|---|---|---|
| id | phone_number | patient_name | call_reason | last_visit_date | status | duration_min | meeting_date | meeting_time | call_cost | notes |

Set all rows you want to call to `status = pending`.

### 5. Run the Agent

**Terminal 1** - Start the agent worker:
```bash
uv run python agent.py dev
```

**Terminal 2** - Start the dialer:
```bash
uv run python dialer.py
```

### Quick Test (Single Call)
```bash
uv run python make_call.py +919876543210 --name "Rahul" --reason "checkup"
```

### Test TTS
```bash
uv run python test_tts.py
```

## ⚠️ Important Rules

- ⚠️ `.env` file KABHI bhi GitHub pe push mat karo
- ⚠️ `service_account.json` bhi secret hai - commit mat karo
- ⚠️ `credentials.json` aur `token.json` bhi secret hain
- ✅ Sirf `.env.example` share karo
- ✅ TTS ke liye hamesha expanded text use karo (numbers, emails, dates)
- ✅ Language ke hisaab se pronunciation rules follow karo

## .gitignore
Make sure these are in your `.gitignore`:
```
.env
credentials.json
service_account.json
token.json
token_sheets.json
__pycache__/
*.mp3
```
