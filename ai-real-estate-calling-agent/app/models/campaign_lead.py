"""CampaignLead - join table linking campaigns to leads."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.campaign import Campaign
    from app.models.lead import Lead


class CampaignLead(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Links a lead to a campaign with per-lead-per-campaign status tracking."""

    __tablename__ = "campaign_leads"

    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    campaign: Mapped["Campaign"] = relationship(back_populates="leads")
    lead: Mapped["Lead"] = relationship(back_populates="campaign_links")

    def __repr__(self) -> str:
        return f"<CampaignLead campaign={self.campaign_id} lead={self.lead_id}>"
