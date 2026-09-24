"""Campaign model - a calling campaign."""

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.campaign_lead import CampaignLead


class Campaign(TimestampMixin, SoftDeleteMixin, UUIDPrimaryKeyMixin, Base):
    """A telemarketing campaign that groups leads."""

    __tablename__ = "campaigns"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Campaign-level configuration (voice, phone number, provider, script)
    voice: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    provider: Mapped[str] = mapped_column(String(50), default="plivo", nullable=False)
    script_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Rate control
    max_calls_per_day: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    call_window_start: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    call_window_end: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Relationships
    leads: Mapped[List["CampaignLead"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Campaign id={self.id} name={self.name}>"
