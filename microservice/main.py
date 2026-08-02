from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional 
from classify import classify_email
from confidence import compute_confidence


class EmailInput(BaseModel):
    subject: str
    body: str
    sender: str
    attachment_text: Optional[str]=None

app=FastAPI()

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
        attachment_unparsed=False  # will come from attachments.py in Step 3
    )
    return result
