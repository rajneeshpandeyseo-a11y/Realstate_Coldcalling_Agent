"""LLM (Large Language Model) provider package.

Exports the abstract interface, the result type, and the concrete
implementations so consumers depend only on this package.
"""

from app.providers.base import LLMProvider, LLMResult

__all__ = ["LLMProvider", "LLMResult", "GeminiLLMProvider", "MockLLMProvider"]


def __getattr__(name: str):
    # Lazily import concrete providers to avoid heavy imports at package load.
    if name == "GeminiLLMProvider":
        from app.providers.llm.gemini import GeminiLLMProvider

        return GeminiLLMProvider
    if name == "MockLLMProvider":
        from app.providers.llm.mock import MockLLMProvider

        return MockLLMProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
