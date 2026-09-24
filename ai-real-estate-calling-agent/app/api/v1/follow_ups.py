"""Follow-up endpoints - /api/v1/follow-ups (Phase 14)."""

from __future__ import annotations

import math
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin_api_key
from app.crud import follow_up as follow_up_crud
from app.db.session import get_db
from app.models.enums import FollowUpStatus
from app.schemas.follow_up import (
    FollowUpCreate,
    FollowUpOut,
    FollowUpUpdate,
)
from app.services import follow_up as follow_up_service

router = APIRouter(
    prefix="/follow-ups",
    tags=["follow-ups"],
    dependencies=[Depends(require_admin_api_key)],
)


@router.post("", response_model=FollowUpOut, status_code=status.HTTP_201_CREATED)
async def create_follow_up(
    payload: FollowUpCreate,
    db: AsyncSession = Depends(get_db),
) -> "FollowUpOut":
    """Schedule a follow-up (callback / retry) for a lead."""
    try:
        follow_up = await follow_up_service.schedule(
            db,
            lead_id=payload.lead_id,
            reason=payload.reason or "follow_up",
            scheduled_for=payload.scheduled_for,
            notes=payload.notes,
            max_attempts=payload.max_attempts,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except follow_up_service.DuplicateFollowUpError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return FollowUpOut.model_validate(follow_up)


@router.get("", response_model=None)
async def list_follow_ups(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: FollowUpStatus | None = Query(None, alias="status"),
    lead_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List follow-ups with optional filters."""
    rows, total = await follow_up_crud.list_follow_ups(
        db,
        page=page,
        page_size=page_size,
        status=status_filter.value if status_filter else None,
        lead_id=lead_id,
    )
    return {
        "items": [FollowUpOut.model_validate(r).model_dump(mode="json") for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": math.ceil(total / page_size) if total else 0,
    }


@router.get("/{follow_up_id}", response_model=FollowUpOut)
async def get_follow_up(
    follow_up_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> "FollowUpOut":
    """Fetch a single follow-up."""
    follow_up = await follow_up_crud.get_follow_up(db, follow_up_id)
    if follow_up is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="follow up not found"
        )
    return FollowUpOut.model_validate(follow_up)


@router.patch("/{follow_up_id}", response_model=FollowUpOut)
async def update_follow_up(
    follow_up_id: uuid.UUID,
    payload: FollowUpUpdate,
    db: AsyncSession = Depends(get_db),
) -> "FollowUpOut":
    """Update a follow-up (edit schedule/notes)."""
    follow_up = await follow_up_crud.get_follow_up(db, follow_up_id)
    if follow_up is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="follow up not found"
        )
    follow_up = await follow_up_crud.update_follow_up(db, follow_up, payload)
    return FollowUpOut.model_validate(follow_up)


@router.post("/{follow_up_id}/complete", response_model=FollowUpOut)
async def complete_follow_up(
    follow_up_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> "FollowUpOut":
    """Mark a follow-up as done (the callback/retry was made)."""
    try:
        follow_up = await follow_up_service.complete(db, follow_up_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return FollowUpOut.model_validate(follow_up)


@router.post("/{follow_up_id}/cancel", response_model=FollowUpOut)
async def cancel_follow_up(
    follow_up_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> "FollowUpOut":
    """Cancel a pending follow-up."""
    try:
        follow_up = await follow_up_service.cancel(db, follow_up_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return FollowUpOut.model_validate(follow_up)
