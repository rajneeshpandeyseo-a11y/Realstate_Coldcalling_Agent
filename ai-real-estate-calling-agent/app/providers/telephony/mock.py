"""Mock telephony provider.

Simulates Plivo-style call initiation and webhook events with deterministic,
zero-cost behaviour. Used whenever MOCK_MODE is on so no real paid call is
ever placed during development/testing.
"""

from __future__ import annotations

import uuid

from app.providers.base import (
    CallEventType,
    CallInitiationResult,
    CostEstimate,
    TelephonyProvider,
)

# Nominal Plivo India per-minute rate (Sept 2026 research, not guaranteed).
# Used to estimate cost in mock mode so cost logic is exercised without billing.
PLIVO_ESTIMATED_RATE_PER_MINUTE = 0.38


class MockTelephonyProvider(TelephonyProvider):
    # Nominal per-minute estimate surfaced to the cost ledger.
    per_minute_rate: float = PLIVO_ESTIMATED_RATE_PER_MINUTE

    def name(self) -> str:
        return "mock"

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
        # Deterministic pseudo-id so tests are stable.
        call_id = f"mock-{uuid.uuid4()}"
        return CallInitiationResult(
            provider_call_id=call_id,
            status="queued",
            details={
                "to": to_number,
                "from": from_number,
                "answer_url": answer_url,
                "status_callback_url": status_callback_url,
                "simulated": True,
            },
        )

    def parse_webhook_event(self, event_type: str, payload: dict) -> CallEventType:
        # Accepts our own string or Plivo-style event verbs.
        mapping = {
            "initiated": CallEventType.INITIATED,
            "ringing": CallEventType.RINGING,
            "answered": CallEventType.ANSWERED,
            "in-progress": CallEventType.IN_PROGRESS,
            "in_progress": CallEventType.IN_PROGRESS,
            "completed": CallEventType.COMPLETED,
            "no-answer": CallEventType.NO_ANSWER,
            "busy": CallEventType.BUSY,
            "failed": CallEventType.FAILED,
        }
        return mapping.get(event_type, CallEventType.FAILED)

    async def hangup(self, provider_call_id: str) -> bool:
        # Mock calls are local; nothing to hang up on the provider side.
        return True
