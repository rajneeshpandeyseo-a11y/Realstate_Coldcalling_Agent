"""Gemini (Google AI) LLM provider.

Implements the LLMProvider interface against Google's `generateContent` REST
API (https://ai.google.dev/api/generate-content). The endpoint is:

    POST {LLM_ENDPOINT}/models/{GEMINI_MODEL}:generateContent

with the API key sent in the `x-goog-api-key` header. The request body is a
JSON `GenerateContentRequest`; the response's first candidate's text is
returned plus token usage from `usageMetadata`.

Cost is estimated from the configured per-1M-token input/output rates, the
cheapest instruction-following budget tier (matching the default
`gemini-2.5-flash-lite`). This is always a *fallback* in this app - the
deterministic rule engine handles most turns, so the LLM is rarely invoked.
"""

from __future__ import annotations

import time

import httpx

from app.config import settings
from app.logging_config import get_logger
from app.providers.base import CostEstimate, LLMProvider, LLMResult

log = get_logger("app.providers.llm.gemini")


class GeminiLLMProvider(LLMProvider):
    """Generates text completions via Google's Gemini generateContent API."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._api_key = api_key or settings.GEMINI_API_KEY
        self.endpoint_base = settings.LLM_ENDPOINT
        self.model = settings.GEMINI_MODEL
        self.rate_per_1m_input = settings.LLM_COST_PER_1M_INPUT
        self.rate_per_1m_output = settings.LLM_COST_PER_1M_OUTPUT
        self._transport = transport

    def name(self) -> str:
        return "gemini"

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> LLMResult:
        if not self._api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured. Set it in .env to use real LLM."
            )

        url = f"{self.endpoint_base}/models/{self.model}:generateContent"
        payload: dict = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=60.0, transport=self._transport
            ) as client:
                resp = await client.post(
                    url,
                    headers={
                        "x-goog-api-key": self._api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            log.warning(
                "gemini llm request failed",
                extra={"ctx": {"error": str(exc)}},
            )
            raise

        latency_ms = int((time.perf_counter() - started) * 1000)
        if resp.status_code != 200:
            log.warning(
                "gemini llm non-200",
                extra={"ctx": {"status": resp.status_code, "body": resp.text[:200]}},
            )
            resp.raise_for_status()

        body = resp.json()
        candidates = (body or {}).get("candidates") or []
        if not candidates:
            raise RuntimeError("gemini llm returned no candidates")

        parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
        text = "".join(
            part.get("text", "") for part in parts if isinstance(part, dict)
        ).strip()
        if not text:
            raise RuntimeError("gemini llm returned empty text")

        usage = (body or {}).get("usageMetadata") or {}
        input_tokens = usage.get("promptTokenCount", 0)
        output_tokens = usage.get("candidatesTokenCount", 0)

        return LLMResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost=self._estimate_cost(input_tokens, output_tokens),
            provider=self.name(),
            raw={"model_version": (body or {}).get("modelVersion")},
        )

    # --------------------------------------------------------------- helpers

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> CostEstimate:
        return CostEstimate(
            currency="USD",
            component="llm",
            amount=round(
                input_tokens * self.rate_per_1m_input / 1_000_000
                + output_tokens * self.rate_per_1m_output / 1_000_000,
                8,
            ),
            quantity=input_tokens + output_tokens,
            unit="tokens",
            rate=self.rate_per_1m_input,
            breakdown={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )
