from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class BaseSchema(BaseModel):
    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }

class PaginatedResponse(BaseSchema):
    items: List[Any]
    total: int
    page: int
    size: int
    total_pages: int

class ErrorResponse(BaseSchema):
    detail: str
    code: str
    status_code: int

class SuccessResponse(BaseSchema):
    success: bool = True
    message: Optional[str] = None
