"""AgentConfig model - voice/behavior config for AI agents."""

from typing import Optional

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class AgentConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Stores configuration for a voice agent persona / calling script."""

    __tablename__ = "agent_configs"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    greeting: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    closing: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # JSON blob for flexible config: voice style, prompts, state map, etc.
    config_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<AgentConfig id={self.id} name={self.name}>"
