"""ProviderConfig model - stored config for telephony/STT/TTS/LLM providers."""

from typing import Optional

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ProviderType


class ProviderConfig(TimestampMixin, UUIDPrimaryKeyMixin, Base):
    """Persisted provider configuration (safe, non-secret portions).

    NOTE: Sensitive credentials (API keys/tokens) are intentionally NOT stored
    here by default - they live only in environment variables / secret manager.
    """

    __tablename__ = "provider_configs"

    provider_type: Mapped[ProviderType] = mapped_column(
        String(30), nullable=False
    )
    provider_name: Mapped[str] = mapped_column(String(50), nullable=False)
    # Runtime-relevant non-secret config (model/voice/region), as JSON.
    config_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<ProviderConfig type={self.provider_type} name={self.provider_name}>"
