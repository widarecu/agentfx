from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, validator
from ..models.strategy import Strategy as StrategyModel

class StrategyBase(BaseModel):
    """Base schema for Strategy, containing all common fields."""
    name: str = Field(..., min_length=3, max_length=100, description="Name of the strategy")
    version: str = Field(..., regex=r'^\d+\.\d+\.\d+$', description="Semantic version (e.g., 1.0.0)")
    description: Optional[str] = Field(None, max_length=1000, description="Detailed description of the strategy")
    parameters_schema: Dict[str, Any] = Field(..., description="JSON Schema for strategy parameters")
    indicators: Optional[List[str]] = Field(None, description="List of required indicators")
    is_active: bool = Field(True, description="Whether the strategy is active")

class StrategyCreate(StrategyBase):
    """Schema for creating a new strategy."""
    @validator('name')
    def name_to_lowercase(cls, v):
        return v.lower().replace(' ', '_')

class StrategyUpdate(BaseModel):
    """Schema for updating an existing strategy."""
    description: Optional[str] = Field(None, max_length=1000)
    parameters_schema: Optional[Dict[str, Any]] = None
    indicators: Optional[List[str]] = None
    is_active: Optional[bool] = None

class StrategyInDBBase(StrategyBase):
    """Base schema for Strategy in database."""
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        orm_mode = True

class Strategy(StrategyInDBBase):
    """Schema for returning a strategy (includes all fields)."""
    pass

class StrategyInDB(StrategyInDBBase):
    """Schema for strategy stored in DB (can include internal fields)."""
    pass
