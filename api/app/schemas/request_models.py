from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app_shared.request_status import (
    FACEBOOK_QUERY_SOURCE,
    FACEBOOK_SOURCE_TYPE,
    RequestStatusCode,
)


class CreateRequestPayload(BaseModel):
    id_query: int = Field(..., ge=1)
    subject: str = Field(..., min_length=1)
    start_date: datetime | None = None
    end_date: datetime | None = None
    status_current: RequestStatusCode
    query_source: int | None = None

    @field_validator("id_query", mode="before")
    @classmethod
    def normalize_id_query(cls, value: Any) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            normalized = value.strip()
            if normalized.isdigit():
                return int(normalized)
        raise ValueError("id_query must be a numeric value")

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("subject must not be blank")
        return normalized

    @field_validator("status_current")
    @classmethod
    def validate_status_current(cls, value: RequestStatusCode) -> RequestStatusCode:
        if value != RequestStatusCode.PENDING:
            raise ValueError("status_current must be 1 when creating a request")
        return value

    @model_validator(mode="after")
    def validate_date_window(self) -> "CreateRequestPayload":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be greater than or equal to start_date")
        return self


class RequestMetadata(BaseModel):
    request_id: UUID
    id_query: int
    user_id: UUID
    ip_request: str
    numero_requests: int
    subject: str
    query_source: int = Field(default=FACEBOOK_QUERY_SOURCE)
    source_type: int = Field(default=FACEBOOK_SOURCE_TYPE)
    status_current: RequestStatusCode
    created_at: datetime
    updated_at: datetime
    start_date: datetime | None = None
    end_date: datetime | None = None


class RequestDataPayload(BaseModel):
    profiles: list[dict[str, Any]] = Field(default_factory=list)
    posts: list[dict[str, Any]] = Field(default_factory=list)
    videos: list[dict[str, Any]] = Field(default_factory=list)
    pages: list[dict[str, Any]] = Field(default_factory=list)
    groups: list[dict[str, Any]] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)


class RequestDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    metadata: RequestMetadata
    data: RequestDataPayload


class CreateRequestFailureResponse(BaseModel):
    detail: str
    metadata: RequestMetadata


class RequestConflictResponse(BaseModel):
    detail: str
    id_query: int
