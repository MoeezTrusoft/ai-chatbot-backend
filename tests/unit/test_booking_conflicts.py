from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from bookcraft.components.consultations.repository import (
    AppointmentConflictError,
    commit_appointment,
)


@pytest.mark.parametrize(
    ("sqlstate", "detail", "expected"),
    [
        ("40P01", "deadlock detected", AppointmentConflictError),
        ("40001", "serialization failure", AppointmentConflictError),
        (
            "23P01",
            'violates exclusion constraint "consultation_no_overlap"',
            AppointmentConflictError,
        ),
        ("23502", "unrelated not-null failure", DBAPIError),
        ("08006", "connection failure", DBAPIError),
    ],
)
async def test_booking_conflicts_rollback_without_hiding_unrelated_database_failures(
    sqlstate, detail, expected
):
    original = Exception(detail)
    original.sqlstate = sqlstate
    session = AsyncMock(spec=AsyncSession)
    session.commit.side_effect = DBAPIError("INSERT", {}, original)
    with pytest.raises(expected):
        await commit_appointment(session)
    session.rollback.assert_awaited_once()
    session.commit.assert_awaited_once()
