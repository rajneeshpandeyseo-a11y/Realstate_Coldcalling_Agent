"""CallEvent model - immutable lifecycle events for a call."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.call import Call


class CallEvent(UUIDPrimaryKeyMixin, Base):
    """A single event in the call lifecycle (initiated, answered, hungup, etc.)."""

    __tablename__ = "call_events"

    call_id: Mapped[str] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    call: Mapped["Call"] = relationship(back_populates="events")

    def __repr__(self) -> str:
        return f"<CallEvent call_id={self.call_id} type={self.event_type}>"
