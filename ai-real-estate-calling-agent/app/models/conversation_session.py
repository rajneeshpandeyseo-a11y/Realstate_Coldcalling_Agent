"""ConversationSession model - one logical conversation (possibly multiple calls)."""

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.call import Call
    from app.models.conversation_message import ConversationMessage


class ConversationSession(TimestampMixin, UUIDPrimaryKeyMixin, Base):
    """A dialog session; may span one or more call attempts."""

    __tablename__ = "conversation_sessions"

    call_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("calls.id", ondelete="SET NULL"), index=True, nullable=True
    )
    state: Mapped[str] = mapped_column(String(50), default="START", nullable=False)
    language: Mapped[str] = mapped_column(String(20), default="hi-Latn", nullable=False)

    # Snapshot of collected requirement so a follow-up call can resume seamlessly.
    collected_data: Mapped[Optional[str]] = mapped_column(nullable=True)

    # Relationships
    call: Mapped[Optional["Call"]] = relationship(back_populates="conversation_sessions")
    messages: Mapped[List["ConversationMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ConversationSession id={self.id} state={self.state}>"
