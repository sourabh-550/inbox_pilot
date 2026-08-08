import base64
import logging

from fastapi import FastAPI, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List
import shutil
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from classify import classify_email, ClassificationError
from confidence import compute_confidence
from attachments import extract_attachment_text
from notion import find_or_create_source, create_item, get_todays_items
from timezone_utils import to_ist
from calendar_utils import create_event_with_conflict_check
from telegram_utils import send_telegram_message, escape_markdown
from dedup import compute_fingerprint, is_duplicate, mark_processed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("inboxpilot.main")

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
        f"*{escape_markdown(result.get('item_title', 'Untitled'))}*\n"
        f"From: {escape_markdown(result.get('source_name', 'Unknown'))}\n"
        f"Category: {escape_markdown(result.get('category', 'unknown'))}\n"
        f"Confidence: {result.get('confidence_score', 0.0)}\n"
        f"Flags: {escape_markdown(readable_flags)}\n\n"
        f"This needs manual review."
    )


def send_classification_failed_alert(subject: str, sender: str, error: str):
    send_telegram_message(
        f"🚨 *Classification Failed*\n\n"
        f"Subject: {escape_markdown(subject)}\n"
        f"From: {escape_markdown(sender)}\n"
        f"Error: {escape_markdown(error)}\n\n"
        f"This email was NOT written to Notion or scheduled. "
        f"It's logged as needing manual review — please check it directly in Gmail."
    )


def try_schedule_event(result: dict):
    """
    Returns a dict describing what happened: scheduled, conflict, skipped, or error.
    Wrapped so that a Calendar API failure (e.g. an expired/revoked OAuth
    token on the headless EC2 box) can't crash the whole /process request
    after the Notion write has already succeeded.
    """
    event_date = result.get("event_date")
    confidence = result.get("confidence_score", 0.0)

    if not event_date:
        return {"calendar_status": "skipped_no_date"}

    if confidence < CONFIDENCE_THRESHOLD:
        return {"calendar_status": "skipped_low_confidence"}

    try:
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
    except Exception as e:
        logger.error(f"Calendar scheduling failed for '{result.get('item_title')}': {e}")
        send_telegram_message(
            f"⚠️ *Calendar Scheduling Error*\n\n"
            f"*{escape_markdown(result.get('item_title', 'Untitled'))}*\n"
            f"Error: {escape_markdown(str(e))}\n\n"
            f"The item was saved to Notion but NOT scheduled. Please add it to your calendar manually."
        )
        return {"calendar_status": "error", "calendar_error": str(e)}

    if link:
        return {"calendar_status": "scheduled", "calendar_link": link}
    else:
        send_telegram_message(
            f"⚠️ *Scheduling Conflict*\n\n"
            f"*{escape_markdown(result.get('item_title', 'Untitled'))}*\n"
            f"From: {escape_markdown(result.get('source_name', 'Unknown'))}\n"
            f"Requested time: {event_date}\n\n"
            f"This overlaps with an existing event — not auto-scheduled. Please review manually."
        )
        return {"calendar_status": "conflict", "conflicts": conflicts}


def run_pipeline(
    email_id: str,
    subject: str,
    body: str,
    sender: str,
    attachment_text: Optional[str],
    attachment_unparsed: bool
) -> dict:
    """
    Shared pipeline logic for both /process and /process_with_attachment,
    used from the point right after attachment extraction.

    Key ordering fix: mark_processed() now runs immediately after the
    Notion write succeeds, NOT after Calendar/Telegram too. Those two are
    best-effort and individually wrapped, so a Calendar or Telegram hiccup
    can no longer cause the email to be left "unprocessed" and reprocessed
    into a duplicate Notion item on a retry.
    """
    try:
        result = classify_email(
            subject=subject,
            body=body,
            sender=sender,
            attachment_text=attachment_text
        )
    except ClassificationError as e:
        logger.error(f"Classification failed for '{subject}' from {sender}: {e}")
        send_classification_failed_alert(subject, sender, str(e))
        # Deliberately NOT marking this email as processed: nothing was
        # saved anywhere, so it's safe (and desirable) for this to be
        # retried rather than silently lost.
        return {
            "status": "classification_failed",
            "email_id": email_id,
            "error": str(e)
        }

    result["confidence_score"] = compute_confidence(
        flags=result.get("flags", []),
        attachment_unparsed=attachment_unparsed
    )
    if attachment_unparsed and "attachment_unparsed" not in result.get("flags", []):
        result.setdefault("flags", []).append("attachment_unparsed")

    if result["confidence_score"] < CONFIDENCE_THRESHOLD:
        send_low_confidence_alert(result)

    result["event_date"] = to_ist(result.get("event_date"))

    try:
        notion_item_id = save_to_notion(result, sender)
    except Exception as e:
        logger.error(f"Notion write failed for '{subject}' from {sender}: {e}")
        send_telegram_message(
            f"🚨 *Notion Write Failed*\n\n"
            f"Subject: {escape_markdown(subject)}\n"
            f"From: {escape_markdown(sender)}\n"
            f"Error: {escape_markdown(str(e))}\n\n"
            f"Nothing was saved for this email. It's safe to retry."
        )
        # Not marking as processed: nothing was actually saved, so a retry
        # (rather than silent data loss) is the correct outcome here.
        return {
            "status": "notion_write_failed",
            "email_id": email_id,
            "error": str(e)
        }

    result["notion_item_id"] = notion_item_id

    # From this point on, a real Notion record exists. Mark the email as
    # processed now so that any downstream (Calendar/Telegram) failure
    # can't cause a re-processed duplicate Notion item on retry.
    mark_processed(email_id)

    calendar_result = try_schedule_event(result)
    result.update(calendar_result)

    return result


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
        temp_path = None
        try:
            file_bytes = base64.b64decode(att.data_base64)
            # NOTE: still using a filename-based temp path here (not fixed
            # in this pass — see the attachment race-condition/leak items
            # from the review; flagging as a follow-up fix).
            temp_path = f"temp_{att.filename}"
            with open(temp_path, "wb") as f:
                f.write(file_bytes)

            text, unparsed = extract_attachment_text(temp_path)

            if unparsed:
                attachment_unparsed = True
            if text:
                attachment_texts.append(f"[{att.filename}]\n{text}")
        except Exception as e:
            attachment_unparsed = True
            logger.error(f"Failed to process attachment {att.filename}: {e}")
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    combined_attachment_text = "\n\n".join(attachment_texts) if attachment_texts else None

    return run_pipeline(
        email_id=email_id,
        subject=email.subject,
        body=email.body,
        sender=email.sender,
        attachment_text=combined_attachment_text,
        attachment_unparsed=attachment_unparsed
    )


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
    attachment_text, attachment_unparsed = "", True
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        attachment_text, attachment_unparsed = extract_attachment_text(temp_path)
    except Exception as e:
        logger.error(f"Failed to process attachment {file.filename}: {e}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return run_pipeline(
        email_id=email_id,
        subject=subject,
        body=body,
        sender=sender,
        attachment_text=attachment_text if attachment_text else None,
        attachment_unparsed=attachment_unparsed
    )


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
            title = escape_markdown(it.get("title", "Untitled"))
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