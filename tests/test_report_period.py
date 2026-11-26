from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import column

from app.core.report_period import date_filters, timestamp_filters, validate_period


def test_order_period_includes_the_entire_local_end_date():
    start, stop = timestamp_filters(column("created_at"), 30, date(2024, 2, 29), date(2024, 3, 1))
    assert start.right.value == datetime(2024, 2, 29, 3, tzinfo=timezone.utc)
    assert stop.right.value == datetime(2024, 3, 2, 3, tzinfo=timezone.utc)
    assert "created_at < " in str(stop)


def test_daily_report_period_uses_inclusive_dates():
    day = date(2024, 2, 29)
    start, end = date_filters(column("report_date"), 30, day, day)
    assert start.right.value == end.right.value == day
    assert "report_date <= " in str(end)
    assert timestamp_filters(column("created_at"), None) == ()


@pytest.mark.parametrize("start,end", [
    (date(2024, 3, 1), None),
    (date(2024, 3, 1), date(2024, 2, 29)),
    (date(2023, 3, 1), date(2024, 3, 1)),
    (date(2024, 3, 1), date.max),
])
def test_validate_period_rejects_invalid_ranges(start, end):
    with pytest.raises(HTTPException) as error:
        validate_period(start, end)
    assert error.value.status_code == 422
