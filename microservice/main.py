from fastapi import FastAPI, UploadFile, File
from pydantic import BaseModel
from typing import Optional
import shutil
import os

from classify import classify_email
from confidence import compute_confidence
from attachments import extract_attachment_text
from notion import find_or_create_source, create_item
from timezone_utils import to_ist

app = FastAPI()

class EmailInput(BaseModel):
    subject: str
    body: str
    sender: str
    attachment_text: Optional[str] = None


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


@app.post("/process")
def process_email(email: EmailInput):
    result = classify_email(
        subject=email.subject,
        body=email.body,
        sender=email.sender,
        attachment_text=email.attachment_text
    )

    result["confidence_score"] = compute_confidence(
        flags=result.get("flags", []),
        attachment_unparsed=False
    )

    result["event_date"] = to_ist(result.get("event_date"))

    notion_item_id = save_to_notion(result, email.sender)
    result["notion_item_id"] = notion_item_id

    return result


@app.post("/process_with_attachment")
def process_email_with_attachment(
    subject: str,
    body: str,
    sender: str,
    file: UploadFile = File(...)
):
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

    result["event_date"] = to_ist(result.get("event_date"))

    notion_item_id = save_to_notion(result, sender)
    result["notion_item_id"] = notion_item_id

    return result