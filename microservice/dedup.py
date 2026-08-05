import os
import hashlib
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
engine = create_engine(DATABASE_URL)


def compute_fingerprint(subject: str, sender: str, body: str) -> str:
    """
    Creates a stable hash from email content, used when no real
    Gmail message ID is available (e.g. manual testing).
    """
    raw = f"{subject}|{sender}|{body}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_duplicate(email_id: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT 1 FROM inboxpilot_v1_dedup WHERE email_id = :email_id"),
            {"email_id": email_id}
        )
        return result.fetchone() is not None


def mark_processed(email_id: str):
    with engine.connect() as conn:
        conn.execute(
            text("INSERT INTO inboxpilot_v1_dedup (email_id) VALUES (:email_id) ON CONFLICT (email_id) DO NOTHING"),
            {"email_id": email_id}
        )
        conn.commit()