"""CostRecord model - estimated cost for a call, broken down by component."""

from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import CostComponent

if TYPE_CHECKING:
    from app.models.call import Call


class CostRecord(TimestampMixin, UUIDPrimaryKeyMixin, Base):
    """Estimated cost for a single call, broken down by component."""

    __tablename__ = "cost_records"

    call_id: Mapped[str] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), index=True, nullable=False
    )
    component: Mapped[CostComponent] = mapped_column(
        String(20), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(10), default="INR", nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)
    # e.g. minutes used, chars used, tokens used - for auditability.
    quantity: Mapped[Optional[float]] = mapped_column(Numeric(12, 4), nullable=True)
    unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    rate: Mapped[Optional[float]] = mapped_column(Numeric(12, 6), nullable=True)

    # Relationships
    call: Mapped["Call"] = relationship(back_populates="cost_records")

    def __repr__(self) -> str:
        return f"<CostRecord call_id={self.call_id} {self.component}={self.amount}>"
