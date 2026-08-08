import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

LOCAL_TZ = ZoneInfo("Asia/Kolkata")


class ClassificationError(Exception):
    """
    Raised when the LLM's response can't be parsed as the expected JSON
    object, or when the Groq call itself fails. Callers should catch this
    and decide how to handle an email that couldn't be classified, rather
    than letting it crash the request as an unhandled 500.
    """
    def __init__(self, message: str, raw_output: str = None):
        super().__init__(message)
        self.raw_output = raw_output

SYSTEM_PROMPT_TEMPLATE = """You are an email classification and extraction assistant.
Your job is to read an email (and optional attachment text) and return ONLY a JSON object — no explanation, no markdown formatting, no extra text.

The current date and time is: {current_datetime} ({current_day_name}), timezone Asia/Kolkata (IST).
Use this as the anchor point for resolving any relative date/time references in the email, such as "tomorrow", "next Monday", "in 3 days", "this Friday", or "today". Always compute event_date relative to this current date, not relative to any date mentioned in the examples below.

Classify the email into exactly one of these categories:
- job_opportunity
- meeting
- deadline
- irrelevant

Note: category should reflect the nature of the action required, not just the topic. An email about a scheduled event with a specific date/time/venue (e.g. an interview drive, mock interview, orientation, or info session) is a "meeting", even if it's related to job placements. Use "job_opportunity" only when the email is about an actual job/internship posting, application process, or offer — not a scheduled event about job-related topics.

Also identify any of these flags that apply (return as a list, can be empty):
- missing_date: no date/time found, even though the category implies one should exist
- ambiguous_date: a date-like phrase exists but is vague (e.g. "sometime next week", "TBD")
- missing_sender_org: cannot tell who the email is actually from
- vague_subject: the subject line gives little signal about the email's content
- conflicting_signals: the email content could plausibly fit more than one category

Note: missing_date and ambiguous_date are mutually exclusive — use missing_date only when no date-like phrase exists at all, and ambiguous_date only when one exists but is unclear. Never use both for the same email.

Return a JSON object with exactly these fields:
{{
  "category": one of the four categories above,
  "source_name": string,
  "item_title": string,
  "event_date": ISO 8601 string or null,
  "location_or_link": string or null,
  "priority": "high", "medium", or "low",
  "summary": short 1-2 sentence string,
  "flags": list of zero or more flags from the list above
}}

Here are three examples:

Example 1:
Email subject: Interview Invitation - TechCorp
Email body: We would like to invite you for an interview on August 10th at 3 PM via Google Meet.
Sender: hr@techcorp.com
Output:
{{"category": "job_opportunity", "source_name": "TechCorp", "item_title": "Interview Invitation", "event_date": "2026-08-10T15:00:00", "location_or_link": "Google Meet", "priority": "high", "summary": "TechCorp invited the candidate for an interview on August 10th at 3 PM via Google Meet.", "flags": []}}

Example 2:
Email subject: Your Weekly Tech Digest
Email body: Here are this week's top 5 stories in AI and software development...
Sender: newsletter@techdigest.com
Output:
{{"category": "irrelevant", "source_name": "TechDigest", "item_title": "Weekly Tech Digest", "event_date": null, "location_or_link": null, "priority": "low", "summary": "A newsletter with weekly tech news roundup, not actionable.", "flags": []}}

Example 3:
Email subject: Mock Interview Drive for Final Year Students
Email body: A Mock Interview Drive will be conducted on August 1st at 9:15 AM in the Auditorium to prepare students for campus placements.
Sender: placement@college.edu
Output:
{{"category": "meeting", "source_name": "College Placement Cell", "item_title": "Mock Interview Drive", "event_date": "2026-08-01T09:15:00", "location_or_link": "Auditorium", "priority": "high", "summary": "The placement cell scheduled a mock interview drive on August 1st at 9:15 AM in the Auditorium to prepare students for campus placements.", "flags": []}}

Note: the dates in the examples above are illustrative only. They do NOT represent the current date. Always use the current date/time given at the top of this prompt to resolve any relative date language in the actual email you are classifying.
"""

def classify_email(subject: str, body: str, sender: str, attachment_text: str = None):
    now = datetime.now(LOCAL_TZ)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        current_datetime=now.strftime("%Y-%m-%d %H:%M:%S"),
        current_day_name=now.strftime("%A")
    )

    user_content = f"Email subject: {subject}\nEmail body: {body}\nSender: {sender}"
    if attachment_text:
        user_content += f"\nAttachment text: {attachment_text}"

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0,
            response_format={"type": "json_object"}
        )
    except Exception as e:
        raise ClassificationError(f"Groq API call failed: {e}")

    raw_output = response.choices[0].message.content

    try:
        return json.loads(raw_output)
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: model ignored instructions and wrapped the JSON in a
    # ```json ... ``` fence (or added stray text around it). Try to
    # salvage just the JSON object before giving up.
    if raw_output:
        stripped = raw_output.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.lower().startswith("json"):
                stripped = stripped[4:]
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(stripped[start:end + 1])
            except json.JSONDecodeError:
                pass

    raise ClassificationError(
        "Could not parse a valid JSON object from the model's response",
        raw_output=raw_output
    )