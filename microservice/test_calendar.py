from calendar_utils import create_event_with_conflict_check

# This should conflict with the InboxPilot Test Event we created earlier (3-4 PM IST)
link, conflicts = create_event_with_conflict_check(
    summary="Overlapping Test Event",
    start_datetime="2026-08-10T15:30:00+05:30",
    end_datetime="2026-08-10T16:30:00+05:30"
)

if link:
    print("Event created:", link)
else:
    print("Conflict detected:", conflicts)