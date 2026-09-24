"""Shared Pydantic schema helpers."""

from datetime import datetime
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    """Base for schemas that serialize ORM objects."""

    model_config = ConfigDict(from_attributes=True)


class PaginatedResponse(ORMModel, Generic[T]):
    """Generic paginated list response."""

    items: List[T]
    total: int
    page: int
    page_size: int
    pages: int


class AuditInfo(ORMModel):
    """created/updated timestamps exposed on resources."""

    created_at: datetime
    updated_at: Optional[datetime] = None
