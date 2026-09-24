"""TTS (Text-to-Speech) provider package.

Exports the abstract interface, the result type, and the concrete
implementations so consumers depend only on this package.
"""

from app.providers.base import TTSProvider, TTSResult

__all__ = ["TTSProvider", "TTSResult", "SarvamTTSProvider", "MockTTSProvider"]


def __getattr__(name: str):
    # Lazily import concrete providers to avoid heavy imports at package load.
    if name == "SarvamTTSProvider":
        from app.providers.tts.sarvam import SarvamTTSProvider

        return SarvamTTSProvider
    if name == "MockTTSProvider":
        from app.providers.tts.mock import MockTTSProvider

        return MockTTSProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

