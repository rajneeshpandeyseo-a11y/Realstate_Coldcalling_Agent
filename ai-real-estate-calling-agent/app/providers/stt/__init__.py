"""STT (Speech-to-Text) provider package.

Exports the abstract interface, the result type, and the concrete
implementations so consumers depend only on this package.
"""

from app.providers.base import STTProvider, STTResult

__all__ = ["STTProvider", "STTResult", "SarvamSTTProvider", "MockSTTProvider"]


def __getattr__(name: str):
    # Lazily import concrete providers to avoid heavy imports at package load.
    if name == "SarvamSTTProvider":
        from app.providers.stt.sarvam import SarvamSTTProvider

        return SarvamSTTProvider
    if name == "MockSTTProvider":
        from app.providers.stt.mock import MockSTTProvider

        return MockSTTProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

