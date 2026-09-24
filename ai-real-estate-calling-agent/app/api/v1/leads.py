"""Lead CRM endpoints - /api/v1/leads."""

from __future__ import annotations

import math
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin_api_key
from app.crud.lead import get_lead as crud_get_lead
from app.crud.lead import list_leads as crud_list_leads
from app.crud.lead import soft_delete_lead as crud_soft_delete
from app.db.session import get_db
from app.models.enums import LeadStatus
from app.schemas.common import PaginatedResponse
from app.schemas.lead import LeadCreate, LeadOut, LeadUpdate
from app.services import audit, lead as lead_service

router = APIRouter(
    prefix="/leads",
    tags=["leads"],
    dependencies=[Depends(require_admin_api_key)],
)


@router.post("", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
async def create_lead(
    payload: LeadCreate,
    db: AsyncSession = Depends(get_db),
) -> "LeadOut":
    """Create a new lead."""
    try:
        lead = await lead_service.create_lead(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    await audit.record_audit(
        db, action="lead.create", resource_type="lead", resource_id=lead.id
    )
    return LeadOut.model_validate(lead)


@router.get("", response_model=PaginatedResponse)
async def list_leads(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: LeadStatus | None = Query(None, alias="status"),
    search: str | None = Query(None, max_length=100),
    db: AsyncSession = Depends(get_db),
):
    """List active leads with optional filtering and pagination."""
    rows, total = await crud_list_leads(
        db, page=page, page_size=page_size, status=status_filter.value if status_filter else None, search=search
    )
    items = [LeadOut.model_validate(r) for r in rows]
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


@router.get("/{lead_id}", response_model=LeadOut)
async def get_lead(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> "LeadOut":
    """Fetch a single lead."""
    lead = await crud_get_lead(db, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="lead not found")
    return LeadOut.model_validate(lead)


@router.patch("/{lead_id}", response_model=LeadOut)
async def update_lead(
    lead_id: uuid.UUID,
    payload: LeadUpdate,
    db: AsyncSession = Depends(get_db),
) -> "LeadOut":
    """Update a lead (partial update)."""
    lead = await crud_get_lead(db, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="lead not found")
    lead = await lead_service.update_lead(db, lead, payload)
    await audit.record_audit(
        db, action="lead.update", resource_type="lead", resource_id=lead.id
    )
    return LeadOut.model_validate(lead)


@router.post("/{lead_id}/do-not-call", response_model=LeadOut)
async def do_not_call(
    lead_id: uuid.UUID,
    reason: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> "LeadOut":
    """Mark a lead as do-not-call (opt-out)."""
    lead = await crud_get_lead(db, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="lead not found")
    lead = await lead_service.set_do_not_call(db, lead, reason=reason)
    await audit.record_audit(
        db,
        action="lead.do_not_call",
        resource_type="lead",
        resource_id=lead.id,
        details={"reason": reason},
    )
    return LeadOut.model_validate(lead)


@router.delete("/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lead(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a lead."""
    lead = await crud_get_lead(db, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="lead not found")
    await crud_soft_delete(db, lead)
    await audit.record_audit(
        db, action="lead.delete", resource_type="lead", resource_id=lead.id
    )
    return None
