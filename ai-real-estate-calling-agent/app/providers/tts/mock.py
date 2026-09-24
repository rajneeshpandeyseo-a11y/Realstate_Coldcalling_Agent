"""Mock TTS provider.

Returns a small deterministic WAV payload based on the input text so call flow
works without any Sarvam TTS API cost. Cost is estimated from the configured
per-character TTS rate.
"""

from __future__ import annotations

import struct

from app.config import settings
from app.providers.base import CostEstimate, TTSProvider, TTSResult


def _build_silent_wav(rate: int = 24000, width: int = 2) -> bytes:
    """A minimal valid mono 16-bit PCM WAV (used by the deterministic mock)."""
    data = b"\x00\x00"
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(data), b"WAVE", b"fmt ",
        16, 1, 1, rate, rate * width, width, width * 8, b"data", len(data),
    ) + data


class MockTTSProvider(TTSProvider):
    def name(self) -> str:
        return "mock"

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "default",
        format: str = "wav",
    ) -> TTSResult:
        # Deterministic valid WAV payload sized by text length (emits the same
        # format Sarvam returns so the transcoding path is exercised in tests).
        char_count = len(text)
        wav = _build_silent_wav()

        cost = CostEstimate(
            currency="INR",
            component="tts",
            amount=round(char_count * settings.TTS_COST_PER_CHAR, 6),
            quantity=char_count,
            unit="chars",
            rate=settings.TTS_COST_PER_CHAR,
        )
        return TTSResult(
            audio=wav,
            format=format,
            char_count=char_count,
            latency_ms=5,
            cost=cost,
            provider=self.name(),
            raw={"voice": voice, "simulated": True},
        )
