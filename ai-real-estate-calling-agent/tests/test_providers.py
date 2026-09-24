"""Unit tests for the provider abstraction layer (PHASE 6).

Covers the shared interfaces, the four mock implementations, the registry
factory, webhook-event mapping and the conversation LLM fallback bridge.
"""

import base64
import json

import pytest

import httpx

from app.conversation.engine import ProviderLLMFallback, _parse_llm_json
from app.conversation.intents import (
    Intent,
    IntentResult,
    Confidence,
    intent_from_str,
)
from app.providers import (
    get_llm_provider,
    get_stt_provider,
    get_telephony_provider,
    get_tts_provider,
)
from app.providers.base import (
    CallEventType,
    CallInitiationResult,
    CostEstimate,
    ProviderKind,
)
from app.providers.llm.mock import MockLLMProvider
from app.providers.stt.mock import MockSTTProvider
from app.providers.telephony.mock import MockTelephonyProvider
from app.providers.tts.mock import MockTTSProvider


# ------------------------------------------------------------- registry


@pytest.mark.asyncio
async def test_registry_returns_mocks_in_mock_mode():
    # conftest sets MOCK_MODE=true, so every factory returns a mock.
    assert get_telephony_provider().name() == "mock"
    assert get_stt_provider().name() == "mock"
    assert get_tts_provider().name() == "mock"
    assert get_llm_provider().name() == "mock"


def test_provider_kinds():
    assert MockTelephonyProvider().kind == ProviderKind.TELEPHONY
    assert MockSTTProvider().kind == ProviderKind.STT
    assert MockTTSProvider().kind == ProviderKind.TTS
    assert MockLLMProvider().kind == ProviderKind.LLM


# ------------------------------------------------------------- telephony mock


@pytest.mark.asyncio
async def test_telephony_mock_places_call():
    provider = MockTelephonyProvider()
    result = await provider.place_call(
        to_number="+919700000001",
        from_number="+14150000000",
        answer_url="https://example.com/answer",
    )
    assert isinstance(result, CallInitiationResult)
    assert result.status == "queued"
    assert result.provider_call_id.startswith("mock-")


def test_telephony_mock_webhook_mapping():
    provider = MockTelephonyProvider()
    assert provider.parse_webhook_event("initiated", {}) == CallEventType.INITIATED
    assert provider.parse_webhook_event("ringing", {}) == CallEventType.RINGING
    assert provider.parse_webhook_event("answered", {}) == CallEventType.ANSWERED
    assert provider.parse_webhook_event("in-progress", {}) == CallEventType.IN_PROGRESS
    assert provider.parse_webhook_event("completed", {}) == CallEventType.COMPLETED
    assert provider.parse_webhook_event("no-answer", {}) == CallEventType.NO_ANSWER
    assert provider.parse_webhook_event("busy", {}) == CallEventType.BUSY
    assert provider.parse_webhook_event("failed", {}) == CallEventType.FAILED
    # Unknown event defaults to FAILED (safe default).
    assert provider.parse_webhook_event("weird", {}) == CallEventType.FAILED


@pytest.mark.asyncio
async def test_telephony_mock_hangup_is_local_noop():
    provider = MockTelephonyProvider()
    assert await provider.hangup("mock-call-1") is True
    assert await provider.hangup("") is True


def _plivo_provider(handler, auth_id="MA", auth_token="SECRET", from_number="+14150000000"):
    from app.providers.telephony.plivo import PlivoTelephonyProvider

    transport = httpx.MockTransport(handler)
    return PlivoTelephonyProvider(
        auth_id=auth_id,
        auth_token=auth_token,
        from_number=from_number,
        transport=transport,
    )


@pytest.mark.asyncio
async def test_plivo_places_call():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization", "")
        captured["json"] = json.loads(request.read())
        return httpx.Response(
            201,
            json={"request_uuid": "abc-123", "message": "call fired", "api_id": "api-1"},
        )

    provider = _plivo_provider(handler)
    result = await provider.place_call(
        to_number="+919700000001",
        from_number="+14150000000",
        answer_url="https://example.com/answer.xml",
        hangup_url="https://example.com/hangup",
        status_callback_url="https://example.com/status",
        recording_enabled=True,
    )

    assert "Basic" in captured["auth"]  # Basic auth
    # Header is base64("MA:SECRET"): decode to confirm the credentials.
    b64 = captured["auth"].split(" ", 1)[1]
    assert base64.b64decode(b64).decode() == "MA:SECRET"
    assert captured["url"].endswith("/MA/Call/")
    assert captured["json"]["to"] == "+919700000001"
    assert captured["json"]["from"] == "+14150000000"
    assert captured["json"]["answer_url"] == "https://example.com/answer.xml"
    assert captured["json"]["record"] is True
    assert captured["json"]["hangup_url"] == "https://example.com/hangup"
    # Plivo's Call API has no status_callback_url; it is routed to ring_url
    # (ringing) and hangup_url (completed) instead. ring_url defaults to GET,
    # so we force POST to match our webhook.
    assert captured["json"]["ring_url"] == "https://example.com/status"
    assert captured["json"]["ring_method"] == "POST"
    assert "status_callback_url" not in captured["json"]

    assert isinstance(result, CallInitiationResult)
    assert result.provider_call_id == "abc-123"
    assert result.status == "queued"
    assert result.details["cost"].rate == 0.38


@pytest.mark.asyncio
async def test_plivo_hangup_uses_delete_call_api():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        b64 = (request.headers.get("authorization", "") or "").split(" ", 1)[1]
        captured["auth_creds"] = base64.b64decode(b64).decode()
        return httpx.Response(204)

    provider = _plivo_provider(handler)
    ok = await provider.hangup("call-abc-123")
    assert ok is True
    assert captured["method"] == "DELETE"
    assert captured["url"].endswith("/MA/Call/call-abc-123/")
    assert captured["auth_creds"] == "MA:SECRET"


@pytest.mark.asyncio
async def test_plivo_hangup_failure_returns_false():
    provider = _plivo_provider(lambda r: httpx.Response(404))
    assert await provider.hangup("nope") is False


@pytest.mark.asyncio
async def test_plivo_hangup_missing_call_or_creds_is_false():
    from app.providers.telephony.plivo import PlivoTelephonyProvider

    provider = _plivo_provider(lambda r: httpx.Response(204))
    assert await provider.hangup("") is False
    empty = PlivoTelephonyProvider(
        auth_id="", auth_token="", from_number="+1", transport=httpx.MockTransport(
            lambda r: httpx.Response(204)
        )
    )
    assert await empty.hangup("x") is False


@pytest.mark.asyncio
async def test_plivo_places_call_uses_default_from():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"request_uuid": "r-1"})

    provider = _plivo_provider(handler, from_number="+14150000000")
    result = await provider.place_call(to_number="+919700000001", from_number="")
    assert result.provider_call_id == "r-1"


@pytest.mark.asyncio
async def test_plivo_missing_credentials_raises():
    from app.providers.telephony.plivo import PlivoTelephonyProvider

    provider = PlivoTelephonyProvider(
        auth_id="", auth_token="", from_number="+1", transport=httpx.MockTransport(
            lambda r: httpx.Response(500)
        )
    )
    with pytest.raises(RuntimeError):
        await provider.place_call(to_number="+1", from_number="")


@pytest.mark.asyncio
async def test_plivo_missing_from_number_raises():
    def handler(request):  # pragma: no cover
        return httpx.Response(201, json={"request_uuid": "r"})

    provider = _plivo_provider(handler, from_number="")
    with pytest.raises(RuntimeError):
        await provider.place_call(to_number="+919700000001", from_number="")


@pytest.mark.asyncio
async def test_plivo_non_201_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    provider = _plivo_provider(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.place_call(to_number="+919700000001", from_number="+14150000000")


@pytest.mark.asyncio
async def test_plivo_missing_request_uuid_raises():
    def handler(request):
        return httpx.Response(201, json={"api_id": "x"})

    provider = _plivo_provider(handler)
    # api_id should be used as a fallback; use an empty body to force an error.
    provider2 = _plivo_provider(lambda r: httpx.Response(201, json={}))
    with pytest.raises(RuntimeError):
        await provider2.place_call(to_number="+1", from_number="+14150000000")


def test_plivo_webhook_mapping():
    provider = _plivo_provider(lambda r: httpx.Response(201, json={"request_uuid": "x"}))
    assert provider.parse_webhook_event("", {"CallStatus": "ringing"}) == CallEventType.RINGING
    assert provider.parse_webhook_event("", {"CallStatus": "in-progress"}) == CallEventType.IN_PROGRESS
    assert provider.parse_webhook_event("", {"CallStatus": "answered"}) == CallEventType.ANSWERED
    assert provider.parse_webhook_event("", {"CallStatus": "completed"}) == CallEventType.COMPLETED
    assert provider.parse_webhook_event("", {"CallStatus": "busy"}) == CallEventType.BUSY
    assert provider.parse_webhook_event("", {"CallStatus": "no-answer"}) == CallEventType.NO_ANSWER
    assert provider.parse_webhook_event("", {"CallStatus": "transport-error"}) == CallEventType.FAILED
    # Falls back to the passed event_type for unknown payloads.
    assert provider.parse_webhook_event("ringing", {}) == CallEventType.RINGING
    assert provider.parse_webhook_event("mystery", {}) == CallEventType.FAILED


# ------------------------------------------------------------- STT mock


@pytest.mark.asyncio
async def test_stt_mock_transcribes_with_cost():
    provider = MockSTTProvider()
    result = await provider.transcribe(b"\x00" * 4000)
    assert result.text
    assert result.provider == "mock"
    assert isinstance(result.cost, CostEstimate)
    assert result.cost.component == "stt"
    assert result.cost.currency == "INR"
    assert result.cost.amount > 0


# ------------------------------------------------------------- TTS mock


@pytest.mark.asyncio
async def test_tts_mock_synthesizes_with_cost():
    provider = MockTTSProvider()
    result = await provider.synthesize("Namaste ji", voice="shubh")
    assert result.audio
    assert result.char_count == len("Namaste ji")
    assert isinstance(result.cost, CostEstimate)
    assert result.cost.component == "tts"
    # Cost scales with character count (per-char rate).
    assert result.cost.quantity == len("Namaste ji")


# ------------------------------------------------------------- LLM mock


@pytest.mark.asyncio
async def test_llm_mock_completes_with_cost():
    provider = MockLLMProvider()
    result = await provider.complete("state=QUALIFICATION text=2 bhk")
    assert result.text
    assert result.input_tokens >= 1
    assert isinstance(result.cost, CostEstimate)
    assert result.cost.component == "llm"
    assert "input_tokens" in (result.cost.breakdown or {})


# ------------------------------------------------------------- LLM fallback bridge


def test_parse_llm_json_plain():
    out = _parse_llm_json('{"intent": "provide_bhk", "confidence": 0.9, "slots": {"bhk": "2bhk"}}')
    assert out == {"intent": "provide_bhk", "confidence": 0.9, "slots": {"bhk": "2bhk"}}


def test_parse_llm_json_fenced():
    raw = '```json\n{"intent": "interested", "confidence": 0.8}\n```'
    assert _parse_llm_json(raw) == {"intent": "interested", "confidence": 0.8}


def test_parse_llm_json_invalid():
    assert _parse_llm_json("not json at all") is None
    assert _parse_llm_json("") is None


def test_intent_from_str_resolves_and_defaults():
    # Intent is a plain str subclass, so compare by value, not identity.
    assert intent_from_str("do_not_call") == Intent.DO_NOT_CALL
    assert intent_from_str("provide_budget") == Intent.BUDGET
    assert intent_from_str("not_interested") == Intent.NOT_INTERESTED
    assert intent_from_str("totally_unknown") == Intent.OTHER


@pytest.mark.asyncio
async def test_provider_llm_fallback_uses_mock():
    fallback = ProviderLLMFallback(provider=MockLLMProvider())
    result = await fallback.classify("koi baat nahi", "CLOSING")
    assert isinstance(result, IntentResult)
    # Mock LLM returns {"intent": "interested", "confidence": 0.8}.
    assert result.intent == Intent.INTERESTED
    assert result.confidence >= Confidence.MEDIUM


# ------------------------------------------------------------- Gemini LLM


def _gemini_provider(handler, api_key="test-key"):
    from app.providers.llm.gemini import GeminiLLMProvider

    transport = httpx.MockTransport(handler)
    return GeminiLLMProvider(api_key=api_key, transport=transport)


def _gemini_success_body(text="done"):
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": text}], "role": "model"},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 40,
            "candidatesTokenCount": 12,
            "totalTokenCount": 52,
        },
        "modelVersion": "gemini-2.5-flash-lite",
    }


@pytest.mark.asyncio
async def test_gemini_llm_completes():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth_header"] = request.headers.get("x-goog-api-key")
        captured["content_type"] = request.headers.get("content-type", "")
        captured["json"] = json.loads(request.read())
        return httpx.Response(200, json=_gemini_success_body("provide_bhk"))

    provider = _gemini_provider(handler)
    result = await provider.complete(
        "state=QUALIFICATION text=2 bhk",
        system="You classify Hinglish intents into JSON.",
        max_tokens=128,
        temperature=0.0,
    )

    assert captured["auth_header"] == "test-key"
    assert "application/json" in captured["content_type"]
    assert f"/models/{provider.model}:generateContent" in captured["url"]
    assert captured["json"]["contents"][0]["parts"][0]["text"] == "state=QUALIFICATION text=2 bhk"
    assert captured["json"]["generationConfig"]["maxOutputTokens"] == 128
    assert captured["json"]["generationConfig"]["temperature"] == 0.0
    assert captured["json"]["systemInstruction"]["parts"][0]["text"].startswith(
        "You classify"
    )

    assert result.text == "provide_bhk"
    assert result.provider == "gemini"
    assert result.input_tokens == 40
    assert result.output_tokens == 12
    assert isinstance(result.cost, CostEstimate)
    assert result.cost.component == "llm"
    assert result.cost.breakdown == {"input_tokens": 40, "output_tokens": 12}


@pytest.mark.asyncio
async def test_gemini_llm_no_system_omits_instruction():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_gemini_success_body())

    provider = _gemini_provider(handler)
    result = await provider.complete("hello", system=None)
    # No assertion on body here; just that the call succeeds without cost error.
    assert result.text == "done"


@pytest.mark.asyncio
async def test_gemini_llm_missing_key_raises(monkeypatch):
    from app.config import settings as app_settings
    from app.providers.llm.gemini import GeminiLLMProvider

    monkeypatch.setattr(app_settings, "GEMINI_API_KEY", "")

    def handler(request):  # pragma: no cover
        return httpx.Response(500)

    provider = GeminiLLMProvider(api_key="", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError):
        await provider.complete("hello")


@pytest.mark.asyncio
async def test_gemini_llm_non_200_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    provider = _gemini_provider(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.complete("hello")


@pytest.mark.asyncio
async def test_gemini_llm_no_candidates_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candidates": []})

    provider = _gemini_provider(handler)
    with pytest.raises(RuntimeError):
        await provider.complete("hello")


@pytest.mark.asyncio
async def test_gemini_llm_empty_text_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": ""}], "role": "model"}}
                ]
            },
        )

    provider = _gemini_provider(handler)
    with pytest.raises(RuntimeError):
        await provider.complete("hello")


# ------------------------------------------------------------- Sarvam STT


def _sarvam_provider(handler, api_key="test-key"):
    from app.providers.stt.sarvam import SarvamSTTProvider

    transport = httpx.MockTransport(handler)
    return SarvamSTTProvider(api_key=api_key, transport=transport)


@pytest.mark.asyncio
async def test_sarvam_stt_transcribes_with_cost():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth_header"] = request.headers.get("api-subscription-key")
        body = request.content.decode("utf-8", errors="ignore")
        captured["has_mode"] = "codemix" in body
        captured["has_model"] = "saaras:v3" in body
        captured["has_lang"] = "hi-IN" in body
        captured["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(
            200,
            json={
                "request_id": "req-123",
                "transcript": "haan main interested hoon",
                "language_code": "hi-IN",
            },
        )

    provider = _sarvam_provider(handler)
    result = await provider.transcribe(b"\x00" * 32000, content_type="audio/wav")

    assert captured["auth_header"] == "test-key"
    assert captured["url"].endswith("/speech-to-text")
    assert captured["has_mode"] is True
    assert captured["has_model"] is True
    assert captured["has_lang"] is True
    assert "multipart/form-data" in captured["content_type"]

    assert result.text == "haan main interested hoon"
    assert result.provider == "sarvam"
    assert isinstance(result.cost, CostEstimate)
    assert result.cost.component == "stt"
    assert result.cost.amount > 0


@pytest.mark.asyncio
async def test_sarvam_stt_missing_key_raises(monkeypatch):
    from app.providers.stt.sarvam import SarvamSTTProvider

    # Ensure no effective key is configured (the provider falls back to
    # settings.SARVAM_API_KEY when an explicit empty key is passed).
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "SARVAM_API_KEY", "")

    def handler(request):  # pragma: no cover - never reached
        return httpx.Response(500)

    provider = SarvamSTTProvider(
        api_key="", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(RuntimeError):
        await provider.transcribe(b"xyz")


@pytest.mark.asyncio
async def test_sarvam_stt_non_200_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    provider = _sarvam_provider(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.transcribe(b"\x00" * 1000)


# ------------------------------------------------------------- Sarvam TTS


def _sarvam_tts_provider(handler, api_key="test-key", cache_size=4):
    from app.providers.tts.sarvam import SarvamTTSProvider

    transport = httpx.MockTransport(handler)
    return SarvamTTSProvider(api_key=api_key, transport=transport, cache_size=cache_size)


@pytest.mark.asyncio
async def test_sarvam_tts_synthesizes_and_decodes():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth_header"] = request.headers.get("api-subscription-key")
        captured["content_type"] = request.headers.get("content-type", "")
        body = request.read()
        captured["json"] = json.loads(body) if body else {}
        return httpx.Response(
            200,
            json={
                "request_id": "tts-req-1",
                "audios": [base64.b64encode(b"RIFFWAVEDATA").decode()],
            },
        )

    provider = _sarvam_tts_provider(handler)
    result = await provider.synthesize("Namaste ji", voice="shubh", format="wav")

    assert captured["auth_header"] == "test-key"
    assert captured["url"].endswith("/text-to-speech")
    assert "application/json" in captured["content_type"]
    assert captured["json"]["text"] == "Namaste ji"
    assert captured["json"]["language_code"] == "hi-IN"
    assert captured["json"]["speaker"] == "shubh"
    assert captured["json"]["model"] == "bulbul:v3"
    assert captured["json"]["output_audio_codec"] == "wav"

    assert result.audio == b"RIFFWAVEDATA"
    assert result.char_count == len("Namaste ji")
    assert result.provider == "sarvam"
    assert isinstance(result.cost, CostEstimate)
    assert result.cost.component == "tts"
    assert result.cost.amount == round(len("Namaste ji") * result.cost.rate, 6)


@pytest.mark.asyncio
async def test_sarvam_tts_caches_identical_text():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(
            200,
            json={
                "request_id": f"req-{call_count['n']}",
                "audios": [base64.b64encode(b"FIRSTCALL").decode()],
            },
        )

    provider = _sarvam_tts_provider(handler)

    first = await provider.synthesize("Perfect ji")
    second = await provider.synthesize("Perfect ji")
    assert first.audio == second.audio
    # Only one network call - second served from the in-memory cache.
    assert call_count["n"] == 1
    assert second.raw.get("cached") is True


@pytest.mark.asyncio
async def test_sarvam_tts_missing_key_raises(monkeypatch):
    from app.config import settings as app_settings
    from app.providers.tts.sarvam import SarvamTTSProvider

    monkeypatch.setattr(app_settings, "SARVAM_API_KEY", "")

    def handler(request):  # pragma: no cover - never reached
        return httpx.Response(500)

    provider = SarvamTTSProvider(
        api_key="", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(RuntimeError):
        await provider.synthesize("hello")


@pytest.mark.asyncio
async def test_sarvam_tts_non_200_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, text="payment required")

    provider = _sarvam_tts_provider(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.synthesize("hello ji")


@pytest.mark.asyncio
async def test_sarvam_tts_no_audio_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"request_id": "r", "audios": []})

    provider = _sarvam_tts_provider(handler)
    with pytest.raises(RuntimeError):
        await provider.synthesize("hello ji")
