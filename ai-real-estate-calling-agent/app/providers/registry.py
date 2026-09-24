"""Provider registry / factory.

Builds the concrete provider instances based on application settings.
In MOCK_MODE (or when a provider is set to `mock`) the mock implementation is
returned so the system never places real paid calls during development/test.

Real providers are introduced in later phases (Plivo Phase 10, Sarvam STT
Phase 7, Sarvam TTS Phase 8, Gemini LLM Phase 9) - selecting them here is a
config change once those are implemented.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import settings
from app.providers.base import (
    LLMProvider,
    STTProvider,
    TelephonyProvider,
    TTSProvider,
)


def _select(configured: str) -> str:
    """Always return 'mock' when mock mode is on."""
    if settings.is_mock:
        return "mock"
    return configured or "mock"


@lru_cache
def get_telephony_provider() -> TelephonyProvider:
    name = _select(settings.TELEPHONY_PROVIDER)
    if name == "mock":
        from app.providers.telephony.mock import MockTelephonyProvider

        return MockTelephonyProvider()
    if name == "plivo":
        from app.providers.telephony.plivo import PlivoTelephonyProvider

        return PlivoTelephonyProvider()
    raise NotImplementedError(f"telephony provider '{name}' not implemented yet")


@lru_cache
def get_stt_provider() -> STTProvider:
    name = _select(settings.STT_PROVIDER)
    if name == "mock":
        from app.providers.stt.mock import MockSTTProvider

        return MockSTTProvider()
    if name == "sarvam":
        from app.providers.stt.sarvam import SarvamSTTProvider

        return SarvamSTTProvider()
    raise NotImplementedError(f"stt provider '{name}' not implemented yet")


@lru_cache
def get_tts_provider() -> TTSProvider:
    name = _select(settings.TTS_PROVIDER)
    if name == "mock":
        from app.providers.tts.mock import MockTTSProvider

        return MockTTSProvider()
    if name == "sarvam":
        from app.providers.tts.sarvam import SarvamTTSProvider

        return SarvamTTSProvider()
    raise NotImplementedError(f"tts provider '{name}' not implemented yet")


@lru_cache
def get_llm_provider() -> LLMProvider:
    name = _select(settings.LLM_PROVIDER)
    if name in ("mock", "fixed"):
        # `fixed` is the deterministic no-LLM provider (guaranteed cost-free,
        # never calls an external model); `mock` is the deterministic stand-in.
        # Both incur zero external cost.
        if name == "fixed":
            from app.providers.llm.fixed import FixedLLMProvider

            return FixedLLMProvider()
        from app.providers.llm.mock import MockLLMProvider

        return MockLLMProvider()
    if name == "gemini":
        from app.providers.llm.gemini import GeminiLLMProvider

        return GeminiLLMProvider()
    raise NotImplementedError(f"llm provider '{name}' not implemented yet")
