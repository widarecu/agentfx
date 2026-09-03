import os
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_env_file() -> str:
    """
    Determine which environment file to load based on:
    1. ENVIRONMENT environment variable
    2. ENV_FILE environment variable (fallback for tests)
    3. Default to .env.development
    """
    environment = os.getenv("ENVIRONMENT", "").lower()

    env_mapping = {
        "development": ".env.development",
        "dev": ".env.development",
        "test": ".env.test",
        "testing": ".env.test",
        "uat": ".env.uat",
        "staging": ".env.staging",
        "stage": ".env.staging",
        "production": ".env.production",
        "prod": ".env.production",
    }

    if environment in env_mapping:
        return env_mapping[environment]

    # Fallback to ENV_FILE for backward compatibility
    env_file = os.getenv("ENV_FILE")
    if env_file:
        return env_file

    # Default to development environment
    return ".env.development"


class Settings(BaseSettings):
    # Project
    PROJECT_NAME: str = Field(
        default="AgentFX API",
        description="Name of the project for documentation and logging",
    )

    TESTING: bool = Field(
        default=False,
        env="TESTING",
        description="Whether the application is running in test mode",
    )

    API_V1_STR: str = Field(
        default="/api/v1", description="API version prefix for all endpoints"
    )

    ENVIRONMENT: Literal["development", "staging", "production", "test", "uat"] = Field(
        default="development",
        env="ENVIRONMENT",
        description="Current environment (development/staging/production/test/uat)",
    )

    DEBUG: bool = Field(
        default=False,
        env="DEBUG",
        description="Enable debug mode (more verbose logging)",
    )

    # Security
    SECRET_KEY: str = Field(
        default="your-secret-key-here",
        env="SECRET_KEY",
        description="Secret key for JWT token generation (MUST be changed in production)",
    )

    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=60,  # 1 hour - use refresh tokens for longer sessions
        env="ACCESS_TOKEN_EXPIRE_MINUTES",
        description="JWT token expiration time in minutes",
    )

    @field_validator("DEBUG", "TESTING", mode="before")
    @classmethod
    def parse_boolean(cls, v: str | bool | None) -> bool:
        """
        Parse boolean values from environment variables.
        Handles empty strings, 'true'/'false', '1'/'0', etc.
        """
        if v is None or v == "":
            return False
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes", "on")
        return bool(v)

    @field_validator("SECRET_KEY", mode="after")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        """
        Validate that SECRET_KEY is not the default value in production/staging.
        This prevents accidental deployment with an insecure secret key.
        """
        import os

        environment = os.getenv("ENVIRONMENT", "development").lower()
        if v == "your-secret-key-here" and environment in ("production", "staging"):
            raise ValueError(
                "SECRET_KEY must be explicitly set in production/staging environments. "
                'Generate a secure key with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        return v

    ALGORITHM: str = Field(
        default="HS256",
        env="JWT_ALGORITHM",
        description="Algorithm used for JWT token encoding/decoding",
    )

    # Fernet key for at-rest encryption of user-owned secrets
    # (US-019: OANDA API keys stored per user). Must be a
    # 32-byte-url-safe-base64 string — generate with:
    #     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Empty in local/dev by default so tests can inject a fresh key;
    # a `field_validator` below refuses empty in production/staging/UAT.
    FERNET_KEY: str = Field(
        default="",
        env="FERNET_KEY",
        description=(
            "Fernet key (base64) for encrypting per-user OANDA credentials at rest. "
            "REQUIRED in production/staging/UAT — generate a fresh one per environment "
            "and store in the secrets vault. NEVER commit."
        ),
    )

    @field_validator("FERNET_KEY", mode="after")
    @classmethod
    def validate_fernet_key(cls, v: str) -> str:
        """
        Fail fast at startup if `FERNET_KEY` isn't set in a persistent
        environment. Local dev / test can leave it blank — the secrets
        module will lazily generate a per-process ephemeral key for
        those cases, which is safe because dev/test databases hold no
        real user secrets.
        """
        import os

        environment = os.getenv("ENVIRONMENT", "development").lower()
        if not v and environment in ("production", "staging", "uat"):
            raise ValueError(
                "FERNET_KEY must be explicitly set in production/staging/UAT. "
                "Generate one with: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        return v

    # Database
    DATABASE_URL: str = Field(
        default="postgresql://agentfx:agentfx@localhost:5432/agentfx_dev",
        env="DATABASE_URL",
        description="Database connection URL",
    )

    # Test Database (for integration tests)
    # NOTE: Default port is 5432 to match local PostgreSQL installations
    # Docker test database uses port 5433 - set TEST_DATABASE_URL env var if using Docker
    TEST_DATABASE_URL: str = Field(
        default="postgresql://test_user:test_password@localhost:5432/agentfx_test",
        env="TEST_DATABASE_URL",
        description="Test database connection URL for integration tests",
    )

    @field_validator("DATABASE_URL", mode="before")
    def assemble_db_connection(cls, v: str, values: dict[str, Any]) -> str:
        """
        Automatically use test database when in testing mode.
        This ensures consistent database usage across all application components.
        """
        if isinstance(values, dict) and values.get("TESTING", False):
            test_url = values.get("TEST_DATABASE_URL")
            if test_url:
                return test_url
        return v

    TEST_DATABASE_HOST: str = Field(
        default="localhost",
        env="TEST_DATABASE_HOST",
        description="Test database host",
    )

    TEST_DATABASE_PORT: int = Field(
        default=5432,
        env="TEST_DATABASE_PORT",
        description="Test database port (default 5432 for local PostgreSQL, 5433 for Docker)",
    )

    TEST_DATABASE_NAME: str = Field(
        default="agentfx_test",
        env="TEST_DATABASE_NAME",
        description="Test database name",
    )

    TEST_DATABASE_USER: str = Field(
        default="test_user",
        env="TEST_DATABASE_USER",
        description="Test database username",
    )

    TEST_DATABASE_PASSWORD: str = Field(
        default="test_password",
        env="TEST_DATABASE_PASSWORD",
        description="Test database password",
    )

    # CORS
    BACKEND_CORS_ORIGINS: str = Field(
        default="http://localhost:5173,http://localhost:8000",
        description="List of allowed CORS origins (comma-separated)",
    )

    # Model Configuration - env_file is determined at import time based on ENVIRONMENT
    model_config = SettingsConfigDict(
        env_file=get_env_file(), env_file_encoding="utf-8", extra="ignore"
    )

    # Logging
    LOG_LEVEL: str = Field(
        default="INFO",
        env="LOG_LEVEL",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    # OANDA API
    OANDA_ENV: Literal["practice", "live"] = Field(
        default="practice",
        env="OANDA_ENV",
        description="OANDA environment (practice/live)",
    )

    OANDA_API_KEY: str = Field(
        default="", env="OANDA_API_KEY", description="OANDA API key for authentication"
    )

    OANDA_ACCOUNT_ID: str = Field(
        default="",
        env="OANDA_ACCOUNT_ID",
        description="OANDA account ID for API access",
    )

    OANDA_API_HOST: str = Field(
        default="api-fxpractice.oanda.com",
        env="OANDA_API_HOST",
        description="OANDA REST API hostname",
    )

    OANDA_STREAMING_HOST: str = Field(
        default="stream-fxpractice.oanda.com",
        env="OANDA_STREAMING_HOST",
        description="OANDA Streaming API hostname",
    )

    # Candle Ingestion Worker
    INGESTION_ENABLED: bool = Field(
        default=True,
        env="INGESTION_ENABLED",
        description="Enable/disable background candle ingestion worker",
    )

    INGESTION_POLL_INTERVAL: int = Field(
        default=60,
        env="INGESTION_POLL_INTERVAL",
        description="Interval in seconds between candle ingestion cycles",
    )

    INGESTION_ALIGNMENT_BUFFER_SECONDS: int = Field(
        default=3,
        env="INGESTION_ALIGNMENT_BUFFER_SECONDS",
        description=(
            "Seconds after each minute boundary to wake the ingestion "
            "loop. OANDA typically has closed bars available within "
            "~1s of bar close; 3s gives comfortable margin without "
            "wasted latency. Bump if OANDA's post-close latency ever "
            "grows. Must be < SIGNAL_TICK_OFFSET_SECONDS or the paper "
            "runner will race the ingester."
        ),
    )

    SIGNAL_TICK_OFFSET_SECONDS: int = Field(
        default=10,
        env="SIGNAL_TICK_OFFSET_SECONDS",
        description=(
            "Seconds after each granularity boundary to fire paper-run "
            "signal ticks. Must be > INGESTION_ALIGNMENT_BUFFER_SECONDS "
            "+ typical ingestion cycle duration (observed 2-6s), so the "
            "just-closed bar is guaranteed to be in the DB when the "
            "runner evaluates. Default 10s: ingester finishes by ~:09, "
            "runner ticks at :10, order placement lands ~:10-:11."
        ),
    )

    BACKFILL_DAYS: int = Field(
        default=7,
        env="BACKFILL_DAYS",
        description="Days of historical data to backfill on startup (default 7, set to 1825 for 5 years)",
    )

    CANDLE_INSTRUMENTS: str = Field(
        default="EUR_USD,GBP_USD,USD_JPY,AUD_USD,USD_CHF,USD_CAD",
        env="CANDLE_INSTRUMENTS",
        description="Comma-separated list of instruments to ingest",
    )

    CANDLE_GRANULARITIES: str = Field(
        default="M1,M5,M10,M30,H1,H4,H12,D,W,M",
        env="CANDLE_GRANULARITIES",
        description="Comma-separated list of granularities to ingest",
    )

    CANDLE_HEALTH_CHECK_INTERVAL: int = Field(
        default=3600,
        env="CANDLE_HEALTH_CHECK_INTERVAL",
        description="Seconds between gap detection/healing runs (0 to disable)",
    )
    GAP_REVERIFY_DAYS: int = Field(
        default=30,
        env="GAP_REVERIFY_DAYS",
        description="Days before re-checking a previously empty gap in case OANDA backfills",
    )

    MAX_GAPS_PER_HEALTH_CHECK: int = Field(
        default=500,
        env="MAX_GAPS_PER_HEALTH_CHECK",
        description="Maximum gaps to process per instrument/granularity per health check cycle",
    )

    # Notifications — email (Wilson 2026-08-31)
    # Replaced the earlier Slack webhook path — Slack push notifications
    # weren't reliably reaching Wilson's phone. Email via SMTP is the
    # interim channel until a proper mobile app ships. SMTP config is
    # deployment-set (Railway env vars); empty SMTP_HOST short-circuits
    # every call site so tests + unconfigured envs never leak.
    NOTIFICATIONS_ENABLED: bool = Field(
        default=True,
        env="NOTIFICATIONS_ENABLED",
        description=(
            "Master on/off for outbound notifications. Set to False in the "
            "test environment (conftest overrides this) so unit tests can't "
            "leak real emails. In production, keep True and control "
            "delivery via SMTP_HOST presence."
        ),
    )
    SMTP_HOST: str = Field(
        default="",
        env="SMTP_HOST",
        description=(
            "SMTP server hostname (e.g. smtp.sendgrid.net, smtp.gmail.com, "
            "smtp-mail.outlook.com). Empty = email notifications disabled."
        ),
    )
    SMTP_PORT: int = Field(
        default=587,
        env="SMTP_PORT",
        description="SMTP submission port. Default 587 (STARTTLS).",
    )
    SMTP_USER: str = Field(
        default="",
        env="SMTP_USER",
        description=(
            "SMTP auth username. For SendGrid this is literally 'apikey'; "
            "for Gmail/Outlook it's the sender address."
        ),
    )
    SMTP_PASSWORD: str = Field(
        default="",
        env="SMTP_PASSWORD",
        description=("SMTP auth password / API key / app password. Never logged."),
    )
    EMAIL_FROM: str = Field(
        default="",
        env="EMAIL_FROM",
        description=(
            "Envelope 'From' address for outbound alerts. Must be authorised "
            "by the SMTP provider (e.g. a SendGrid-verified sender)."
        ),
    )
    EMAIL_TO: str = Field(
        default="",
        env="EMAIL_TO",
        description=(
            "Recipient address(es) for paper-run alerts. Comma-separated "
            "for multiple. Empty = no recipient, notifications short-circuit."
        ),
    )
    # Legacy Slack config — kept for one release so old deployments don't
    # break on rollback. New code MUST NOT read it. Delete after v0.7.x.
    SLACK_WEBHOOK_URL: str = Field(
        default="",
        env="SLACK_WEBHOOK_URL",
        description=(
            "DEPRECATED: replaced by SMTP_* + EMAIL_* email notifications. "
            "Kept temporarily so rollbacks don't fail-fast on missing var."
        ),
    )

    @property
    def instruments_list(self) -> list[str]:
        """Parse CANDLE_INSTRUMENTS into a list."""
        return [i.strip() for i in self.CANDLE_INSTRUMENTS.split(",") if i.strip()]

    @property
    def granularities_list(self) -> list[str]:
        """Parse CANDLE_GRANULARITIES into a list."""
        return [g.strip() for g in self.CANDLE_GRANULARITIES.split(",") if g.strip()]


# Create settings instance
settings = Settings()
