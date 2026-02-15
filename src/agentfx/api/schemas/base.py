from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={datetime: lambda v: v.isoformat() if v else None},
    )


class PaginatedResponse(BaseSchema):
    items: list[Any]
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
    message: str | None = None
