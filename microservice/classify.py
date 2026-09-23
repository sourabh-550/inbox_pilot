import os
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
from groq import Groq
from dotenv import load_dotenv

# ClassificationError lives in schemas (importable without a Groq client);
# re-exported here so `from classify import ClassificationError` still works.
from schemas import (
    ClassificationError, CLASSIFICATION_JSON_SCHEMA, validate_classification, truncate_text,
)

load_dotenv()

logger = logging.getLogger("inboxpilot.classify")

# The SDK retries by itself (408/409/429/5xx, timeouts, connection errors)
# and logs each retry at INFO, so there's no tenacity layer on top. Both
# values are set explicitly; worst case is about 3 x 30s plus short backoffs.
client = Groq(
    api_key=os.environ.get("GROQ_API_KEY"),
    timeout=httpx.Timeout(30.0, connect=5.0),
    max_retries=2,
)

LOCAL_TZ = ZoneInfo("Asia/Kolkata")

# gpt-oss is a reasoning model: don't add a small max_tokens cap, or the
# reasoning can use it up before any JSON is written.
GROQ_MODEL = os.environ.get("GROQ_MODEL") or "openai/gpt-oss-120b"

# Caps on what goes into the prompt (about 4k tokens together), so a huge
# email or attachment can't blow the context or Groq's rate limits.
MAX_BODY_CHARS = 6000
MAX_ATTACHMENT_CHARS = 10000

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
  "event_date": "YYYY-MM-DDTHH:MM:SS" or "YYYY-MM-DD" or null (see rules below),
  "location_or_link": string or null,
  "priority": "high", "medium", or "low",
  "summary": short 1-2 sentence string,
  "flags": list of zero or more flags from the list above
}}

Rules for event_date:
- If the email gives both a date and a time, return "YYYY-MM-DDTHH:MM:SS" in IST, with no timezone offset.
- If the email gives only a date and no time (common for deadlines, e.g. "submit by August 5th"), return "YYYY-MM-DD". Never invent a time such as 00:00 or 23:59.
- If the email gives no date at all, return null.

Here are four examples:

Example 1:
Email subject: Interview Invitation - TechCorp
Email body: We would like to invite you for an interview on August 10th at 3 PM via Google Meet.
Sender: hr@techcorp.com
Output:
{{"category": "meeting", "source_name": "TechCorp", "item_title": "Interview Invitation", "event_date": "2026-08-10T15:00:00", "location_or_link": "Google Meet", "priority": "high", "summary": "TechCorp invited the candidate for an interview on August 10th at 3 PM via Google Meet.", "flags": []}}

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

Example 4:
Email subject: Final Project Report Submission
Email body: Please submit your final project report through the course portal by August 5th.
Sender: professor@college.edu
Output:
{{"category": "deadline", "source_name": "College Faculty", "item_title": "Final Project Report Submission", "event_date": "2026-08-05", "location_or_link": "Course portal", "priority": "high", "summary": "The final project report must be submitted through the course portal by August 5th.", "flags": []}}

Note: the dates in the examples above are illustrative only. They do NOT represent the current date. Always use the current date/time given at the top of this prompt to resolve any relative date language in the actual email you are classifying.
"""

def _cap_for_prompt(text: str, limit: int, label: str) -> str:
    capped = truncate_text(text, limit=limit, suffix="\n[truncated]")
    if capped != text:
        logger.warning(f"{label} cut from {len(text)} characters to the {limit}-character prompt limit")
    return capped


def _parse_json(raw_output: str):
    """
    Returns the parsed JSON value, or None if nothing parseable was found.
    """
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

    return None


def classify_email(subject: str, body: str, sender: str, attachment_text: str = None):
    now = datetime.now(LOCAL_TZ)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        current_datetime=now.strftime("%Y-%m-%d %H:%M:%S"),
        current_day_name=now.strftime("%A")
    )

    body = _cap_for_prompt(body, MAX_BODY_CHARS, "Email body")
    user_content = f"Email subject: {subject}\nEmail body: {body}\nSender: {sender}"
    if attachment_text:
        attachment_text = _cap_for_prompt(attachment_text, MAX_ATTACHMENT_CHARS, "Attachment text")
        user_content += f"\nAttachment text: {attachment_text}"

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0,
            reasoning_effort="low",
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "email_classification",
                    "strict": True,
                    "schema": CLASSIFICATION_JSON_SCHEMA,
                },
            }
        )
    except Exception as e:
        raise ClassificationError(f"Groq API call failed (model {GROQ_MODEL}): {e}")

    raw_output = response.choices[0].message.content
    finish_reason = response.choices[0].finish_reason

    parsed = _parse_json(raw_output)
    if parsed is None:
        raise ClassificationError(
            f"Could not parse a valid JSON object from the model's response "
            f"(model {GROQ_MODEL}, finish_reason {finish_reason})",
            raw_output=raw_output
        )

    # Strict mode should already guarantee the shape; this is the safety net.
    try:
        return validate_classification(parsed, raw_output=raw_output)
    except ClassificationError as e:
        raise ClassificationError(
            f"{e} (model {GROQ_MODEL}, finish_reason {finish_reason})",
            raw_output=raw_output
        ) from e