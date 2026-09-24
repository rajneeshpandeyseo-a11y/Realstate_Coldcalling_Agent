"""Pydantic schemas for Call cost records (Phase 15)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from app.models.enums import CostComponent
from app.schemas.common import ORMModel


class CostRecordCreate(ORMModel):
    """Payload to persist one per-component cost line for a call."""

    call_id: Any
    component: CostComponent
    currency: str = "INR"
    amount: float = 0.0
    quantity: Optional[float] = None
    unit: Optional[str] = None
    rate: Optional[float] = None


class CostRecordOut(ORMModel):
    """CostRecord response schema."""

    id: Any
    call_id: Any
    component: CostComponent
    currency: str
    amount: float
    quantity: Optional[float] = None
    unit: Optional[str] = None
    rate: Optional[float] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class CallCostSummary(ORMModel):
    """Aggregated cost summary for a single call from its cost records."""

    call_id: Any
    currency: str = "INR"
    components: dict[str, float]
    total: float
