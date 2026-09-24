"""FollowUp model - scheduled callback / retry for a lead."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import FollowUpStatus

if TYPE_CHECKING:
    from app.models.lead import Lead


class FollowUp(TimestampMixin, UUIDPrimaryKeyMixin, Base):
    """A follow-up action (callback or auto-retry) scheduled for a lead."""

    __tablename__ = "follow_ups"

    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[FollowUpStatus] = mapped_column(
        String(20), default=FollowUpStatus.PENDING.value, nullable=False, index=True
    )
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    lead: Mapped["Lead"] = relationship(back_populates="follow_ups")

    def __repr__(self) -> str:
        return f"<FollowUp id={self.id} status={self.status.value}>"
