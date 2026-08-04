def to_ist(event_date: str | None) -> str | None:
    """
    Ensures an ISO datetime string explicitly carries the +05:30 (IST) offset.
    If it already has timezone info, leave it alone.
    If it has none, append +05:30.
    Returns None unchanged if there's no date at all.
    """
    if not event_date:
        return event_date

    if event_date.endswith("Z") or "+" in event_date[10:]:
        return event_date

    return event_date + "+05:30"