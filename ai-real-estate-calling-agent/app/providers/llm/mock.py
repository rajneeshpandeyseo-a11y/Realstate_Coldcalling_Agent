"""Mock LLM provider.

Deterministic responses so the conversation fallback path works without any
Gemini API cost. Used when MOCK_MODE is on.
"""

from __future__ import annotations

from app.config import settings
from app.providers.base import CostEstimate, LLMProvider, LLMResult


class MockLLMProvider(LLMProvider):
    def name(self) -> str:
        return "mock"

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> LLMResult:
        # Deterministic canned reply; a real fallback (Phase 9) parses intent.
        input_tokens = max(1, len(prompt.split()))
        output_tokens = 8

        cost = CostEstimate(
            currency="USD",
            component="llm",
            amount=round(
                input_tokens * settings.LLM_COST_PER_1M_INPUT / 1_000_000
                + output_tokens * settings.LLM_COST_PER_1M_OUTPUT / 1_000_000,
                8,
            ),
            quantity=input_tokens,
            unit="tokens",
            rate=None,
            breakdown={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )
        return LLMResult(
            text='{"intent": "interested", "confidence": 0.8}',
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=5,
            cost=cost,
            provider=self.name(),
            raw={"simulated": True},
        )
