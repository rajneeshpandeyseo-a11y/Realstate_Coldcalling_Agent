"""LeadRequirement model - property requirements collected during a call."""

from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.lead import Lead


class LeadRequirement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Property requirements gathered from the lead (latest snapshot)."""

    __tablename__ = "lead_requirements"

    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    call_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("calls.id", ondelete="SET NULL"), nullable=True
    )

    # Property details
    property_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    bhk: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    budget_min: Mapped[Optional[float]] = mapped_column(nullable=True)
    budget_max: Mapped[Optional[float]] = mapped_column(nullable=True)
    budget_currency: Mapped[str] = mapped_column(String(10), default="INR", nullable=False)
    budget_raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    purpose: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    timeline: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Qualified status captured at requirement level too
    lead_score: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    qualification_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    lead: Mapped["Lead"] = relationship(back_populates="requirements")

    def __repr__(self) -> str:
        return (
            f"<LeadRequirement id={self.id} lead_id={self.lead_id} "
            f"type={self.property_type} bhk={self.bhk}>"
        )
