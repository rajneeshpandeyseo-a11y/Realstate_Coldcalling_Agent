"""ConversationMessage model - speaker + timestamp + text transcript entries."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import UUIDPrimaryKeyMixin
from app.models.enums import ConversationSpeaker

if TYPE_CHECKING:
    from app.models.call import Call
    from app.models.conversation_session import ConversationSession


class ConversationMessage(UUIDPrimaryKeyMixin, Base):
    """A single transcript entry (agent or customer utterance)."""

    __tablename__ = "conversation_messages"

    call_id: Mapped[str] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), index=True, nullable=False
    )
    session_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("conversation_sessions.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    speaker: Mapped[ConversationSpeaker] = mapped_column(
        String(20), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # The conversation state this utterance was captured under.
    state: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # STT/LLM latency in ms for observability/cost.
    latency_ms: Mapped[Optional[int]] = mapped_column(nullable=True)

    # Relationships
    call: Mapped["Call"] = relationship(back_populates="conversation_messages")
    session: Mapped[Optional["ConversationSession"]] = relationship(
        back_populates="messages"
    )

    def __repr__(self) -> str:
        return f"<ConversationMessage speaker={self.speaker.value} text={self.text[:30]}>"
