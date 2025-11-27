from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field, validator


class StrategyBase(BaseModel):
    """Base schema for Strategy, containing all common fields."""

    name: str = Field(
        ..., min_length=3, max_length=100, description="Name of the strategy"
    )
    version: str = Field(
        ..., regex=r"^\d+\.\d+\.\d+$", description="Semantic version (e.g., 1.0.0)"
    )
    description: str | None = Field(
        None, max_length=1000, description="Detailed description of the strategy"
    )
    parameters_schema: dict[str, Any] = Field(
        ..., description="JSON Schema for strategy parameters"
    )
    indicators: list[str] | None = Field(
        None, description="List of required indicators"
    )
    is_active: bool = Field(True, description="Whether the strategy is active")


class StrategyCreate(StrategyBase):
    """Schema for creating a new strategy."""

    @validator("name")
    def name_to_lowercase(cls, v):
        return v.lower().replace(" ", "_")


class StrategyUpdate(BaseModel):
    """Schema for updating an existing strategy."""

    description: str | None = Field(None, max_length=1000)
    parameters_schema: dict[str, Any] | None = None
    indicators: list[str] | None = None
    is_active: bool | None = None


class StrategyInDBBase(StrategyBase):
    """Base schema for Strategy in database."""

    id: str
    created_at: datetime
    updated_at: datetime | None = None

    class Config:
        orm_mode = True


class Strategy(StrategyInDBBase):
    """Schema for returning a strategy (includes all fields)."""

    pass


class StrategyInDB(StrategyInDBBase):
    """Schema for strategy stored in DB (can include internal fields)."""

    pass
