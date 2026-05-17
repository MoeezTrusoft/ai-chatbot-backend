from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PortfolioActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: UUID | None = None
    thread_id: UUID
    service: str
    genre: str | None = None
    exclude_sample_ids: list[str] = Field(default_factory=list)
    limit: int = 3


class PortfolioActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    requested_genre: str | None = None
    status: str
    message: str
    samples: list[dict[str, Any]] = Field(default_factory=list)
    sample_ids: list[str] = Field(default_factory=list)
    skipped_sample_ids: list[str] = Field(default_factory=list)
    matched_genre: str | None = None
    fallback_used: bool = False
    customer_safe_summary: str
