import re
from livekit.plugins import sarvam

def clean_text(text: str) -> str:
    """
    Remove formatting symbols, angle brackets, and placeholders like <date> or <time>
    so that the TTS engine never speaks them out loud.
    """
    if not text:
        return text
    # Remove anything inside angle brackets like <date>, <time>
    text = re.sub(r"<[^>]*>", "", text)
    # Remove stray angle brackets
    text = text.replace("<", "").replace(">", "")
    # Remove asterisks, underscores, and backticks (markdown)
    text = text.replace("*", "").replace("_", "").replace("`", "")
    return text

class CleanSynthesizeStream:
    def __init__(self, original_stream):
        self._stream = original_stream

    def push_text(self, text: str) -> None:
        cleaned = clean_text(text)
        self._stream.push_text(cleaned)

    async def __aenter__(self):
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self._stream.__aexit__(exc_type, exc_val, exc_tb)

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._stream.__anext__()

    def __getattr__(self, name):
        return getattr(self._stream, name)

class CleanTTS(sarvam.TTS):
    def synthesize(self, text: str, *args, **kwargs):
        return super().synthesize(clean_text(text), *args, **kwargs)

    def stream(self, *args, **kwargs):
        stream_obj = super().stream(*args, **kwargs)
        return CleanSynthesizeStream(stream_obj)
