"""SiteVisit model - a scheduled site-visit appointment."""

from datetime import date
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import SiteVisitStatus

if TYPE_CHECKING:
    from app.models.lead import Lead


class SiteVisit(TimestampMixin, UUIDPrimaryKeyMixin, Base):
    """A site-visit appointment scheduled during a call."""

    __tablename__ = "site_visits"

    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    call_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("calls.id", ondelete="SET NULL"), nullable=True
    )

    preferred_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    preferred_time: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    alternate_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    alternate_time: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[SiteVisitStatus] = mapped_column(
        String(20), default=SiteVisitStatus.REQUESTED.value, nullable=False
    )
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confirmed_by_customer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    lead: Mapped["Lead"] = relationship(back_populates="site_visits")

    def __repr__(self) -> str:
        return (
            f"<SiteVisit id={self.id} lead_id={self.lead_id} "
            f"date={self.preferred_date} time={self.preferred_time}>"
        )
