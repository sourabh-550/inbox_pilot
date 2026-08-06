import base64

from fastapi import FastAPI, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List
import shutil
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from classify import classify_email
from confidence import compute_confidence
from attachments import extract_attachment_text
from notion import find_or_create_source, create_item, get_todays_items
from timezone_utils import to_ist
from calendar_utils import create_event_with_conflict_check
from telegram_utils import send_telegram_message
from dedup import compute_fingerprint, is_duplicate, mark_processed

app = FastAPI()

CONFIDENCE_THRESHOLD = 0.7
LOCAL_TZ = ZoneInfo("Asia/Kolkata")

class AttachmentInput(BaseModel):
    filename: str
    mimetype: str
    data_base64: str

class EmailInput(BaseModel):
    subject: str
    body: str
    sender: str
    attachment_text: Optional[str] = None
    gmail_message_id: Optional[str] = None
    attachments: List[AttachmentInput] = []


def save_to_notion(result: dict, sender: str):
    source_id = find_or_create_source(result.get("source_name", "Unknown"))

    item_id = create_item(
        title=result.get("item_title", "Untitled"),
        source_page_id=source_id,
        category=result.get("category", "irrelevant"),
        event_date=result.get("event_date"),
        priority=result.get("priority", "medium"),
        location_or_link=result.get("location_or_link") or "",
        source_email=sender,
        attachment_summary=result.get("summary", ""),
        confidence_score=result.get("confidence_score", 0.0),
        notes=""
    )
    return item_id


def send_low_confidence_alert(result: dict):
    flags = result.get("flags", [])
    readable_flags = ", ".join(f.replace("_", " ") for f in flags) or "none"

    send_telegram_message(
        f"❓ *Low Confidence Item*\n\n"
        f"*{result.get('item_title', 'Untitled')}*\n"
        f"From: {result.get('source_name', 'Unknown')}\n"
        f"Category: {result.get('category', 'unknown')}\n"
        f"Confidence: {result.get('confidence_score', 0.0)}\n"
        f"Flags: {readable_flags}\n\n"
        f"This needs manual review."
    )


def try_schedule_event(result: dict):
    """
    Returns a dict describing what happened: scheduled, conflict, or skipped.
    """
    event_date = result.get("event_date")
    confidence = result.get("confidence_score", 0.0)

    if not event_date:
        return {"calendar_status": "skipped_no_date"}

    if confidence < CONFIDENCE_THRESHOLD:
        return {"calendar_status": "skipped_low_confidence"}

    # naive 1-hour duration assumption, since we only extract a start time for now
    start = datetime.fromisoformat(event_date)
    end = start + timedelta(hours=1)

    link, conflicts = create_event_with_conflict_check(
        summary=result.get("item_title", "Untitled"),
        start_datetime=start.isoformat(),
        end_datetime=end.isoformat(),
        location=result.get("location_or_link") or "",
        description=result.get("summary", "")
    )

    if link:
        return {"calendar_status": "scheduled", "calendar_link": link}
    else:
        send_telegram_message(
            f"⚠️ *Scheduling Conflict*\n\n"
            f"*{result.get('item_title', 'Untitled')}*\n"
            f"From: {result.get('source_name', 'Unknown')}\n"
            f"Requested time: {event_date}\n\n"
            f"This overlaps with an existing event — not auto-scheduled. Please review manually."
        )
        return {"calendar_status": "conflict", "conflicts": conflicts}


@app.post("/process")
def process_email(email: EmailInput):
    email_id = email.gmail_message_id or compute_fingerprint(
        email.subject, email.sender, email.body
    )

    if is_duplicate(email_id):
        return {"status": "duplicate_skipped", "email_id": email_id}

    # Decode and extract text from any attachments
    attachment_texts = []
    attachment_unparsed = False

    for att in email.attachments:
        try:
            file_bytes = base64.b64decode(att.data_base64)
            temp_path = f"temp_{att.filename}"
            with open(temp_path, "wb") as f:
                f.write(file_bytes)

            text, unparsed = extract_attachment_text(temp_path)
            os.remove(temp_path)

            if unparsed:
                attachment_unparsed = True
            if text:
                attachment_texts.append(f"[{att.filename}]\n{text}")
        except Exception as e:
            attachment_unparsed = True
            print(f"Failed to process attachment {att.filename}: {e}")

    combined_attachment_text = "\n\n".join(attachment_texts) if attachment_texts else None

    result = classify_email(
        subject=email.subject,
        body=email.body,
        sender=email.sender,
        attachment_text=combined_attachment_text
    )

    result["confidence_score"] = compute_confidence(
        flags=result.get("flags", []),
        attachment_unparsed=attachment_unparsed
    )

    if result["confidence_score"] < CONFIDENCE_THRESHOLD:
        send_low_confidence_alert(result)

    result["event_date"] = to_ist(result.get("event_date"))

    notion_item_id = save_to_notion(result, email.sender)
    result["notion_item_id"] = notion_item_id

    calendar_result = try_schedule_event(result)
    result.update(calendar_result)

    mark_processed(email_id)

    return result


@app.post("/process_with_attachment")
def process_email_with_attachment(
    subject: str,
    body: str,
    sender: str,
    file: UploadFile = File(...),
    gmail_message_id: str = None
):
    email_id = gmail_message_id or compute_fingerprint(subject, sender, body)

    if is_duplicate(email_id):
        return {"status": "duplicate_skipped", "email_id": email_id}

    temp_path = f"temp_{file.filename}"
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    attachment_text, attachment_unparsed = extract_attachment_text(temp_path)

    os.remove(temp_path)

    result = classify_email(
        subject=subject,
        body=body,
        sender=sender,
        attachment_text=attachment_text if attachment_text else None
    )

    result["confidence_score"] = compute_confidence(
        flags=result.get("flags", []),
        attachment_unparsed=attachment_unparsed
    )

    if result["confidence_score"] < CONFIDENCE_THRESHOLD:
        send_low_confidence_alert(result)

    result["event_date"] = to_ist(result.get("event_date"))

    notion_item_id = save_to_notion(result, sender)
    result["notion_item_id"] = notion_item_id

    calendar_result = try_schedule_event(result)
    result.update(calendar_result)

    mark_processed(email_id)

    return result


CATEGORY_LABELS = {
    "Job Opportunity": "💼 Job Opportunities",
    "Meeting": "📅 Meetings",
    "Deadline": "⏰ Deadlines",
    "Other": "📥 Other / Irrelevant",
}

CATEGORY_ORDER = ["Job Opportunity", "Meeting", "Deadline", "Other"]


def format_digest_message(items: list, digest_date: str) -> str:
    if not items:
        return f"📊 *Daily Summary — {digest_date}*\n\nNo new emails processed today."

    grouped = {}
    for item in items:
        cat = item.get("category") or "Other"
        grouped.setdefault(cat, []).append(item)

    lines = [f"📊 *Daily Summary — {digest_date}*", ""]

    for cat in CATEGORY_ORDER:
        cat_items = grouped.get(cat, [])
        if not cat_items:
            continue

        if cat == "Other":
            lines.append(f"{CATEGORY_LABELS[cat]}: {len(cat_items)} logged")
            lines.append("")
            continue

        lines.append(f"{CATEGORY_LABELS.get(cat, cat)} ({len(cat_items)})")
        for it in cat_items:
            title = it.get("title", "Untitled")
            event_date = it.get("event_date")
            if event_date:
                try:
                    dt = datetime.fromisoformat(event_date)
                    date_str = dt.strftime("%b %d, %I:%M %p")
                    lines.append(f"  • {title} — {date_str}")
                except ValueError:
                    lines.append(f"  • {title}")
            else:
                lines.append(f"  • {title}")
        lines.append("")

    return "\n".join(lines).strip()


@app.post("/daily_digest")
def daily_digest():
    """
    Summarizes everything processed "today" (IST calendar day) and sends
    a single Telegram message. Intended to be called once daily by an
    n8n Schedule Trigger node — not called by the Gmail-triggered path.
    """
    now = datetime.now(LOCAL_TZ)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)

    items = get_todays_items(
        start_iso=start_of_day.isoformat(),
        end_iso=end_of_day.isoformat()
    )

    message = format_digest_message(items, digest_date=now.strftime("%B %d, %Y"))
    send_telegram_message(message)

    return {"status": "sent", "item_count": len(items), "date": now.strftime("%Y-%m-%d")}