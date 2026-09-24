"""Business logic for follow-ups: callbacks and automatic retries (Phase 14).

Creates a durable `FollowUp` against a lead whenever a call outcome needs to be
revisited - either the customer explicitly asked to be called back
(`CALLBACK_REQUESTED`) or the call went unanswered / busy and an automatic retry
should be scheduled. Provides the queries a future retry worker uses to find and
re-dial due leads.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import follow_up as follow_up_crud
from app.crud.lead import get_lead as crud_get_lead
from app.logging_config import get_logger
from app.models.enums import FollowUpStatus, LeadStatus
from app.schemas.follow_up import FollowUpCreate, FollowUpUpdate
from app.services import audit

log = get_logger("app.services.follow_up")

# Default delay before an automatic retry after a failed/no-answer call.
DEFAULT_RETRY_HOURS = 4
DEFAULT_MAX_ATTEMPTS = 3


class FollowUpError(Exception):
    """Base error for follow-up operations."""


class DuplicateFollowUpError(FollowUpError):
    """Raised when a lead already has a pending follow-up of the same reason."""


def _status_value(status) -> str:
    return getattr(status, "value", status)


def _is_pending(follow_up) -> bool:
    return _status_value(follow_up.status) == FollowUpStatus.PENDING.value


async def schedule(
    db: AsyncSession,
    *,
    lead_id: uuid.UUID,
    reason: str,
    scheduled_for: datetime | None = None,
    notes: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> "object":
    """Create a follow-up, refusing a second pending one for the same reason."""
    active = await follow_up_crud.get_follow_ups_for_lead(db, lead_id)
    if any(
        _is_pending(f) and (f.reason or "") == reason for f in active
    ):
        raise DuplicateFollowUpError(
            f"lead {lead_id} already has a pending follow-up reason={reason!r}"
        )

    follow_up = await follow_up_crud.create_follow_up(
        db,
        FollowUpCreate(
            lead_id=lead_id,
            reason=reason,
            scheduled_for=scheduled_for,
            notes=notes,
            max_attempts=max_attempts,
        ),
    )
    await audit.record_audit(
        db, action=f"follow_up.create", resource_type="follow_up",
        resource_id=follow_up.id,
        details={
            "lead_id": str(lead_id),
            "reason": reason,
        },
    )
    return follow_up


async def schedule_callback(
    db: AsyncSession,
    *,
    lead_id: uuid.UUID,
    scheduled_for: datetime | None = None,
    notes: str | None = None,
) -> "object":
    """Schedule a callback that the customer explicitly requested."""
    follow_up = await schedule(
        db,
        lead_id=lead_id,
        reason="callback",
        scheduled_for=scheduled_for,
        notes=notes,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
    )
    return follow_up


async def schedule_retry(
    db: AsyncSession,
    *,
    lead_id: uuid.UUID,
    reason: str = "retry",
    delay_hours: int = DEFAULT_RETRY_HOURS,
    notes: str | None = None,
) -> "object":
    """Schedule an automatic retry after a no-answer/busy/failed call."""
    follow_up = await schedule(
        db,
        lead_id=lead_id,
        reason=reason,
        scheduled_for=datetime.now(timezone.utc) + timedelta(hours=delay_hours),
        notes=notes,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
    )
    return follow_up


async def complete(db: AsyncSession, follow_up_id: uuid.UUID) -> "object":
    """Mark a follow-up done (the callback/retry was made)."""
    follow_up = await follow_up_crud.get_follow_up(db, follow_up_id)
    if follow_up is None:
        raise KeyError(f"follow up {follow_up_id} not found")
    follow_up = await follow_up_crud.update_follow_up(
        db,
        follow_up,
        FollowUpUpdate(
            status=FollowUpStatus.DONE,
            completed_at=datetime.now(timezone.utc),
        ),
    )
    await audit.record_audit(
        db, action="follow_up.complete", resource_type="follow_up",
        resource_id=follow_up.id,
    )
    return follow_up


async def cancel(db: AsyncSession, follow_up_id: uuid.UUID) -> "object":
    """Cancel a pending follow-up."""
    follow_up = await follow_up_crud.get_follow_up(db, follow_up_id)
    if follow_up is None:
        raise KeyError(f"follow up {follow_up_id} not found")
    follow_up = await follow_up_crud.update_follow_up(
        db, follow_up, FollowUpUpdate(status=FollowUpStatus.CANCELLED)
    )
    await audit.record_audit(
        db, action="follow_up.cancel", resource_type="follow_up",
        resource_id=follow_up.id,
    )
    return follow_up


async def bump_attempt(
    db: AsyncSession, follow_up_id: uuid.UUID
) -> "object":
    """Increment the retry counter after a dial attempt."""
    follow_up = await follow_up_crud.get_follow_up(db, follow_up_id)
    if follow_up is None:
        raise KeyError(f"follow up {follow_up_id} not found")
    follow_up.attempts = (follow_up.attempts or 0) + 1
    await db.flush()
    await db.refresh(follow_up)
    return follow_up


async def next_due(
    db: AsyncSession, *, now: datetime | None = None, limit: int = 100
) -> list:
    """Follow-ups that are pending, due and under their attempt cap."""
    now = now or datetime.now(timezone.utc)
    return await follow_up_crud.list_due(db, now=now, limit=limit)


async def mark_lead_callback_requested(db: AsyncSession, lead_id: uuid.UUID) -> None:
    """Set the lead status to CALLBACK_REQUESTED (caller owns the commit)."""
    lead = await crud_get_lead(db, lead_id)
    if lead is not None and _status_value(lead.status) != LeadStatus.CALLBACK_REQUESTED.value:
        lead.status = LeadStatus.CALLBACK_REQUESTED
        await db.flush()
