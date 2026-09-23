import base64
import io
import logging

from fastapi import FastAPI, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from classify import classify_email, ClassificationError
from confidence import compute_confidence
from attachments import save_and_extract
from notion import (
    find_or_create_source, create_item, update_item_status, get_todays_items,
    STATUS_SCHEDULED, STATUS_NEEDS_REVIEW, STATUS_CONFLICT, STATUS_LOGGED_ONLY,
)
from timezone_utils import to_ist, is_date_only
from calendar_utils import create_event_with_conflict_check, create_all_day_event
from telegram_utils import send_telegram_message, escape_markdown
from dedup import compute_fingerprint, claim, mark_done, release, DedupUnavailableError

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


def initial_notion_status(result: dict) -> str:
    """
    Status written with the Notion item, before the calendar step runs.
    A dated, confident item starts as "Needs review" and is only promoted
    once the calendar outcome is known (see sync_notion_status), so a crash
    or calendar failure never leaves it wrongly marked "Scheduled".
    """
    if result.get("confidence_score", 0.0) < CONFIDENCE_THRESHOLD:
        return STATUS_NEEDS_REVIEW
    if not result.get("event_date"):
        return STATUS_LOGGED_ONLY
    return STATUS_NEEDS_REVIEW


CALENDAR_TO_NOTION_STATUS = {
    "scheduled": STATUS_SCHEDULED,
    "conflict": STATUS_CONFLICT,
}


def sync_notion_status(page_id: str, calendar_status: str, current: str) -> str:
    """
    Updates the Notion item's status from the calendar outcome and returns
    the status actually stored. Only "scheduled" and "conflict" change it.
    Best-effort: on failure the item keeps its current status ("Needs
    review"), which is logged but not alerted on.
    """
    new_status = CALENDAR_TO_NOTION_STATUS.get(calendar_status)
    if new_status is None:
        return current

    try:
        update_item_status(page_id, new_status)
    except Exception as e:
        logger.error(f"Notion status update to '{new_status}' failed for page {page_id}: {e}")
        return current

    return new_status


def save_to_notion(result: dict, sender: str, status: str):
    source_id = find_or_create_source(result.get("source_name", "Unknown"))

    item_id = create_item(
        title=result.get("item_title", "Untitled"),
        source_page_id=source_id,
        category=result.get("category", "irrelevant"),
        status=status,
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


def send_dedup_warning(stage: str, email_id: str, error: str, consequence: str):
    send_telegram_message(
        f"⚠️ *Dedup Database Error*\n\n"
        f"Stage: {escape_markdown(stage)}\n"
        f"Email ID: {escape_markdown(email_id)}\n"
        f"Error: {escape_markdown(error)}\n\n"
        f"{consequence}"
    )


def acquire_claim(email_id: str) -> tuple[bool, Optional[str]]:
    """
    Returns (skip, claim_token). skip=True means another request already
    processed or is processing this email. If the dedup DB is unreachable we
    fail open: process anyway with claim_token=None, which makes the later
    mark_done/release calls no-ops for this request.
    """
    try:
        claim_token = claim(email_id)
    except DedupUnavailableError as e:
        logger.error(f"Dedup unavailable, processing {email_id} without duplicate protection: {e}")
        send_dedup_warning(
            "claim", email_id, str(e),
            "Processing this email anyway, without duplicate protection."
        )
        return False, None

    return claim_token is None, claim_token


def safe_mark_done(email_id: str, claim_token: Optional[str]):
    if claim_token is None:
        return
    try:
        mark_done(email_id, claim_token)
    except DedupUnavailableError as e:
        send_dedup_warning(
            "mark done", email_id, str(e),
            "The item was saved to Notion, but a retry of this email may create a duplicate."
        )


def safe_release(email_id: str, claim_token: Optional[str]):
    if claim_token is None:
        return
    try:
        release(email_id, claim_token)
    except DedupUnavailableError as e:
        send_dedup_warning(
            "release", email_id, str(e),
            "The claim could not be released, so retries of this email will be "
            "skipped as duplicates until the claim goes stale."
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
        # Date-only items (typically deadlines) become a transparent all-day
        # event: it doesn't block the day, so there's no conflict check.
        if is_date_only(event_date):
            link = create_all_day_event(
                summary=result.get("item_title", "Untitled"),
                date_str=event_date,
                location=result.get("location_or_link") or "",
                description=result.get("summary", "")
            )
            return {"calendar_status": "scheduled", "calendar_link": link}

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
    claim_token: Optional[str],
    subject: str,
    body: str,
    sender: str,
    attachment_text: Optional[str],
    attachment_unparsed: bool
) -> dict:
    """
    Shared pipeline logic for both /process and /process_with_attachment,
    used from the point right after attachment extraction. The caller has
    already claimed the email (claim_token is None if dedup was unavailable).

    Key ordering: mark_done() runs immediately after the Notion write
    succeeds, NOT after Calendar/Telegram too. Those two are best-effort and
    individually wrapped, so a Calendar or Telegram hiccup can't cause the
    email to be left "unprocessed" and reprocessed into a duplicate Notion
    item on a retry. If classification or the Notion write fails, the claim
    is released so a retry can succeed.
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
        # Releasing the claim: nothing was saved anywhere, so it's safe
        # (and desirable) for this to be retried rather than silently lost.
        safe_release(email_id, claim_token)
        return {
            "status": "classification_failed",
            "email_id": email_id,
            "error": str(e)
        }

    # Normalize the date before scoring, so an unparseable date can lower
    # the confidence score instead of breaking the Notion write later.
    raw = result.get("event_date")
    try:
        result["event_date"] = to_ist(raw)
    except ValueError:
        logger.warning(f"Unparseable event_date {raw!r} for '{subject}' from {sender}, treating as no date")
        result["event_date"] = None
        flags = result.setdefault("flags", [])
        if "missing_date" not in flags and "ambiguous_date" not in flags:
            flags.append("ambiguous_date")

    result["confidence_score"] = compute_confidence(
        flags=result.get("flags", []),
        attachment_unparsed=attachment_unparsed
    )
    if attachment_unparsed and "attachment_unparsed" not in result.get("flags", []):
        result.setdefault("flags", []).append("attachment_unparsed")

    if result["confidence_score"] < CONFIDENCE_THRESHOLD:
        send_low_confidence_alert(result)

    notion_status = initial_notion_status(result)

    try:
        notion_item_id = save_to_notion(result, sender, notion_status)
    except Exception as e:
        logger.error(f"Notion write failed for '{subject}' from {sender}: {e}")
        send_telegram_message(
            f"🚨 *Notion Write Failed*\n\n"
            f"Subject: {escape_markdown(subject)}\n"
            f"From: {escape_markdown(sender)}\n"
            f"Error: {escape_markdown(str(e))}\n\n"
            f"Nothing was saved for this email. It's safe to retry."
        )
        # Releasing the claim: nothing was actually saved, so a retry
        # (rather than silent data loss) is the correct outcome here.
        safe_release(email_id, claim_token)
        return {
            "status": "notion_write_failed",
            "email_id": email_id,
            "error": str(e)
        }

    result["notion_item_id"] = notion_item_id

    # From this point on, a real Notion record exists. Mark the email as
    # done now so that any downstream (Calendar/Telegram) failure can't
    # cause a re-processed duplicate Notion item on retry. A dedup DB error
    # here is alerted on but doesn't stop the calendar step.
    safe_mark_done(email_id, claim_token)

    calendar_result = try_schedule_event(result)
    result.update(calendar_result)

    result["notion_status"] = sync_notion_status(
        notion_item_id, calendar_result["calendar_status"], notion_status
    )

    return result


@app.post("/process")
def process_email(email: EmailInput):
    email_id = email.gmail_message_id or compute_fingerprint(
        email.subject, email.sender, email.body
    )

    skip, claim_token = acquire_claim(email_id)
    if skip:
        return {"status": "duplicate_skipped", "email_id": email_id}

    # Any unexpected exception releases the claim so the email can be
    # retried. After the Notion write, mark_done has already set the row to
    # 'done', so release is a no-op there and can't undo a saved email.
    try:
        # Decode and extract text from any attachments
        attachment_texts = []
        attachment_unparsed = False

        for att in email.attachments:
            try:
                file_bytes = base64.b64decode(att.data_base64)
                text, unparsed = save_and_extract(att.filename, io.BytesIO(file_bytes))

                if unparsed:
                    attachment_unparsed = True
                if text:
                    attachment_texts.append(f"[{att.filename}]\n{text}")
            except Exception as e:
                attachment_unparsed = True
                logger.error(f"Failed to process attachment {att.filename}: {e}")

        combined_attachment_text = "\n\n".join(attachment_texts) if attachment_texts else None

        return run_pipeline(
            email_id=email_id,
            claim_token=claim_token,
            subject=email.subject,
            body=email.body,
            sender=email.sender,
            attachment_text=combined_attachment_text,
            attachment_unparsed=attachment_unparsed
        )
    except Exception:
        safe_release(email_id, claim_token)
        raise


@app.post("/process_with_attachment")
def process_email_with_attachment(
    subject: str,
    body: str,
    sender: str,
    file: UploadFile = File(...),
    gmail_message_id: str = None
):
    email_id = gmail_message_id or compute_fingerprint(subject, sender, body)

    skip, claim_token = acquire_claim(email_id)
    if skip:
        return {"status": "duplicate_skipped", "email_id": email_id}

    # See process_email: release the claim on any unexpected exception.
    try:
        attachment_text, attachment_unparsed = "", True
        try:
            attachment_text, attachment_unparsed = save_and_extract(file.filename, file.file)
        except Exception as e:
            logger.error(f"Failed to process attachment {file.filename}: {e}")

        return run_pipeline(
            email_id=email_id,
            claim_token=claim_token,
            subject=subject,
            body=body,
            sender=sender,
            attachment_text=attachment_text if attachment_text else None,
            attachment_unparsed=attachment_unparsed
        )
    except Exception:
        safe_release(email_id, claim_token)
        raise


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
                    # Date-only items have no time, so don't show "12:00 AM"
                    fmt = "%b %d" if is_date_only(event_date) else "%b %d, %I:%M %p"
                    date_str = dt.strftime(fmt)
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