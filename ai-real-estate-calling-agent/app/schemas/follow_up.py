"""Pydantic schemas for the FollowUp resource (Phase 14)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.enums import FollowUpStatus
from app.schemas.common import ORMModel


class FollowUpCreate(BaseModel):
    """Payload to schedule a follow-up (callback or retry) for a lead."""

    lead_id: uuid.UUID
    reason: Optional[str] = Field(default=None, max_length=100)
    scheduled_for: Optional[datetime] = None
    notes: Optional[str] = None
    max_attempts: int = Field(default=3, ge=1)


class FollowUpUpdate(BaseModel):
    """Partial update for a follow-up."""

    reason: Optional[str] = Field(default=None, max_length=100)
    scheduled_for: Optional[datetime] = None
    notes: Optional[str] = None
    status: Optional[FollowUpStatus] = None
    completed_at: Optional[datetime] = None


class FollowUpOut(ORMModel):
    """FollowUp response schema."""

    id: Any
    lead_id: Any
    status: FollowUpStatus
    scheduled_for: Optional[datetime] = None
    reason: Optional[str] = None
    notes: Optional[str] = None
    attempts: int
    max_attempts: int
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
