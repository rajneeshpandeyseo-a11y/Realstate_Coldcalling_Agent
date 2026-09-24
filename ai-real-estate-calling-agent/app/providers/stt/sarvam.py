"""Sarvam Speech-to-Text provider.

Implements the STTProvider interface against Sarvam's REST transcribe endpoint
(https://api.sarvam.ai/speech-to-text). The `codemix` mode is well-suited to
Hinglish call audio; the model is configurable via settings.

Cost is estimated from the configured per-minute rate (`STT_COST_PER_MINUTE`)
so call cost tracking stays consistent with the rest of the app. The actual
billed amount may differ; treat this as an estimate (Phase 14 refines it).
"""

from __future__ import annotations

import time

import httpx

from app.config import settings
from app.logging_config import get_logger
from app.providers.base import CostEstimate, STTProvider, STTResult

log = get_logger("app.providers.stt.sarvam")


class SarvamSTTProvider(STTProvider):
    """Transcribes audio via Sarvam's Speech-to-Text REST API."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._api_key = api_key or settings.SARVAM_API_KEY
        self.endpoint = settings.STT_ENDPOINT
        self.model = settings.STT_MODEL
        self.mode = settings.STT_MODE
        self.language_code = settings.STT_LANGUAGE_CODE
        self.rate_per_minute = settings.STT_COST_PER_MINUTE
        self._transport = transport

    def name(self) -> str:
        return "sarvam"

    async def transcribe(
        self,
        audio: bytes,
        *,
        language: str = "hi-Latn",
        content_type: str = "audio/wav",
    ) -> STTResult:
        if not self._api_key:
            raise RuntimeError(
                "SARVAM_API_KEY is not configured. Set it in .env to use real STT."
            )

        # Determine the filename/content type used by multipart upload.
        filename = "audio.wav" if content_type == "audio/wav" else "audio.bin"

        files = {
            "file": (filename, audio, content_type),
        }
        data = {
            "model": self.model,
            "mode": self.mode,
            "language_code": self.language_code,
        }

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=60.0, transport=self._transport
            ) as client:
                resp = await client.post(
                    self.endpoint,
                    headers={"api-subscription-key": self._api_key},
                    data=data,
                    files=files,
                )
        except httpx.HTTPError as exc:
            log.warning(
                "sarvam stt request failed",
                extra={"ctx": {"error": str(exc)}},
            )
            raise

        latency_ms = int((time.perf_counter() - started) * 1000)
        if resp.status_code != 200:
            log.warning(
                "sarvam stt non-200",
                extra={"ctx": {"status": resp.status_code, "body": resp.text[:200]}},
            )
            resp.raise_for_status()

        payload = resp.json()
        transcript = (payload or {}).get("transcript") or ""
        audio_seconds = max(0.1, len(audio) / 16000.0)

        cost = CostEstimate(
            currency="INR",
            component="stt",
            amount=round(self.rate_per_minute * audio_seconds / 60.0, 6),
            quantity=round(audio_seconds, 2),
            unit="minutes",
            rate=self.rate_per_minute,
        )
        return STTResult(
            text=transcript,
            confidence=0.0,
            duration_seconds=audio_seconds,
            latency_ms=latency_ms,
            cost=cost,
            provider=self.name(),
            raw={"request_id": (payload or {}).get("request_id")},
        )
