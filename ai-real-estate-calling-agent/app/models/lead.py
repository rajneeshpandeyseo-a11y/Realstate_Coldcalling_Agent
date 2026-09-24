"""Lead model - a real-estate prospect to be called."""

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import LeadScore, LeadStatus

if TYPE_CHECKING:
    from app.models.call import Call
    from app.models.campaign_lead import CampaignLead
    from app.models.follow_up import FollowUp
    from app.models.lead_requirement import LeadRequirement
    from app.models.site_visit import SiteVisit


class Lead(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A real-estate lead."""

    __tablename__ = "leads"

    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    status: Mapped[LeadStatus] = mapped_column(
        default=LeadStatus.NEW, nullable=False, index=True
    )
    # Store enum value as a plain string column (simpler than PG enum type,
    # and keeps migrations portable).
    score: Mapped[Optional[LeadScore]] = mapped_column(String(20), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Opt-out flag - if True, this lead must NEVER be called again.
    do_not_call: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    opt_out_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Contact preference / context
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    campaign_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Relationships
    requirements: Mapped[List["LeadRequirement"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    calls: Mapped[List["Call"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    site_visits: Mapped[List["SiteVisit"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    follow_ups: Mapped[List["FollowUp"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    campaign_links: Mapped[List["CampaignLead"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Lead id={self.id} phone={self.phone} status={self.status.value}>"
