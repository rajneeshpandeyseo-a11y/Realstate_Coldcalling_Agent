"""Application configuration.

All runtime configuration is read from environment variables (via `.env`).
Secrets are NEVER hard-coded here or anywhere in the codebase.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- Application ---
    APP_NAME: str = "AI Real Estate Calling Agent"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    SECRET_KEY: str = "change-me"
    PUBLIC_BASE_URL: str = "http://localhost:8000"

    # --- Database ---
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/real_estate_calling"
    )

    # --- Telephony ---
    TELEPHONY_PROVIDER: str = "plivo"  # plivo | mock
    PLIVO_AUTH_ID: str = ""
    PLIVO_AUTH_TOKEN: str = ""
    PLIVO_PHONE_NUMBER: str = ""
    PLIVO_ENDPOINT: str = "https://api.plivo.com/v1/Account"
    PLIVO_COST_PER_MINUTE: float = 0.38

    # --- Speech-to-Text ---
    STT_PROVIDER: str = "sarvam"  # sarvam | mock
    SARVAM_API_KEY: str = ""
    STT_COST_PER_MINUTE: float = 1.5
    # Sarvam STT request tuning (Phase 7). `codemix` best handles Hinglish;
    # `translit` returns Roman script which some rule engines prefer.
    STT_MODEL: str = "saaras:v3"
    STT_MODE: str = "codemix"
    STT_LANGUAGE_CODE: str = "hi-IN"
    STT_ENDPOINT: str = "https://api.sarvam.ai/speech-to-text"

    # --- Text-to-Speech ---
    TTS_PROVIDER: str = "sarvam"  # sarvam | mock
    # Feminine voice to match the scripted persona ("bol rahi hoon").
    # Verified Bulbul v3 Tier-1 female Hindi voice (docs: priya/ishita).
    SARVAM_TTS_VOICE: str = "priya"
    TTS_COST_PER_CHAR: float = 0.003
    # Sarvam Bulbul TTS request tuning (Phase 8)
    TTS_MODEL: str = "bulbul:v3"
    TTS_LANGUAGE_CODE: str = "hi-IN"
    TTS_ENDPOINT: str = "https://api.sarvam.ai/text-to-speech"
    # Conversational pace: 1.0 sounded unnaturally slow on live calls.
    # Sarvam allows 0.5-2.0; 1.2 is natural Hindi telesales speed.
    TTS_PACE: float = 1.2
    TTS_OUTPUT_CODEC: str = "wav"
    TTS_SAMPLE_RATE: str = "24000"

    # --- LLM ---
    # Default is `fixed`: fully deterministic rule engine, NO external LLM call
    # and no cost. Enable a real LLM later by setting LLM_PROVIDER=gemini together
    # with GEMINI_API_KEY. `mock` is a deterministic stand-in used in tests.
    LLM_PROVIDER: str = "fixed"  # fixed | mock | gemini
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"
    LLM_ENDPOINT: str = "https://generativelanguage.googleapis.com/v1beta"
    LLM_COST_PER_1M_INPUT: float = 0.10
    LLM_COST_PER_1M_OUTPUT: float = 0.40

    # --- Conversation / Calls ---
    MOCK_MODE: bool = True
    RECORDING_ENABLED: bool = False
    TRANSCRIPT_STORAGE_ENABLED: bool = True

    # --- Live-call & cost safety (Phase A) ---
    # Defaults are SAFE: no real (paid) calls are placed and no automatic retry
    # loop runs unless explicitly enabled AND mock mode is off.
    #
    # LIVE_CALLS_ENABLED: master switch for placing real outbound Plivo calls.
    #   Must be `true` AND MOCK_MODE=false before any paid call can be placed,
    #   and even then only one explicit call at a time (see the API guard).
    LIVE_CALLS_ENABLED: bool = False
    # AUTO_RETRY_ENABLED: when true, a NO_ANSWER/BUSY call automatically schedules
    #   a follow-up retry. Kept off by default to avoid accidental call loops.
    AUTO_RETRY_ENABLED: bool = False
    # RUN_LIVE_TESTS: opt-in guard for tests that hit real providers. Normal
    #   `pytest` runs must stay cost-free (MOCK_MODE) and ignore these.
    RUN_LIVE_TESTS: bool = False

    # --- Security (Phase 16) ---
    # API key required on admin/management routes (X-API-Key header).
    ADMIN_API_KEY: str = "dev-admin-key"
    # Shared secret for verifying telephony webhooks (X-Webhook-Token header).
    # Leave empty to run webhooks unauthenticated in dev.
    WEBHOOK_TOKEN: str = ""
    # Maximum accepted request body size (bytes) - request hardening.
    MAX_REQUEST_BYTES: int = 1_000_000

    @property
    def is_mock(self) -> bool:
        """True when the system should not place real paid calls."""
        return self.MOCK_MODE or self.ENVIRONMENT == "test"

    @property
    def database_url_for_alembic(self) -> str:
        """Sync URL for Alembic migrations (async driver swapped for sync)."""
        return self.DATABASE_URL.replace("+asyncpg", "")


@lru_cache
def get_settings() -> Settings:
    """Return a cached settings instance."""
    return Settings()


settings = get_settings()
