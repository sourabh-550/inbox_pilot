"""
Manual check of the classifier against the live Groq API. Run from inside
microservice/:
    python try_classify.py

Imports only classify, so nothing here touches Notion, Calendar, Telegram
or the dedup database. Needs GROQ_API_KEY in .env.
"""
import json

from classify import classify_email, ClassificationError, GROQ_MODEL

SAMPLES = [
    {
        "subject": "Technical Interview Scheduled - Backend Developer Role",
        "body": (
            "Hi, thank you for applying to the Backend Developer role at Nimbus Labs. "
            "Your technical interview is scheduled for September 25, 2026 at 4:00 PM IST "
            "on Google Meet. The meeting link will be shared an hour before."
        ),
        "sender": "recruiting@nimbuslabs.io",
    },
    {
        "subject": "Online Assessment - Submission Deadline",
        "body": (
            "Please submit your assessment by September 28, 2026. "
            "You can upload your solution through the candidate portal."
        ),
        "sender": "hiring@nimbuslabs.io",
    },
]


if __name__ == "__main__":
    print(f"Model: {GROQ_MODEL}\n")
    for i, email in enumerate(SAMPLES, start=1):
        print(f"=== Sample {i}: {email['subject']}")
        try:
            result = classify_email(**email)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except ClassificationError as e:
            print(f"ClassificationError: {e}")
            print(f"Raw output: {e.raw_output!r}")
        print()
