from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UrlTaskCreate(BaseModel):
    url: str
    output_name: str = "video"
    output_subdir: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    ignore_certificate_errors: bool = False
    force: bool = False


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    source_type: str
    output_name: str
    output_subdir: str
    output_path: str | None
    ignore_certificate_errors: bool
    progress: float
    downloaded_bytes: int
    total_bytes: int | None
    speed: float | None
    eta: int | None
    attempt: int
    error_code: str | None
    error_message: str | None
    media_info: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    total: int
    page: int
    page_size: int


class DeleteResponse(BaseModel):
    deleted: bool
    output_deleted: bool


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class HeadersJson(BaseModel):
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("headers")
    @classmethod
    def limit_headers(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 8:
            raise ValueError("Too many headers")
        return value
