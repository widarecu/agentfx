from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal
from pydantic import Field, field_validator
import os


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

    ENVIRONMENT: Literal["development", "staging", "production", "test"] = Field(
        default="development",
        env="ENVIRONMENT",
        description="Current environment (development/staging/production/test)",
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
    def assemble_db_connection(cls, v: str, values: dict[str, any]) -> str:
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


# Create settings instance
settings = Settings()
