"""
test_tts.py - TTS Test Script
==============================
Tests Sarvam TTS pronunciation with sample dental clinic text.
Writes output to test_output.mp3.

Usage:
    uv run python test_tts.py
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Test sentences with pronunciation rules applied
TEST_SENTENCES = {
    "english_greeting": (
        "Hello! Am I speaking with Rahul? Hi, this is Tara calling from "
        "Maya Dental Clinic. I'm calling to help you schedule your dental appointment."
    ),
    "english_pricing": (
        "Our routine dental checkup costs four thousand five hundred rupees. "
        "Teeth cleaning ranges from fifteen hundred to twenty five hundred rupees. "
        "And root canal treatment is between five thousand to ten thousand rupees."
    ),
    "english_appointment": (
        "Your appointment has been booked successfully! "
        "Rahul, your dental appointment is confirmed for fifteenth June "
        "at two thirty PM. Please arrive ten minutes early."
    ),
    "english_phone": (
        "If you need to reschedule, you can call us at "
        "plus nine one, nine eight seven six five, four three two one zero."
    ),
    "hindi_greeting": (
        "Namaste! Kya main Rahul ji se baat kar rahi hoon? "
        "Main Tara bol rahi hoon Maya Dental Clinic se."
    ),
    "hindi_pricing": (
        "Hamara routine checkup chaar hazaar paanch sau rupaye ka hai. "
        "Aur teeth cleaning pandrah sau se pachchees sau rupaye tak hoti hai."
    ),
}


async def test_sarvam_tts():
    """Test Sarvam TTS with sample sentences."""
    try:
        from livekit.plugins import sarvam

        tts = sarvam.TTS(model="bulbul:v3")

        for name, text in TEST_SENTENCES.items():
            logger.info(f"\n--- Testing: {name} ---")
            logger.info(f"Text: {text[:80]}...")

            output_file = f"test_output_{name}.mp3"
            logger.info(f"Generating audio → {output_file}")

            # Synthesize speech
            audio_stream = tts.synthesize(text)
            audio_data = b""
            async for chunk in audio_stream:
                if hasattr(chunk, "data"):
                    audio_data += chunk.data

            if audio_data:
                with open(output_file, "wb") as f:
                    f.write(audio_data)
                logger.info(f"✅ Saved: {output_file} ({len(audio_data)} bytes)")
            else:
                logger.warning(f"⚠️ No audio data received for: {name}")

    except ImportError:
        logger.error("livekit-plugins-sarvam not installed. Run: uv sync")
    except Exception as e:
        logger.error(f"TTS test failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_sarvam_tts())
