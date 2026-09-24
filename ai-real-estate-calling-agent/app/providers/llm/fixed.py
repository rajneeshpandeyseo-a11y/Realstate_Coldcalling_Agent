"""Fixed (deterministic, no-LLM) provider.

Ready for TODAY's controlled, rule-only deployment: it performs NO external
call and incurs NO cost. It exists so the system can run with the conversation
engine in fully deterministic (rule-based) mode, where even the LLM *fallback*
is disabled.

Any attempt to call it raises a clear error instead of silently paying an LLM
vendor, so a misconfiguration can never generate a billed LLM request while
the system is set to `LLM_PROVIDER=fixed`.
"""

from __future__ import annotations

from app.providers.base import CostEstimate, LLMProvider, LLMResult


class FixedLLMProvider(LLMProvider):
    """Guaranteed cost-free provider - refuses to call any external LLM."""

    def name(self) -> str:
        return "fixed"

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> LLMResult:
        raise RuntimeError(
            "LLM is disabled in fixed mode (LLM_PROVIDER=fixed). "
            "The deterministic rule engine is running without any LLM fallback "
            "so no external (and therefore no paid) call is made. Enable a real "
            "LLM by setting LLM_PROVIDER=gemini and GEMINI_API_KEY later."
        )
