"""HTTP API for consultation holiday blackout dates.

CRUD over the ``consultation_holidays`` table, consumed by the CSR consultation
calendar (via the Node bridge). Mutations write through to the in-process cache
so the scheduling engine honors a new/removed holiday immediately in this worker;
other workers converge on the next periodic refresh. Auth mirrors the other
bridge endpoints (``require_http_auth``).
"""

import re
from datetime import date

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from bookcraft.api.auth import require_http_auth
from bookcraft.components.consultations import HolidayRepository
from bookcraft.components.consultations.holidays import add_to_cache, remove_from_cache
from bookcraft.infra.config import Settings

__all__ = ["router"]

router = APIRouter(prefix="/api/v1/holidays", tags=["holidays"])

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class HolidayItem(BaseModel):
    date: str
    label: str | None = None
    created_by: str | None = None


class HolidayListResponse(BaseModel):
    holidays: list[HolidayItem]


class HolidayCreateRequest(BaseModel):
    date: str
    label: str | None = None
    created_by: str | None = None


class HolidayDeleteResponse(BaseModel):
    date: str
    removed: bool


def _parse_iso_date(value: str) -> date:
    if not _ISO_DATE.match(value or ""):
        raise HTTPException(
            status_code=422,
            detail={"error": "date must be in YYYY-MM-DD format"},
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:  # e.g. 2026-13-40
        raise HTTPException(
            status_code=422,
            detail={"error": "not a valid calendar date"},
        ) from exc


def _repository(request: Request) -> HolidayRepository:
    repo = getattr(request.app.state, "holiday_repository", None)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "holiday store is not available"},
        )
    return repo


@router.get("", response_model=HolidayListResponse)
async def list_holidays(request: Request) -> HolidayListResponse:
    settings: Settings = request.app.state.settings
    require_http_auth(request, settings)
    repo = _repository(request)
    records = await repo.list_all()
    return HolidayListResponse(
        holidays=[
            HolidayItem(
                date=record.holiday_date.isoformat(),
                label=record.label,
                created_by=record.created_by,
            )
            for record in records
        ]
    )


@router.post("", response_model=HolidayItem)
async def create_holiday(payload: HolidayCreateRequest, request: Request) -> HolidayItem:
    settings: Settings = request.app.state.settings
    require_http_auth(request, settings)
    repo = _repository(request)
    holiday_date = _parse_iso_date(payload.date)
    record = await repo.add(
        holiday_date=holiday_date,
        label=payload.label,
        created_by=payload.created_by,
    )
    # Write-through so the bot stops offering/booking this day right away.
    add_to_cache(record.holiday_date)
    return HolidayItem(
        date=record.holiday_date.isoformat(),
        label=record.label,
        created_by=record.created_by,
    )


@router.delete("/{holiday_date}", response_model=HolidayDeleteResponse)
async def delete_holiday(holiday_date: str, request: Request) -> HolidayDeleteResponse:
    settings: Settings = request.app.state.settings
    require_http_auth(request, settings)
    repo = _repository(request)
    parsed = _parse_iso_date(holiday_date)
    removed = await repo.remove(holiday_date=parsed)
    remove_from_cache(parsed)
    return HolidayDeleteResponse(date=parsed.isoformat(), removed=removed)
