"""Call model - a single telephony call to a lead."""

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import CallDirection, CallStatus

if TYPE_CHECKING:
    from app.models.call_event import CallEvent
    from app.models.conversation_message import ConversationMessage
    from app.models.conversation_session import ConversationSession
    from app.models.cost_record import CostRecord
    from app.models.lead import Lead


class Call(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A telephony call made to (or received from) a lead."""

    __tablename__ = "calls"

    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=True
    )
    provider: Mapped[str] = mapped_column(String(50), default="plivo", nullable=False)
    direction: Mapped[CallDirection] = mapped_column(
        String(20), default=CallDirection.OUTBOUND.value, nullable=False
    )
    status: Mapped[CallStatus] = mapped_column(
        String(30), default=CallStatus.QUEUED.value, nullable=False, index=True
    )
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Provider identifiers
    provider_call_id: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)
    provider_session_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Timing
    queued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    initiated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(nullable=True)

    # Outcome / metadata
    failure_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    recording_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recording_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Retry metadata
    attempt: Mapped[int] = mapped_column(default=1, nullable=False)

    # Relationships
    lead: Mapped[Optional["Lead"]] = relationship(back_populates="calls")
    events: Mapped[List["CallEvent"]] = relationship(
        back_populates="call", cascade="all, delete-orphan"
    )
    conversation_messages: Mapped[List["ConversationMessage"]] = relationship(
        back_populates="call", cascade="all, delete-orphan"
    )
    conversation_sessions: Mapped[List["ConversationSession"]] = relationship(
        back_populates="call", cascade="all, delete-orphan"
    )
    cost_records: Mapped[List["CostRecord"]] = relationship(
        back_populates="call", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Call id={self.id} status={self.status.value}>"
