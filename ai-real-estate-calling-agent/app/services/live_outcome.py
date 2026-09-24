"""Live-call outcome persistence: requirements + site visit (PHASE 4/6).

A real (WebSocket-driven) call reaches its final states through Plivo webhook
events rather than the simulated orchestrator. This module finalises a live
call the same way ``CallOrchestrator.run_simulated_call`` does for simulated
calls:

* load the latest conversation session + collected requirement slots,
* persist the requirement snapshot onto the lead (LeadRequirement),
* create a SiteVisit when the customer agreed to one (VISIT_BOOKING/END),
* refresh the lead status (QUALIFIED when a visit is created).

It is invoked from the Plivo status webhook on COMPLETED and from the live
WebSocket when the engine signals a terminal turn, so the CRM is updated
regardless of which side ends the call.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import conversation as conv_crud
from app.crud.lead import get_lead as crud_get_lead
from app.logging_config import get_logger
from app.models.call import Call
from app.models.enums import CallStatus
from app.services import lead_requirement

log = get_logger("app.services.live_outcome")


async def finalize_live_call(
    db: AsyncSession,
    call: Call,
    *,
    session_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
) -> dict:
    """Persist call requirements + site visit onto the lead.

    Best-effort and idempotent: runs last-writer-wins on the requirement
    snapshot and the site-visit duplicate guard refreshes instead of failing.
    Returns a small summary dict for audit logging.
    """
    if call.lead_id is None:
        return {"requirements": False, "site_visit": False}

    slots: dict = {}
    if session_id is not None:
        session = await conv_crud.get_session(db, session_id)
        if session is not None:
            slots = await conv_crud.load_slots(session)
    if not slots:
        sessions = await _sessions_for_call(db, call.id)
        for session in sessions:
            slots = await conv_crud.load_slots(session)
            if slots:
                session_id = session.id
                break

    req = await lead_requirement.upsert_requirements(
        db,
        lead_id=call.lead_id,
        call_id=call.id,
        slots=slots,
        lead_score="qualified" if slots else None,
    )
    result: dict = {"requirements": req is not None, "site_visit": False}

    terminal_state = await _terminal_state_for_call(db, call.id)
    visit_agreed = terminal_state in ("VISIT_BOOKING", "END")

    if visit_agreed and call.status == CallStatus.COMPLETED.value:
        from app.services.site_visit import create_booking_for_call

        # The booking utterance is the last customer message of the call.
        booking_text = await _last_customer_text(db, call.id)
        property_name = (slots or {}).get("property")
        try:
            visit = await create_booking_for_call(
                db,
                lead_id=call.lead_id,
                call_id=call.id,
                booking_text=booking_text,
                slots=slots,
                location=property_name,
            )
            result["site_visit"] = visit is not None
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(
                "live call site-visit persistence failed",
                extra={"ctx": {"call_id": str(call.id), "error": str(exc)}},
            )

    await db.flush()
    return result


async def _sessions_for_call(db: AsyncSession, call_id: uuid.UUID):
    from sqlalchemy import select

    from app.models.conversation_session import ConversationSession

    stmt = (
        select(ConversationSession)
        .where(ConversationSession.call_id == str(call_id))
        .order_by(ConversationSession.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def _terminal_state_for_call(db: AsyncSession, call_id: uuid.UUID) -> str | None:
    messages = await conv_crud.list_messages_for_call(db, call_id)
    agent_msgs = [m for m in messages if m.speaker == "agent" and m.state]
    if not agent_msgs:
        return None
    return agent_msgs[-1].state


async def _last_customer_text(db: AsyncSession, call_id: uuid.UUID) -> str | None:
    messages = await conv_crud.list_messages_for_call(db, call_id)
    customer_msgs = [m.text for m in messages if m.speaker == "customer" and (m.text or "").strip()]
    return customer_msgs[-1] if customer_msgs else None