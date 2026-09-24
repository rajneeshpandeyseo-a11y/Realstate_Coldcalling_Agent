"""Sarvam Text-to-Speech (Bulbul) provider.

Implements the TTSProvider interface against Sarvam's REST text-to-speech
endpoint (https://api.sarvam.ai/text-to-speech). The request is a JSON POST;
the response contains base64-encoded audio in the `audios` list.

Cost is estimated from the configured per-character rate (`TTS_COST_PER_CHAR`)
for call cost tracking. Actual billing is per character rounded up (Phase 14
refines accuracy).

A small in-memory LRU cache of synthesized audio is kept so frequently used,
identical prompts (e.g. canned Hinglish greetings/confirmations) are not
re-synthesised, saving both TTS cost and latency.
"""

from __future__ import annotations

import base64
import time
from collections import OrderedDict

import httpx

from app.config import settings
from app.logging_config import get_logger
from app.providers.base import CostEstimate, TTSProvider, TTSResult

log = get_logger("app.providers.tts.sarvam")


class SarvamTTSProvider(TTSProvider):
    """Synthesises speech via Sarvam's Bulbul Text-to-Speech REST API."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        cache_size: int = 128,
    ):
        self._api_key = api_key or settings.SARVAM_API_KEY
        self.endpoint = settings.TTS_ENDPOINT
        self.model = settings.TTS_MODEL
        self.language_code = settings.TTS_LANGUAGE_CODE
        self.voice = settings.SARVAM_TTS_VOICE
        self.pace = settings.TTS_PACE
        self.output_codec = settings.TTS_OUTPUT_CODEC
        self.sample_rate = str(settings.TTS_SAMPLE_RATE)
        self.rate_per_char = settings.TTS_COST_PER_CHAR
        self._transport = transport

        # Simple in-memory LRU cache keyed by (text, voice, output_codec).
        self._cache: OrderedDict[tuple, bytes] = OrderedDict()
        self._cache_size = cache_size

    def name(self) -> str:
        return "sarvam"

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "default",
        format: str = "wav",
    ) -> TTSResult:
        if not self._api_key:
            raise RuntimeError(
                "SARVAM_API_KEY is not configured. Set it in .env to use real TTS."
            )

        voice = self.voice if voice in (None, "", "default") else voice
        output_codec = format if format in {
            "wav", "mp3", "linear16", "mulaw", "alaw", "opus", "flac", "aac",
        } else self.output_codec

        cache_key = (text, voice, output_codec, self.sample_rate)
        cached_audio = self._cache_get(cache_key)
        if cached_audio is not None:
            char_count = len(text)
            return TTSResult(
                audio=cached_audio,
                format=output_codec,
                char_count=char_count,
                latency_ms=0,
                cost=self._estimate_cost(char_count),
                provider=self.name(),
                raw={"cached": True},
            )

        payload = {
            "text": text,
            "language_code": self.language_code,
            "speaker": voice,
            "model": self.model,
            "output_audio_codec": output_codec,
            "speech_sample_rate": self.sample_rate,
            "pace": self.pace,
        }

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=60.0, transport=self._transport
            ) as client:
                resp = await client.post(
                    self.endpoint,
                    headers={"api-subscription-key": self._api_key},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            log.warning(
                "sarvam tts request failed",
                extra={"ctx": {"error": str(exc)}},
            )
            raise

        latency_ms = int((time.perf_counter() - started) * 1000)
        if resp.status_code != 200:
            log.warning(
                "sarvam tts non-200",
                extra={"ctx": {"status": resp.status_code, "body": resp.text[:200]}},
            )
            resp.raise_for_status()

        body = resp.json()
        audios = (body or {}).get("audios") or []
        if not audios:
            raise RuntimeError("sarvam tts returned no audio in response")

        raw_audio = base64.b64decode(audios[0])
        self._cache_set(cache_key, raw_audio)

        return TTSResult(
            audio=raw_audio,
            format=output_codec,
            char_count=len(text),
            latency_ms=latency_ms,
            cost=self._estimate_cost(len(text)),
            provider=self.name(),
            raw={"request_id": (body or {}).get("request_id"), "cached": False},
        )

    # --------------------------------------------------------------- helpers

    def _estimate_cost(self, char_count: int) -> CostEstimate:
        return CostEstimate(
            currency="INR",
            component="tts",
            amount=round(char_count * self.rate_per_char, 6),
            quantity=char_count,
            unit="chars",
            rate=self.rate_per_char,
        )

    def _cache_get(self, key) -> bytes | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def _cache_set(self, key, audio: bytes) -> None:
        self._cache[key] = audio
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
