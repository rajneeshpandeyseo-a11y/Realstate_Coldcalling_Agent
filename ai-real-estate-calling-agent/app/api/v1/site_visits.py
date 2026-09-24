"""Site-visit endpoints - /api/v1/site-visits (Phase 13)."""

from __future__ import annotations

import math
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin_api_key
from app.crud import site_visit as visit_crud
from app.db.session import get_db
from app.models.enums import SiteVisitStatus
from app.schemas.site_visit import (
    SiteVisitCreate,
    SiteVisitOut,
    SiteVisitUpdate,
)
from app.services import site_visit as visit_service

router = APIRouter(
    prefix="/site-visits",
    tags=["site-visits"],
    dependencies=[Depends(require_admin_api_key)],
)


@router.post("", response_model=SiteVisitOut, status_code=status.HTTP_201_CREATED)
async def create_site_visit(
    payload: SiteVisitCreate,
    db: AsyncSession = Depends(get_db),
) -> "SiteVisitOut":
    """Schedule a site-visit appointment for a lead."""
    try:
        visit = await visit_service.create_site_visit(db, payload)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except visit_service.MultipleActiveVisitsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return SiteVisitOut.model_validate(visit)


@router.get("", response_model=None)
async def list_site_visits(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: SiteVisitStatus | None = Query(None, alias="status"),
    lead_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List site visits with optional filters."""
    rows, total = await visit_crud.list_site_visits(
        db,
        page=page,
        page_size=page_size,
        status=status_filter.value if status_filter else None,
        lead_id=lead_id,
    )
    return {
        "items": [SiteVisitOut.model_validate(r).model_dump(mode="json") for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": math.ceil(total / page_size) if total else 0,
    }


@router.get("/{visit_id}", response_model=SiteVisitOut)
async def get_site_visit(
    visit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> "SiteVisitOut":
    """Fetch a single site visit."""
    visit = await visit_crud.get_site_visit(db, visit_id)
    if visit is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="site visit not found"
        )
    return SiteVisitOut.model_validate(visit)


@router.patch("/{visit_id}", response_model=SiteVisitOut)
async def update_site_visit(
    visit_id: uuid.UUID,
    payload: SiteVisitUpdate,
    db: AsyncSession = Depends(get_db),
) -> "SiteVisitOut":
    """Update a site visit (reschedule, confirm, cancel)."""
    try:
        visit = await visit_service.update_site_visit(db, visit_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return SiteVisitOut.model_validate(visit)
