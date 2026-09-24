"""Plivo telephony provider.

Implements the TelephonyProvider interface against Plivo's Voice REST API
(https://api.plivo.com/v1/Account/{auth_id}/Call/). Outbound calls are placed
with an HTTP Basic authentication (auth_id:auth_token) and a JSON body.

When the recipient answers, Plivo fetches an XML flow from `answer_url`. Our
app serves that XML from its own FastAPI endpoint (Phase 11), which drives the
agent conversation. Hangup/status callbacks are delivered by Plivo to the
configured callback URL and mapped to CallEventType here.

Cost is estimated from the configured per-minute rate for call cost tracking;
the actual billed amount varies by destination network.
"""

from __future__ import annotations

import time

import httpx

from app.config import settings
from app.logging_config import get_logger
from app.providers.base import (
    CallEventType,
    CallInitiationResult,
    CostEstimate,
    TelephonyProvider,
)

log = get_logger("app.providers.telephony.plivo")

# Plivo CallStatus / Event values -> our canonical CallEventType.
_CALL_STATUS_MAP = {
    "initiated": CallEventType.INITIATED,
    "ringing": CallEventType.RINGING,
    "answered": CallEventType.ANSWERED,
    "in-progress": CallEventType.IN_PROGRESS,
    "in_progress": CallEventType.IN_PROGRESS,
    "completed": CallEventType.COMPLETED,
    "missed": CallEventType.NO_ANSWER,
    "no-answer": CallEventType.NO_ANSWER,
    "no_answer": CallEventType.NO_ANSWER,
    "busy": CallEventType.BUSY,
    "canceled": CallEventType.FAILED,
    "transport-error": CallEventType.FAILED,
    "transport_error": CallEventType.FAILED,
    "failed": CallEventType.FAILED,
    "hangup": CallEventType.COMPLETED,
    "json_decode_error": CallEventType.FAILED,
}


class PlivoTelephonyProvider(TelephonyProvider):
    """Places outbound Voice API calls and maps Plivo webhook events."""

    def __init__(
        self,
        auth_id: str | None = None,
        auth_token: str | None = None,
        from_number: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._auth_id = auth_id if auth_id is not None else settings.PLIVO_AUTH_ID
        self._auth_token = (
            auth_token if auth_token is not None else settings.PLIVO_AUTH_TOKEN
        )
        self.from_number = (
            from_number if from_number is not None else settings.PLIVO_PHONE_NUMBER
        )
        self.endpoint_base = settings.PLIVO_ENDPOINT
        self.rate_per_minute = settings.PLIVO_COST_PER_MINUTE
        self._transport = transport

    def name(self) -> str:
        return "plivo"

    async def place_call(
        self,
        to_number: str,
        from_number: str,
        *,
        answer_url: str | None = None,
        hangup_url: str | None = None,
        status_callback_url: str | None = None,
        recording_enabled: bool = False,
    ) -> CallInitiationResult:
        if not (self._auth_id and self._auth_token):
            raise RuntimeError(
                "PLIVO_AUTH_ID/PLIVO_AUTH_TOKEN are not configured. "
                "Set them in .env to place real calls."
            )
        if not from_number:
            from_number = self.from_number
        if not from_number:
            raise RuntimeError("No Plivo caller id configured (PLIVO_PHONE_NUMBER).")

        url = f"{self.endpoint_base}/{self._auth_id}/Call/"
        payload: dict = {
            "from": from_number,
            "to": to_number,
            "answer_method": "GET",
        }
        if answer_url:
            payload["answer_url"] = answer_url
        if status_callback_url:
            # Plivo's Call API has no `status_callback_url` (that exists only for
            # audio streams). Call lifecycle events are delivered to `ring_url`
            # (ringing) and `hangup_url` (completed), so route both there.
            # ring_url/hangup_url default to GET; force POST to match our webhook.
            payload["ring_url"] = status_callback_url
            payload["ring_method"] = "POST"
        if hangup_url:
            payload["hangup_url"] = hangup_url
        elif status_callback_url:
            payload["hangup_url"] = status_callback_url
            payload["hangup_method"] = "POST"
        if recording_enabled:
            payload["record"] = True

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=60.0, transport=self._transport
            ) as client:
                resp = await client.post(
                    url,
                    auth=(self._auth_id, self._auth_token),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            log.warning(
                "plivo call request failed",
                extra={"ctx": {"error": str(exc)}},
            )
            raise

        latency_ms = int((time.perf_counter() - started) * 1000)
        if resp.status_code != 201:
            log.warning(
                "plivo call non-201",
                extra={"ctx": {"status": resp.status_code, "body": resp.text[:200]}},
            )
            resp.raise_for_status()

        body = resp.json()
        call_id = (body or {}).get("request_uuid") or (body or {}).get("api_id") or ""
        if not call_id:
            raise RuntimeError("plivo call response missing request_uuid")

        return CallInitiationResult(
            provider_call_id=call_id,
            status="queued",
            details={
                "to": to_number,
                "from": from_number,
                "answer_url": answer_url,
                "latency_ms": latency_ms,
                "cost": self._estimate_cost(),
            },
        )

    def parse_webhook_event(self, event_type: str, payload: dict) -> CallEventType:
        # Prefer a Plivo style status/event field over the passed event_type.
        status = (
            (payload or {}).get("CallStatus")
            or (payload or {}).get("Event")
            or event_type
        )
        if isinstance(status, str):
            status = status.lower()
        return _CALL_STATUS_MAP.get(status, CallEventType.FAILED)

    async def hangup(self, provider_call_id: str) -> bool:
        """Terminate a live call: Plivo hangs up the call with the given UUID.

        The Call API supports hangup via ``DELETE /Call/{uuid}/``. We return
        True only on a 204; any other outcome is logged and reported as False
        (the caller usually then records COMPLETED via its status webhook).
        """
        if not provider_call_id:
            return False
        if not (self._auth_id and self._auth_token):
            return False
        url = f"{self.endpoint_base}/{self._auth_id}/Call/{provider_call_id}/"
        try:
            async with httpx.AsyncClient(
                timeout=30.0, transport=self._transport
            ) as client:
                resp = await client.delete(url, auth=(self._auth_id, self._auth_token))
        except httpx.HTTPError as exc:
            log.warning(
                "plivo hangup request failed",
                extra={"ctx": {"error": str(exc)}},
            )
            return False
        if resp.status_code not in (204, 200, 202):
            log.warning(
                "plivo hangup non-success",
                extra={"ctx": {"status": resp.status_code, "body": resp.text[:200]}},
            )
            return False
        log.info("plivo hangup accepted", extra={"ctx": {"call_uuid": provider_call_id}})
        return True

    # --------------------------------------------------------------- helpers

    def _estimate_cost(self) -> CostEstimate:
        # Per-call initiation has no per-minute charge itself; the estimation
        # returns the configured per-minute rate for downstream duration-based
        # cost tracking (Phase 14). quantity left as 0 until duration is known.
        return CostEstimate(
            currency="INR",
            component="telephony",
            amount=0.0,
            quantity=0.0,
            unit="minutes",
            rate=self.rate_per_minute,
        )
