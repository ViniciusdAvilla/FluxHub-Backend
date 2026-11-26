from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException

REPORT_ZONE = ZoneInfo("America/Sao_Paulo")


def validate_period(start_date: date | None, end_date: date | None) -> None:
    if start_date is None and end_date is None:
        return
    if (
        start_date is None or end_date is None
        or start_date < date(1900, 1, 1)
        or end_date > datetime.now(REPORT_ZONE).date()
        or not 0 <= (end_date - start_date).days < 365
    ):
        raise HTTPException(422, "Selecione um período válido de até 365 dias.")


def timestamp_filters(column, days: int | None, start_date=None, end_date=None):
    validate_period(start_date, end_date)
    if start_date is not None and end_date is not None:
        start = datetime.combine(start_date, time.min, REPORT_ZONE)
        stop = datetime.combine(end_date + timedelta(days=1), time.min, REPORT_ZONE)
        return (column >= start.astimezone(timezone.utc),
                column < stop.astimezone(timezone.utc))
    if days is None:
        return ()
    return (column >= datetime.now(timezone.utc) - timedelta(days=days),)


def date_filters(column, days: int | None, start_date=None, end_date=None):
    validate_period(start_date, end_date)
    if start_date is not None and end_date is not None:
        return (column >= start_date, column <= end_date)
    if days is None:
        return ()
    return (column >= date.today() - timedelta(days=days),)
