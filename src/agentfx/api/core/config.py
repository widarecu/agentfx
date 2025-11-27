from pydantic_settings import BaseSettings
from typing import List, Optional, Literal
from pydantic import Field, AnyHttpUrl, validator
import json

class Settings(BaseSettings):
    # Project
    PROJECT_NAME: str = Field(
        default="AgentFX API",
        description="Name of the project for documentation and logging"
    )
    
    TESTING: bool = Field(
        default=False,
        env="TESTING",
        description="Whether the application is running in test mode"
    )
    
    API_V1_STR: str = Field(
        default="/api/v1",
        description="API version prefix for all endpoints"
    )
    
    ENVIRONMENT: Literal["development", "staging", "production"] = Field(
        default="development",
        env="ENVIRONMENT",
        description="Current environment (development/staging/production)"
    )
    
    DEBUG: bool = Field(
        default=False,
        env="DEBUG",
        description="Enable debug mode (more verbose logging)"
    )
    
    # Security
    SECRET_KEY: str = Field(
        default="your-secret-key-here",
        env="SECRET_KEY",
        description="Secret key for JWT token generation (change in production)"
    )
    
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=60 * 24 * 8,  # 8 days
        env="ACCESS_TOKEN_EXPIRE_MINUTES",
        description="JWT token expiration time in minutes"
    )
    
    ALGORITHM: str = Field(
        default="HS256",
        env="JWT_ALGORITHM",
        description="Algorithm used for JWT token encoding/decoding"
    )
    
    # Database
    DATABASE_URL: str = Field(
        default="sqlite:///./agentfx.db",
        env="DATABASE_URL",
        description="Database connection URL"
    )
    
    # CORS
    BACKEND_CORS_ORIGINS: List[str] = Field(
        default=["http://localhost:5175", "http://localhost:8000"],
        env="BACKEND_CORS_ORIGINS",
        description="List of allowed CORS origins"
    )
    
    # Logging
    LOG_LEVEL: str = Field(
        default="INFO",
        env="LOG_LEVEL",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
    )
    
    # OANDA API
    OANDA_ENV: Literal["practice", "live"] = Field(
        default="practice",
        env="OANDA_ENV",
        description="OANDA environment (practice/live)"
    )
    
    OANDA_API_KEY: str = Field(
        default="",
        env="OANDA_API_KEY",
        description="OANDA API key for authentication"
    )
    
    OANDA_ACCOUNT_ID: str = Field(
        default="",
        env="OANDA_ACCOUNT_ID",
        description="OANDA account ID for API access"
    )
    
    OANDA_API_HOST: str = Field(
        default="api-fxpractice.oanda.com",
        env="OANDA_API_HOST",
        description="OANDA REST API hostname"
    )
    
    OANDA_STREAMING_HOST: str = Field(
        default="stream-fxpractice.oanda.com",
        env="OANDA_STREAMING_HOST",
        description="OANDA Streaming API hostname"
    )
    
    # Real-time
    WEBSOCKET_ENABLED: bool = Field(
        default=True,
        env="WEBSOCKET_ENABLED",
        description="Enable/disable WebSocket functionality"
    )
    
    WEBSOCKET_PATH: str = Field(
        default="/ws",
        env="WEBSOCKET_PATH",
        description="Base path for WebSocket connections"
    )
    
    # Model Config
    class Config:
        case_sensitive = True
        env_file = ".env"
        
    @validator("BACKEND_CORS_ORIGINS", pre=True)
    def assemble_cors_origins(cls, v):
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)

# Create settings instance
settings = Settings()
