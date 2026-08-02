import os
import json
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are an email classification and extraction assistant.
Your job is to read an email (and optional attachment text) and return ONLY a JSON object — no explanation, no markdown formatting, no extra text.

Classify the email into exactly one of these categories:
- job_opportunity
- meeting
- deadline
- irrelevant

Also identify any of these flags that apply (return as a list, can be empty):
- missing_date: no date/time found, even though the category implies one should exist
- ambiguous_date: a date-like phrase exists but is vague (e.g. "sometime next week", "TBD")
- missing_sender_org: cannot tell who the email is actually from
- vague_subject: the subject line gives little signal about the email's content
- conflicting_signals: the email content could plausibly fit more than one category

Return a JSON object with exactly these fields:
{
  "category": one of the four categories above,
  "source_name": string,
  "item_title": string,
  "event_date": ISO 8601 string or null,
  "location_or_link": string or null,
  "priority": "high", "medium", or "low",
  "summary": short 1-2 sentence string,
  "flags": list of zero or more flags from the list above
}

Here are two examples:

Example 1:
Email subject: Interview Invitation - TechCorp
Email body: We would like to invite you for an interview on August 10th at 3 PM via Google Meet.
Sender: hr@techcorp.com
Output:
{"category": "job_opportunity", "source_name": "TechCorp", "item_title": "Interview Invitation", "event_date": "2026-08-10T15:00:00", "location_or_link": "Google Meet", "priority": "high", "summary": "TechCorp invited the candidate for an interview on August 10th at 3 PM via Google Meet.", "flags": []}

Example 2:
Email subject: Your Weekly Tech Digest
Email body: Here are this week's top 5 stories in AI and software development...
Sender: newsletter@techdigest.com
Output:
{"category": "irrelevant", "source_name": "TechDigest", "item_title": "Weekly Tech Digest", "event_date": null, "location_or_link": null, "priority": "low", "summary": "A newsletter with weekly tech news roundup, not actionable.", "flags": []}
Note: missing_date and ambiguous_date are mutually exclusive — use missing_date only when no date-like phrase exists at all, and ambiguous_date only when one exists but is unclear. Never use both for the same email.
"""
def classify_email(subject: str, body: str, sender: str, attachment_text: str = None):
    user_content = f"Email subject: {subject}\nEmail body: {body}\nSender: {sender}"
    if attachment_text:
        user_content += f"\nAttachment text: {attachment_text}"

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content}
        ],
        temperature=0
    )

    raw_output = response.choices[0].message.content
    return json.loads(raw_output)