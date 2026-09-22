from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def is_date_only(value) -> bool:
    """
    True for a plain calendar date such as "2026-08-10" (no time part),
    which should become an all-day event rather than a timed one.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        date.fromisoformat(value.strip())
        return True
    except ValueError:
        return False


def to_ist(event_date: str | None) -> str | None:
    """
    Normalizes an event date to what Notion and Google Calendar expect:
    - None / "" -> None
    - date-only ("2026-08-10") -> returned as a date, with no invented time
    - naive datetime -> taken as IST, e.g. "2026-08-10T15:00:00+05:30"
    - datetime with any offset (Z, +05:30, -04:00, ...) -> converted to IST
    Raises ValueError for anything that can't be parsed.
    """
    if event_date is None:
        return None
    if not isinstance(event_date, str):
        raise ValueError(f"event_date must be a string, got {type(event_date).__name__}")

    value = event_date.strip()
    if not value:
        return None

    # Date-only has to be checked first: datetime.fromisoformat("2026-08-10")
    # would silently return midnight and turn a deadline into a 00:00 event.
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        pass

    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Unparseable event_date: {event_date!r}") from None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    else:
        dt = dt.astimezone(IST)

    return dt.isoformat()
