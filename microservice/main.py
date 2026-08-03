from fastapi import FastAPI, UploadFile, File
from pydantic import BaseModel
from typing import Optional
import shutil
import os

from classify import classify_email
from confidence import compute_confidence
from attachments import extract_attachment_text

app = FastAPI()

class EmailInput(BaseModel):
    subject: str
    body: str
    sender: str
    attachment_text: Optional[str] = None


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

    return result