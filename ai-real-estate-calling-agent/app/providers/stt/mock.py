"""Mock STT provider.

Returns a deterministic transcript so conversation testing works without any
Sarvam API cost. Cost is estimated from the configured per-minute STT rate.
"""

from __future__ import annotations

from app.config import settings
from app.providers.base import CostEstimate, STTProvider, STTResult

# A few canned transcripts keyed by a rough payload size so demos are stable.
_CANNED = [
    "haan main interested hoon",
    "haan batao",
    "ghar kharidna hai",
    "3 bhk, 40 lakh ke andar",
]


class MockSTTProvider(STTProvider):
    def name(self) -> str:
        return "mock"

    async def transcribe(
        self,
        audio: bytes,
        *,
        language: str = "hi-Latn",
        content_type: str = "audio/wav",
    ) -> STTResult:
        # Estimate ~1s of audio per ~2KB; never zero.
        duration = max(0.1, len(audio) / 2000.0)
        idx = len(audio) % len(_CANNED)
        text = _CANNED[idx]

        cost = CostEstimate(
            currency="INR",
            component="stt",
            amount=round(duration * settings.STT_COST_PER_MINUTE / 60.0, 6),
            quantity=round(duration, 2),
            unit="minutes",
            rate=settings.STT_COST_PER_MINUTE,
        )
        return STTResult(
            text=text,
            confidence=0.95,
            duration_seconds=duration,
            latency_ms=5,
            cost=cost,
            provider=self.name(),
            raw={"simulated": True},
        )
