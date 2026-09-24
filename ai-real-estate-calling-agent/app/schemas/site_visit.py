"""Pydantic schemas for the SiteVisit resource (Phase 13)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import SiteVisitStatus
from app.schemas.common import ORMModel


class SiteVisitCreate(BaseModel):
    """Payload to schedule a site-visit appointment for a lead."""

    lead_id: uuid.UUID
    call_id: Optional[uuid.UUID] = None
    preferred_date: Optional[date] = None
    preferred_time: Optional[str] = Field(default=None, max_length=20)
    alternate_date: Optional[date] = None
    alternate_time: Optional[str] = Field(default=None, max_length=20)
    location: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = None


class SiteVisitUpdate(BaseModel):
    """Partial update for a site visit (status, slots, confirmation)."""

    status: Optional[SiteVisitStatus] = None
    preferred_date: Optional[date] = None
    preferred_time: Optional[str] = Field(default=None, max_length=20)
    alternate_date: Optional[date] = None
    alternate_time: Optional[str] = Field(default=None, max_length=20)
    location: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = None
    confirmed_by_customer: Optional[bool] = None


class SiteVisitOut(ORMModel):
    """SiteVisit response schema."""

    id: Any
    lead_id: Any
    call_id: Optional[Any] = None
    preferred_date: Optional[date] = None
    preferred_time: Optional[str] = None
    alternate_date: Optional[date] = None
    alternate_time: Optional[str] = None
    status: SiteVisitStatus
    location: Optional[str] = None
    notes: Optional[str] = None
    confirmed_by_customer: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
