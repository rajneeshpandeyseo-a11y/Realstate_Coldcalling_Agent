"""Call lifecycle orchestration: state machine + event logging.

Deterministic transition table ensures the call flow is strictly controlled
(no illegal jumps), which keeps cost and behaviour predictable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crud import call as call_crud
from app.crud import call_event as event_crud
from app.crud.lead import get_lead as crud_get_lead
from app.logging_config import get_logger
from app.providers import TelephonyProvider, get_telephony_provider
from app.models.call import Call
from app.models.enums import CallStatus, LeadStatus
from app.schemas.call import CallCreate, CallUpdate
from app.services import audit, lead as lead_service

log = get_logger("app.services.call")

# Allowed forward transitions. Terminal states are leaves.
TRANSITIONS: dict[str, set[str]] = {
    CallStatus.QUEUED.value: {
        CallStatus.INITIATED.value,
        CallStatus.CANCELLED.value,
    },
    CallStatus.INITIATED.value: {
        CallStatus.RINGING.value,
        CallStatus.ANSWERED.value,
        # Plivo answers directly to `in-progress` (no separate `answered`
        # event); a fast answer may reach us before `ring_url` lands.
        CallStatus.IN_PROGRESS.value,
        CallStatus.NO_ANSWER.value,
        CallStatus.BUSY.value,
        CallStatus.FAILED.value,
        # Plivo reports short/unanswered legs as `completed` too (hangup
        # webhook without any prior answer event). Refusing it left calls
        # stuck pre-terminal with no ended_at - record the truth instead.
        CallStatus.COMPLETED.value,
    },
    CallStatus.RINGING.value: {
        CallStatus.ANSWERED.value,
        CallStatus.IN_PROGRESS.value,
        CallStatus.NO_ANSWER.value,
        CallStatus.BUSY.value,
        CallStatus.FAILED.value,
        # Same as above: a `completed` hangup can arrive straight from
        # ringing (callee hung up during the ring window). Observed live:
        # rejecting it left the call stuck at ringing forever.
        CallStatus.COMPLETED.value,
    },
    CallStatus.ANSWERED.value: {
        CallStatus.IN_PROGRESS.value,
        CallStatus.COMPLETED.value,
        CallStatus.FAILED.value,
    },
    CallStatus.IN_PROGRESS.value: {
        CallStatus.COMPLETED.value,
        CallStatus.CALLBACK_REQUESTED.value,
        CallStatus.DO_NOT_CALL.value,
        # PHASE 1.5: caller stayed silent through both nudges on the live
        # media stream (graceful hangup, no billable conversation happened).
        CallStatus.NO_RESPONSE.value,
        CallStatus.FAILED.value,
    },
}

# States in which the call is considered "live" (billing/monitoring).
ACTIVE_STATES = {
    CallStatus.QUEUED.value,
    CallStatus.INITIATED.value,
    CallStatus.RINGING.value,
    CallStatus.ANSWERED.value,
    CallStatus.IN_PROGRESS.value,
}

TERMINAL_STATES = {
    CallStatus.COMPLETED.value,
    CallStatus.NO_ANSWER.value,
    CallStatus.NO_RESPONSE.value,
    CallStatus.BUSY.value,
    CallStatus.FAILED.value,
    CallStatus.CANCELLED.value,
    CallStatus.CALLBACK_REQUESTED.value,
    CallStatus.DO_NOT_CALL.value,
}


class IllegalTransitionError(Exception):
    """Raised when a call tries to jump to an invalid state."""


def allowed(from_status: str, to_status: str) -> bool:
    """Whether the from->to transition is permitted."""
    return to_status in TRANSITIONS.get(from_status, set())


async def create_call(db: AsyncSession, data: CallCreate) -> Call:
    """Create a new call and ensure the lead is callable."""
    lead = await crud_get_lead(db, data.lead_id)
    if lead is None:
        raise KeyError("lead not found")
    if not lead_service.can_call(lead):
        raise lead_service.DNCError(f"lead {lead.id} is on the do-not-call list")

    call = await call_crud.create_call(db, data)
    call.lead = lead
    call.queued_at = datetime.now(timezone.utc)
    if not call.recording_enabled and settings.RECORDING_ENABLED:
        call.recording_enabled = True
    await db.flush()
    await event_crud.add_event(db, call_id=call.id, event_type="queued")
    await audit.record_audit(
        db, action="call.create", resource_type="call", resource_id=call.id,
        details={"lead_id": str(lead.id)},
    )
    return call


async def place_outbound_call(
    db: AsyncSession,
    call: Call,
    *,
    provider: TelephonyProvider | None = None,
):
    """Ask the configured telephony provider to place the outbound call.

    Uses the provider abstraction (Phase 6) so the business logic never talks
    to a specific vendor. In mock mode this is zero-cost and deterministic; in
    production it hands the dial to Plivo or another provider.

    ``provider`` is injectable (defaulting to the registry-selected one) so
    the Phase 12 orchestrator can drive a real/simulated call with a specific
    provider - e.g. switch providers purely through configuration.
    """
    provider = provider or get_telephony_provider()
    # Live-call safety: refuse to dial a real (paid) number unless the operator
    # has explicitly enabled live calls. This is the single choke point where a
    # real outbound call is placed, so it protects against bulk/auto/retry loops
    # accidentally going live. Mock-mode calls are always allowed (cost-free).
    if not settings.is_mock and not settings.LIVE_CALLS_ENABLED:
        raise RuntimeError(
            "live outbound calls are disabled (LIVE_CALLS_ENABLED=false). "
            "Set LIVE_CALLS_ENABLED=true AND MOCK_MODE=false to place real calls."
        )
    lead = await crud_get_lead(db, call.lead_id) if call.lead_id else None
    to_number = call.phone_number or (lead.phone if lead else None)
    from_number = settings.PLIVO_PHONE_NUMBER
    answer_url = (
        f"{settings.PUBLIC_BASE_URL}/api/v1/webhooks/plivo/answer"
        f"?call_id={call.id}"
    )
    status_callback_url = (
        f"{settings.PUBLIC_BASE_URL}/api/v1/webhooks/plivo/status"
    )
    result = await provider.place_call(
        to_number=to_number or "",
        from_number=from_number or "",
        answer_url=answer_url,
        status_callback_url=status_callback_url,
        recording_enabled=call.recording_enabled,
    )
    call.provider_call_id = result.provider_call_id
    call.provider = provider.name()
    # Ring-window warm-up (best-effort, never blocks the dial): synthesise the
    # personalised greeting while the phone rings so it plays instantly when
    # the customer answers instead of after a cold ~4s TTS synthesis.
    try:
        import asyncio as _asyncio

        from app.services.live_agent import prewarm_greeting_for_lead

        _asyncio.get_running_loop().create_task(
            prewarm_greeting_for_lead(lead.name if lead else None)
        )
    except Exception:
        pass
    # Record the dialled number on the call (was caller-id before: display bug).
    if not call.phone_number:
        call.phone_number = to_number
    log.info(
        "outbound call placed",
        extra={"ctx": {"call_id": str(call.id), "provider": provider.name(), "provider_call_id": result.provider_call_id}},
    )
    return result


async def transition_call(
    db: AsyncSession,
    call: Call,
    to_status: CallStatus | str,
    *,
    event_data: dict | None = None,
    set_fields: CallUpdate | None = None,
) -> Call:
    """Transition a call to a new status, validating against the state machine."""
    to_value = to_status.value if isinstance(to_status, CallStatus) else to_status

    if not allowed(call.status, to_value) and call.status != to_value:
        raise IllegalTransitionError(f"illegal transition {call.status} -> {to_value}")

    now = datetime.now(timezone.utc)
    call.status = to_value

    # Record timing fields based on the new status.
    if to_value == CallStatus.INITIATED.value:
        call.initiated_at = now
        try:
            await place_outbound_call(db, call)
        except Exception as exc:
            # The dial itself failed (Plivo 4xx/5xx, auth, network): record
            # FAILED with the reason instead of leaving the call QUEUED-
            # looking forever ("stuck"). Committed here (not just flushed)
            # so the FastAPI error path's rollback cannot wipe it; the
            # error still propagates so the operator sees it immediately.
            call.status = CallStatus.FAILED.value
            call.ended_at = now
            call.failure_reason = str(exc)[:255]
            await event_crud.add_event(
                db, call_id=call.id, event_type=CallStatus.FAILED.value,
                data={"source": "dial", "error": str(exc)[:500]},
            )
            await audit.record_audit(
                db, action="call.failed", resource_type="call",
                resource_id=call.id, details={"error": str(exc)[:500]},
            )
            await db.commit()
            await db.refresh(call)
            raise
    elif to_value == CallStatus.ANSWERED.value or to_value == CallStatus.IN_PROGRESS.value:
        if call.answered_at is None:
            call.answered_at = now
    elif to_value == CallStatus.COMPLETED.value:
        call.ended_at = now
        if call.answered_at:
            call.duration_seconds = round((now - call.answered_at).total_seconds())
    elif to_value in (CallStatus.FAILED.value, CallStatus.NO_ANSWER.value, CallStatus.NO_RESPONSE.value, CallStatus.BUSY.value, CallStatus.CANCELLED.value):
        call.ended_at = now

    # Apply any supplied field updates.
    if set_fields is not None:
        for field, value in set_fields.model_dump(exclude_unset=True).items():
            if value is not None:
                setattr(call, field, value)

    await event_crud.add_event(
        db, call_id=call.id, event_type=to_value, data=event_data
    )
    await audit.record_audit(
        db, action=f"call.{to_value}", resource_type="call", resource_id=call.id
    )
    await db.flush()
    await db.refresh(call)

    # Apply lead status / follow-up side-effects for terminal outcomes.
    lead = await crud_get_lead(db, call.lead_id) if call.lead_id else None
    if to_value == CallStatus.DO_NOT_CALL.value and lead is not None:
        from app.services.lead import set_do_not_call as mark_dnc

        await mark_dnc(db, lead, reason="caller requested during call")

    elif to_value == CallStatus.COMPLETED.value and lead is not None:
        lead.status = LeadStatus.CONTACTED
        await db.flush()

    elif to_value == CallStatus.CALLBACK_REQUESTED.value and lead is not None:
        # Customer asked to be called back: persist the lead status + a follow-up.
        from app.services import follow_up as follow_up_service

        lead.status = LeadStatus.CALLBACK_REQUESTED
        await follow_up_service.schedule_callback(
            db,
            lead_id=lead.id,
            scheduled_for=(event_data or {}).get("scheduled_for"),
            notes=(event_data or {}).get("notes"),
        )
        await db.flush()

    elif to_value in (CallStatus.NO_ANSWER.value, CallStatus.NO_RESPONSE.value, CallStatus.BUSY.value) and lead is not None:
        # Call not reached the customer: schedule an automatic retry (controlled
        # by AUTO_RETRY_ENABLED, off by default to avoid accidental call loops).
        from app.services import follow_up as follow_up_service

        if settings.AUTO_RETRY_ENABLED:
            await follow_up_service.schedule_retry(
                db,
                lead_id=lead.id,
                reason="retry" if to_value != CallStatus.BUSY.value else "busy",
                notes=(event_data or {}).get("notes"),
            )
        else:
            log.info(
                "auto-retry disabled; skipping follow-up schedule",
                extra={"ctx": {"call_id": str(call.id), "lead_id": str(lead.id)}},
            )

    return call
