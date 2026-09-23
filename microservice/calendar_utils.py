import os
from datetime import date, timedelta
import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from retry_utils import read_retry, write_retry

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Per-request timeout for Calendar calls and the token refresh (the library
# defaults are 60s and 120s).
CALENDAR_TIMEOUT_S = 10


class _TimeoutRequest(Request):
    """google-auth's token-refresh transport, with our timeout instead of 120s."""
    def __call__(self, *args, timeout=CALENDAR_TIMEOUT_S, **kwargs):
        return super().__call__(*args, timeout=timeout, **kwargs)


def get_calendar_service():
    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # google-auth retries a failed refresh by itself (up to 3 attempts).
            creds.refresh(_TimeoutRequest())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token_file:
            token_file.write(creds.to_json())

    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=CALENDAR_TIMEOUT_S))
    service = build("calendar", "v3", http=http)
    return service


@read_retry()
def _query_freebusy(service, body: dict) -> dict:
    return service.freebusy().query(body=body).execute()


# Write policy: a timeout or 5xx isn't retried, because the event may already
# exist and a retry would create a duplicate.
@write_retry()
def _insert_event(service, event: dict) -> dict:
    return service.events().insert(calendarId="primary", body=event).execute()

def check_conflict(start_datetime: str, end_datetime: str) -> list:
    """
    Checks your primary calendar for existing events overlapping this time window.
    Returns a list of conflicting events (empty list means no conflict).
    """
    service = get_calendar_service()

    body = {
        "timeMin": start_datetime,
        "timeMax": end_datetime,
        "timeZone": "Asia/Kolkata",
        "items": [{"id": "primary"}]
    }

    result = _query_freebusy(service, body)
    busy_slots = result["calendars"]["primary"]["busy"]
    return busy_slots

def create_event_with_conflict_check(summary: str, start_datetime: str, end_datetime: str, location: str = "", description: str = ""):
    """
    Checks for conflicts first. If clear, creates the event and returns its link.
    If there's a conflict, returns None and the list of conflicting slots instead.
    """
    conflicts = check_conflict(start_datetime, end_datetime)

    if conflicts:
        return None, conflicts

    link = create_calendar_event(summary, start_datetime, end_datetime, location, description)
    return link, []


def create_calendar_event(summary: str, start_datetime: str, end_datetime: str, location: str = "", description: str = ""):
    service = get_calendar_service()

    event = {
        "summary": summary,
        "location": location,
        "description": description,
        "start": {
            "dateTime": start_datetime,
            "timeZone": "Asia/Kolkata",
        },
        "end": {
            "dateTime": end_datetime,
            "timeZone": "Asia/Kolkata",
        },
    }

    created_event = _insert_event(service, event)
    return created_event.get("htmlLink")


def create_all_day_event(summary: str, date_str: str, location: str = "", description: str = ""):
    """
    Creates an all-day event for a date-only item (e.g. a deadline) and
    returns its link. Marked transparent ("free") so it doesn't block the
    day, which is also why there's no conflict check.
    """
    service = get_calendar_service()

    # Google treats an all-day event's end date as exclusive.
    end_date = (date.fromisoformat(date_str) + timedelta(days=1)).isoformat()

    event = {
        "summary": summary,
        "location": location,
        "description": description,
        "start": {"date": date_str},
        "end": {"date": end_date},
        "transparency": "transparent",
    }

    created_event = _insert_event(service, event)
    return created_event.get("htmlLink")