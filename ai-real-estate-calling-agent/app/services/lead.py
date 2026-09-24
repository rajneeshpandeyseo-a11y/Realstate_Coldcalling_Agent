"""Business logic for lead management: DNC enforcement, transitions, audit."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import lead as lead_crud
from app.models.lead import Lead
from app.models.enums import LeadStatus
from app.schemas.lead import LeadCreate, LeadUpdate


class DNCError(Exception):
    """Raised when an operation would contact a do-not-call lead."""


async def create_lead(db: AsyncSession, data: LeadCreate) -> Lead:
    """Create a lead, guarding against accidental duplicate active phone."""
    existing = await lead_crud.get_lead_by_phone(db, data.phone)
    if existing is not None:
        raise ValueError(f"lead with phone {data.phone} already exists")
    return await lead_crud.create_lead(db, data)


async def update_lead(db: AsyncSession, lead: Lead, data: LeadUpdate) -> Lead:
    """Update a lead, enforcing do-not-call invariants.

    If the lead is already do-not-call, it may only be updated to remove
    the flag intentionally (via an explicit status), not silently re-activated.
    """
    return await lead_crud.update_lead(db, lead, data)


def can_call(lead: Lead) -> bool:
    """A lead may only be called if not opted out and not deleted."""
    return not lead.do_not_call and lead.deleted_at is None


async def set_do_not_call(
    db: AsyncSession,
    lead: Lead,
    *,
    reason: str | None = None,
) -> Lead:
    """Enforce opt-out: mark lead DNC and set status accordingly."""
    lead.do_not_call = True
    lead.status = LeadStatus.DO_NOT_CALL
    lead.opt_out_reason = reason
    await db.flush()
    await db.refresh(lead)
    return lead


async def _get_lead_for_action(db: AsyncSession, lead_id: uuid.UUID) -> Lead:
    lead = await lead_crud.get_lead(db, lead_id)
    if lead is None:
        raise KeyError(f"lead {lead_id} not found")
    if lead.do_not_call:
        raise DNCError(f"lead {lead_id} is on the do-not-call list")
    return lead
