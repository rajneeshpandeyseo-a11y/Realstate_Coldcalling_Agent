"""Pydantic schemas for the Lead resource."""

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import LeadScore, LeadStatus
from app.schemas.common import ORMModel


class LeadCreate(BaseModel):
    """Payload to create a new lead."""

    name: Optional[str] = Field(default=None, max_length=255)
    phone: str = Field(min_length=7, max_length=20)
    source: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = None
    city: Optional[str] = Field(default=None, max_length=100)
    campaign_id: Optional[str] = Field(default=None, max_length=50)
    status: LeadStatus = LeadStatus.NEW


class LeadUpdate(BaseModel):
    """Payload to update a lead. All fields optional (partial update)."""

    name: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, min_length=7, max_length=20)
    source: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = None
    city: Optional[str] = Field(default=None, max_length=100)
    campaign_id: Optional[str] = Field(default=None, max_length=50)
    status: Optional[LeadStatus] = None
    score: Optional[LeadScore] = None


class LeadOut(ORMModel):
    """Lead response schema."""

    id: Any
    name: Optional[str] = None
    phone: str
    status: LeadStatus
    score: Optional[LeadScore] = None
    source: Optional[str] = None
    notes: Optional[str] = None
    city: Optional[str] = None
    campaign_id: Optional[str] = None
    do_not_call: bool
    opt_out_reason: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
