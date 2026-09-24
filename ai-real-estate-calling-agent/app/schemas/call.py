"""Pydantic schemas for the Call resource."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CallDirection, CallStatus
from app.schemas.common import ORMModel


class CallCreate(BaseModel):
    """Payload to create/schedule an outbound call."""

    lead_id: uuid.UUID
    provider: str = Field(default="plivo", max_length=50)
    direction: CallDirection = CallDirection.OUTBOUND
    phone_number: Optional[str] = Field(default=None, max_length=20)
    recording_enabled: bool = False
    attempt: int = Field(default=1, ge=1)


class CallUpdate(BaseModel):
    """Partial update for a call (used by webhook/state transitions)."""

    status: Optional[CallStatus] = None
    provider_call_id: Optional[str] = Field(default=None, max_length=100)
    provider_session_id: Optional[str] = Field(default=None, max_length=100)
    failure_reason: Optional[str] = Field(default=None, max_length=255)
    recording_url: Optional[str] = None
    transcript: Optional[str] = None
    duration_seconds: Optional[int] = Field(default=None, ge=0)


class CallEventOut(ORMModel):
    """A single call lifecycle event."""

    id: Any
    call_id: Any
    event_type: str
    occurred_at: datetime
    data: Optional[str] = None


class CallOut(ORMModel):
    """Call response schema."""

    id: Any
    lead_id: Any
    provider: str
    direction: CallDirection
    status: CallStatus
    phone_number: Optional[str] = None
    provider_call_id: Optional[str] = None
    provider_session_id: Optional[str] = None
    queued_at: Optional[datetime] = None
    initiated_at: Optional[datetime] = None
    answered_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    failure_reason: Optional[str] = None
    recording_url: Optional[str] = None
    recording_enabled: bool
    attempt: int
    created_at: datetime
    updated_at: Optional[datetime] = None
