"""Provider abstraction layer - base types, cost model, and interfaces.

Every external provider (telephony, STT, TTS, LLM) is exposed through a small
ABC. The rest of the app depends only on these interfaces, so swapping a real
provider for the mock (or vice-versa) is a config change, not a code change.

Cost estimation lives here so consumers always get a consistent cost model
and the Phase 14 cost optimization surface is centralised.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderKind(str, enum.Enum):
    TELEPHONY = "telephony"
    STT = "stt"
    TTS = "tts"
    LLM = "llm"


@dataclass
class CostEstimate:
    """Estimated cost for a single provider call."""

    currency: str = "INR"
    component: str = "generic"
    amount: float = 0.0
    quantity: float | None = None   # minutes / chars / tokens
    unit: str | None = None
    rate: float | None = None       # per-unit rate used
    breakdown: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------- results


@dataclass
class STTResult:
    text: str
    confidence: float = 0.0
    duration_seconds: float = 0.0
    latency_ms: int = 0
    cost: CostEstimate | None = None
    provider: str = "unknown"
    # Raw provider response kept for debugging/audit (never secrets).
    raw: dict[str, Any] | None = None


@dataclass
class TTSResult:
    # Audio payload. Bytes when binary; base64 string when `as_base64`.
    audio: bytes
    format: str = "wav"
    as_base64: bool = False
    char_count: int = 0
    latency_ms: int = 0
    cost: CostEstimate | None = None
    provider: str = "unknown"
    raw: dict[str, Any] | None = None


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost: CostEstimate | None = None
    provider: str = "unknown"
    raw: dict[str, Any] | None = None


# ---------------------------------------------------------------- telephony


@dataclass
class CallInitiationResult:
    provider_call_id: str
    status: str = "initiated"
    details: dict[str, Any] = field(default_factory=dict)


class CallEventType(str, enum.Enum):
    """Plivo-style call events we care about."""

    INITIATED = "initiated"
    RINGING = "ringing"
    ANSWERED = "answered"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    NO_ANSWER = "no-answer"
    BUSY = "busy"
    FAILED = "failed"


# -------------------------------------------------------------------- ABCs


class TelephonyProvider(ABC):
    kind = ProviderKind.TELEPHONY

    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g. 'plivo', 'mock')."""

    @abstractmethod
    async def place_call(
        self,
        to_number: str,
        from_number: str,
        *,
        answer_url: str | None = None,
        hangup_url: str | None = None,
        status_callback_url: str | None = None,
        recording_enabled: bool = False,
    ) -> CallInitiationResult:
        """Initiate an outbound call. Returns provider id and outcome params."""

    @abstractmethod
    def parse_webhook_event(self, event_type: str, payload: dict) -> CallEventType:
        """Map a provider webhook/status payload onto a CallEventType."""

    @abstractmethod
    async def hangup(self, provider_call_id: str) -> bool:
        """Hang up an in-progress call by its provider call id.

        Returns True when the provider accepted the hangup, False otherwise
        (e.g. mock mode or provider reports the call is already gone).
        """


class STTProvider(ABC):
    kind = ProviderKind.STT

    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def transcribe(
        self,
        audio: bytes,
        *,
        language: str = "hi-Latn",
        content_type: str = "audio/wav",
    ) -> STTResult:
        """Transcribe audio bytes to text."""


class TTSProvider(ABC):
    kind = ProviderKind.TTS

    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "default",
        format: str = "wav",
    ) -> TTSResult:
        """Synthesize text into audio."""


class LLMProvider(ABC):
    kind = ProviderKind.LLM

    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> LLMResult:
        """Generate text completion from a prompt."""
