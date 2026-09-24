"""Provider abstraction layer.

Exposes the shared interfaces, result/value types and the provider registry so
the rest of the app can depend on this single package.
"""

from app.providers.base import (
    CallEventType,
    CallInitiationResult,
    CostEstimate,
    LLMProvider,
    LLMResult,
    ProviderKind,
    STTProvider,
    STTResult,
    TelephonyProvider,
    TTSProvider,
    TTSResult,
)
from app.providers.registry import (
    get_llm_provider,
    get_stt_provider,
    get_telephony_provider,
    get_tts_provider,
)

__all__ = [
    "CallEventType",
    "CallInitiationResult",
    "CostEstimate",
    "LLMProvider",
    "LLMResult",
    "ProviderKind",
    "STTProvider",
    "STTResult",
    "TelephonyProvider",
    "TTSProvider",
    "TTSResult",
    "get_llm_provider",
    "get_stt_provider",
    "get_telephony_provider",
    "get_tts_provider",
]
