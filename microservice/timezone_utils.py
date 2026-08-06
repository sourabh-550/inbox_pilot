def to_ist(event_date: str | None) -> str | None:
    """
    Ensures an ISO datetime string explicitly carries the +05:30 (IST) offset.
    If it already has timezone info, leave it alone.
    If it's date-only, add a midnight time before the offset.
    If it has no timezone, append +05:30.
    Returns None unchanged if there's no date at all.
    """
    if not event_date:
        return event_date

    if event_date.endswith("Z") or "+" in event_date[10:]:
        return event_date

    # Date-only string (e.g. "2026-06-01") — needs a time component first
    if "T" not in event_date:
        return event_date + "T00:00:00+05:30"

    # Has a time but no offset (e.g. "2026-06-01T14:30:00")
    return event_date + "+05:30"