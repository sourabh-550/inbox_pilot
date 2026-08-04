import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_calendar_service():
    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token_file:
            token_file.write(creds.to_json())

    service = build("calendar", "v3", credentials=creds)
    return service

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

    result = service.freebusy().query(body=body).execute()
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

    created_event = service.events().insert(calendarId="primary", body=event).execute()
    return created_event.get("htmlLink")